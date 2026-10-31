"""Fetch Formula 1 sessions and match explicitly labelled provider feeds."""
import json
import math
import os
import re
import tempfile
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from modules.livetv_sports import non_live_title

_CACHE = None
_STANDINGS_URL = 'https://site.api.espn.com/apis/v2/sports/racing/f1/standings'
_STANDINGS_LIMIT = 4 * 1024 * 1024
_STANDINGS_STALE_SECONDS = 30 * 24 * 3600
_STANDINGS_RETRY_SECONDS = 15 * 60
_standings_cache = {}
_standings_retry = {}
_standings_rate_until = {}
_standings_lock = threading.Lock()
_SESSIONS = {'FP1': 'Practice 1', 'FP2': 'Practice 2', 'FP3': 'Practice 3', 'SS': 'Sprint Qualifying', 'SR': 'Sprint Race', 'QUAL': 'Qualifying', 'RACE': 'Race'}
_SESSION_PATTERNS = {
	'FP1': re.compile(r'\b(?:fp\s*1|(?:free\s+)?practice\s*1)\b', re.I),
	'FP2': re.compile(r'\b(?:fp\s*2|(?:free\s+)?practice\s*2)\b', re.I),
	'FP3': re.compile(r'\b(?:fp\s*3|(?:free\s+)?practice\s*3)\b', re.I),
	'SS': re.compile(r'\b(?:ss|sprint\s+(?:qualifying|shootout))\b', re.I),
	'SR': re.compile(r'\b(?:sr|sprint\s+race)\b', re.I),
	'QUAL': re.compile(r'(?<!sprint )\b(?:qual|qualifying)\b', re.I),
	'RACE': re.compile(r'(?<!sprint )\brace\b', re.I),
}
_REJECT = re.compile(r'\b(?:archive|on[ -]demand)\b', re.I)
_LABEL_DATE = re.compile(r'\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*(?:\s+(\d{4}))?\b', re.I)
_MONTH_FIRST = re.compile(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})(?:,?\s+(\d{4}))?\b', re.I)
_NUMERIC_DATE = re.compile(r'\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b')
_ZONES = {'UTC': 0, 'GMT': 0, 'EST': -5, 'EDT': -4, 'CST': -6, 'CDT': -5, 'MST': -7, 'MDT': -6, 'PST': -8, 'PDT': -7}


class _DriverRows(list):
	def __init__(self, rows, constructors=None, stale=False):
		super().__init__(rows)
		self.constructors = list(constructors or [])
		self.stale = stale


def _valid_driver_rows(rows):
	if not isinstance(rows, list) or not 1 <= len(rows) <= 100: return False
	positions, names = set(), set()
	for row in rows:
		if not isinstance(row, dict): return False
		position, name, points = row.get('position'), row.get('team'), row.get('points')
		if not isinstance(position, int) or isinstance(position, bool) or not 1 <= position <= 100 or position in positions: return False
		if not isinstance(name, str) or not name.strip() or len(name) > 160 or any(ord(c) < 32 for c in name) or name.casefold() in names: return False
		if not isinstance(points, str) or not re.fullmatch(r'\d{1,6}(?:\.\d{1,2})?', points): return False
		positions.add(position)
		names.add(name.casefold())
	return positions == set(range(1, len(rows) + 1))


def _championship_rows(payload, group, entity, metric):
	children = payload.get('children') if isinstance(payload, dict) else None
	if not isinstance(children, list): raise ValueError('Invalid F1 standings')
	groups = [item for item in children if isinstance(item, dict) and str(item.get('abbreviation') or '').casefold() == group]
	if len(groups) != 1: raise ValueError('Missing F1 championship standings')
	standings = groups[0].get('standings')
	entries = standings.get('entries') if isinstance(standings, dict) else None
	if not isinstance(entries, list) or not 1 <= len(entries) <= 100: raise ValueError('Invalid F1 standings entries')
	rows = []
	for entry in entries:
		if not isinstance(entry, dict) or not isinstance(entry.get(entity), dict) or not isinstance(entry.get('stats'), list): raise ValueError('Invalid F1 standings entry')
		name = entry[entity].get('displayName')
		stats = {item.get('name'): item for item in entry['stats'] if isinstance(item, dict) and item.get('name') in ('rank', metric)}
		rank = stats.get('rank', {}).get('value')
		points = stats.get(metric, {}).get('displayValue')
		if not isinstance(rank, (int, float)) or isinstance(rank, bool) or not math.isfinite(rank) or int(rank) != rank: raise ValueError('Invalid F1 rank')
		rows.append({'position': int(rank), 'team': name.strip() if isinstance(name, str) else '', 'points': str(points) if points is not None else ''})
	if not _valid_driver_rows(rows): raise ValueError('Invalid F1 standings rows')
	return sorted(rows, key=lambda row: row['position'])


def _driver_rows(payload):
	return _championship_rows(payload, 'driver', 'athlete', 'championshipPts')


def _constructor_rows(payload):
	return _championship_rows(payload, 'constructor', 'team', 'points')


def _write_standings_cache(path, payload):
	if not path: return
	try:
		directory = os.path.dirname(path)
		os.makedirs(directory, mode=0o700, exist_ok=True)
		fd, temporary = tempfile.mkstemp(prefix='.livetv-f1-standings-', dir=directory)
		try:
			with os.fdopen(fd, 'w', encoding='utf-8') as output: json.dump(payload, output)
			os.replace(temporary, path)
		finally:
			if os.path.exists(temporary): os.unlink(temporary)
	except OSError: pass


def get_driver_standings(cache_dir=None, force_refresh=False):
	"""Get championship standings at most once per local day, reusing stale data on failure."""
	from modules.livetv_standings_generation import get_refresh_time
	cache_dir = os.path.abspath(cache_dir) if cache_dir else ''
	with _standings_lock:
		now = time.time()
		generation = get_refresh_time(cache_dir, 'f1') if cache_dir else 0
		cached = _standings_cache.get(cache_dir)
		path = os.path.join(cache_dir, 'livetv-f1-standings.json') if cache_dir else ''
		backoff_path = os.path.join(cache_dir, 'livetv-f1-standings-backoff.json') if cache_dir else ''
		if cache_dir not in _standings_rate_until:
			try:
				if not backoff_path or not 0 < os.path.getsize(backoff_path) <= 2048: raise ValueError('Invalid backoff')
				with open(backoff_path, encoding='utf-8') as source: backoff = json.load(source)
				retry_at = backoff.get('retry_at') if isinstance(backoff, dict) else None
				if not isinstance(retry_at, (int, float)) or isinstance(retry_at, bool) or not math.isfinite(retry_at) or retry_at > now + 7 * 86400: raise ValueError('Invalid backoff')
				_standings_rate_until[cache_dir] = retry_at
			except (OSError, ValueError, TypeError, RecursionError): _standings_rate_until[cache_dir] = 0
		if cached is None and path:
			try:
				if not 0 < os.path.getsize(path) <= _STANDINGS_LIMIT: raise ValueError('Invalid cache size')
				with open(path, encoding='utf-8') as source: cached = json.load(source)
			except (OSError, ValueError, TypeError, RecursionError): cached = None
		if not isinstance(cached, dict) or not isinstance(cached.get('created'), (int, float)) or isinstance(cached.get('created'), bool) or not 0 <= now - cached['created'] <= _STANDINGS_STALE_SECONDS or not _valid_driver_rows(cached.get('rows')) or ('constructors' in cached and not _valid_driver_rows(cached['constructors'])): cached = None
		if cached:
			_standings_cache[cache_dir] = cached
		if now < _standings_rate_until[cache_dir]: return _DriverRows(cached['rows'], cached.get('constructors'), stale=True) if cached else []
		if not force_refresh and time.monotonic() < _standings_retry.get(cache_dir, 0): return _DriverRows(cached['rows'], cached.get('constructors'), stale=True) if cached else []
		if cached:
			requested = cached.get('requested', cached['created'])
			if not force_refresh and cached.get('constructors') and cache_dir not in _standings_retry and isinstance(requested, (int, float)) and not isinstance(requested, bool) and math.isfinite(requested) and generation <= requested <= now and datetime.fromtimestamp(requested).astimezone().date() == datetime.fromtimestamp(now).astimezone().date(): return _DriverRows(cached['rows'], cached['constructors'])
		try:
			requested = time.time()
			with urlopen(Request(_STANDINGS_URL, headers={'User-Agent': 'BINGIE Live TV', 'Accept': 'application/json'}), timeout=10) as response: body = response.read(_STANDINGS_LIMIT + 1)
			if len(body) > _STANDINGS_LIMIT: raise ValueError('F1 standings too large')
			payload = json.loads(body)
			rows, constructors = _driver_rows(payload), _constructor_rows(payload)
			cached = {'created': time.time(), 'requested': requested, 'rows': rows, 'constructors': constructors}
			_standings_cache[cache_dir] = cached
			_standings_retry.pop(cache_dir, None)
			_write_standings_cache(path, cached)
			return _DriverRows(rows, constructors)
		except (OSError, HTTPException, ValueError, TypeError, OverflowError, RecursionError) as error:
			_standings_retry[cache_dir] = time.monotonic() + _STANDINGS_RETRY_SECONDS
			if isinstance(error, HTTPError) and error.code == 429:
				delay, header = 3600, error.headers.get('Retry-After', '') if error.headers else ''
				try:
					if header.isdecimal(): delay = max(delay, min(int(header), 7 * 86400))
					elif header: delay = max(delay, min(int(parsedate_to_datetime(header).timestamp() - now), 7 * 86400))
				except (TypeError, ValueError, OverflowError): pass
				_standings_rate_until[cache_dir] = now + delay
				_write_standings_cache(backoff_path, {'retry_at': now + delay})
			return _DriverRows(cached['rows'], cached.get('constructors'), stale=True) if cached else []


def _normal(value):
	return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(c)).split())


def _date_agrees(name, value):
	"""Reject dated labels unless their date and time clearly match a session."""
	try:
		when = datetime.fromisoformat(value.replace('Z', '+00:00'))
		if when.utcoffset() is None: return False
		zones = {match.upper() for match in re.findall(r'\b(?:UTC|GMT|EST|EDT|CST|CDT|MST|MDT|PST|PDT)\b', name, re.I)}
		if len(zones) > 1: return False
		clocks = list(re.finditer(r'\b(\d{1,2}):(\d{2})\s*(?:\(([A-Za-z]{2,5})\)|([A-Za-z]{2,5}))?', name))
		if len(clocks) > 1: return False
		clock = clocks[0] if clocks else None
		if clock and (not (clock.group(3) or clock.group(4)) or (clock.group(3) or clock.group(4)).upper() not in _ZONES): return False
		if zones: when = when.astimezone(timezone(timedelta(hours=_ZONES[zones.pop()])))
		dates = []
		for day, month, year in _LABEL_DATE.findall(name): dates.append(datetime.strptime(day + ' ' + month + ' ' + (year or str(when.year)), '%d %b %Y').date())
		for match in _MONTH_FIRST.finditer(name):
			if re.search(r'\b\d{1,2}\s+$', name[:match.start()]): continue
			month, day, year = match.groups()
			dates.append(datetime.strptime(day + ' ' + month + ' ' + (year or str(when.year)), '%d %b %Y').date())
		for match in _NUMERIC_DATE.finditer(name):
			parsed = set()
			for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%d-%m-%Y', '%d/%m/%Y', '%m-%d-%Y', '%m/%d/%Y'):
				try: parsed.add(datetime.strptime(match.group(), fmt).date())
				except ValueError: pass
			if len(parsed) != 1: return False
			dates.extend(parsed)
		if re.search(r'\b\d{1,2}[-/]\d{1,2}\b', _NUMERIC_DATE.sub('', name)): return False
		if any(int(year) != when.year for year in re.findall(r'\b(?:19|20)\d{2}\b', name)): return False
		if clock and not dates: return False
		if any(day != when.date() for day in dates): return False
		if clock:
			label_time = when.replace(hour=int(clock.group(1)), minute=int(clock.group(2)), second=0, microsecond=0)
			if abs((label_time - when).total_seconds()) > 3 * 3600: return False
		return True
	except (ValueError, AttributeError, TypeError, OverflowError): return False


def get_schedule(cache_dir=None):
	"""Return sessions from the current or next race weekend."""
	global _CACHE
	if _CACHE and time.monotonic() - _CACHE[0] < 30: return list(_CACHE[1])
	now = datetime.now(timezone.utc)
	for year in (now.year, now.year + 1):
		url = 'https://site.api.espn.com/apis/site/v2/sports/racing/f1/scoreboard?dates=%s&limit=1000' % year
		with urlopen(Request(url, headers={'User-Agent': 'BINGIE Live TV'}), timeout=10) as response:
			body = response.read(8 * 1024 * 1024 + 1)
		if len(body) > 8 * 1024 * 1024: raise ValueError('Scoreboard response too large')
		data = json.loads(body)
		events = data.get('events') if isinstance(data, dict) else None
		if not isinstance(events, list): raise ValueError('Invalid Formula 1 scoreboard response')
		season = _weekends(events, now)
		weekends = {}
		for fixture in season: weekends.setdefault(fixture['event_id'], []).append(fixture)
		eligible = []
		for sessions in weekends.values():
			pending = [datetime.fromisoformat(item['date'].replace('Z', '+00:00')) for item in sessions if item['status'] in ('LIVE', 'Scheduled')]
			if not pending:
				pending = [now for item in sessions if item['session_code'] == 'RACE' and item['status'] == 'Finished' and datetime.fromisoformat(item['date'].replace('Z', '+00:00')) <= now < datetime.fromisoformat(item['date'].replace('Z', '+00:00')) + timedelta(hours=6)]
			if pending: eligible.append((min(pending), sessions))
		if eligible:
			fixtures = min(eligible, key=lambda item: item[0])[1]
			_CACHE = (time.monotonic(), fixtures)
			return list(fixtures)
	_CACHE = (time.monotonic(), [])
	return []


def _weekends(events, now):
	fixtures = []
	for event in events:
		if not isinstance(event, dict): continue
		name, competitions = event.get('name'), event.get('competitions')
		if not isinstance(name, str) or not name.strip() or not isinstance(competitions, list): continue
		circuit = event.get('circuit') if isinstance(event.get('circuit'), dict) else {}
		address = circuit.get('address') if isinstance(circuit.get('address'), dict) else {}
		aliases = [value.strip() for value in (name, event.get('shortName'), circuit.get('fullName'), address.get('city'), address.get('country')) if isinstance(value, str) and len(value.strip()) >= 4]
		seen = set()
		for session in competitions:
			if not isinstance(session, dict): continue
			identity, date = str(session.get('id') or ''), session.get('date')
			kind, status = session.get('type'), session.get('status')
			if not identity or identity in seen or not isinstance(date, str) or not isinstance(kind, dict) or not isinstance(status, dict): continue
			code, status = str(kind.get('abbreviation') or '').upper(), status.get('type')
			if code not in _SESSIONS or not isinstance(status, dict) or status.get('name') in ('STATUS_POSTPONED', 'STATUS_CANCELED', 'STATUS_CANCELLED'): continue
			state = status.get('state')
			if state not in ('in', 'pre', 'post'): continue
			try:
				when = datetime.fromisoformat(date.replace('Z', '+00:00'))
				if when.utcoffset() is None: continue
			except (ValueError, TypeError, OverflowError): continue
			if state == 'pre' and when + timedelta(hours=6) <= now: continue
			fixtures.append({'id': identity, 'event_id': str(event.get('id') or name.strip()), 'event': name.strip(), 'aliases': aliases, 'session_code': code, 'session_label': _SESSIONS[code], 'date': date, 'status': {'in': 'LIVE', 'pre': 'Scheduled', 'post': 'Finished'}[state]})
			seen.add(identity)
	return fixtures


def fixture_groups(streams, fixtures):
	"""Return session-id to stream-id mappings for unambiguous event feeds."""
	if not isinstance(streams, list) or not isinstance(fixtures, list): return {}
	owners = {}
	for fixture in fixtures:
		if not isinstance(fixture, dict): continue
		for alias in fixture.get('aliases', [fixture.get('event', '')]):
			if isinstance(alias, str) and len(alias.strip()) >= 4: owners.setdefault(_normal(alias), set()).add(fixture.get('event_id', fixture.get('event')))
	result = {}
	for stream in streams:
		if not isinstance(stream, dict): continue
		identity, name = str(stream.get('stream_id') or ''), stream.get('name')
		if not identity.isdecimal() or not isinstance(name, str) or non_live_title(name) or _REJECT.search(name): continue
		codes = [code for code, pattern in _SESSION_PATTERNS.items() if pattern.search(name)]
		if len(codes) != 1: continue
		candidates = []
		for fixture in fixtures:
			if not isinstance(fixture, dict) or fixture.get('session_code') != codes[0]: continue
			event, session_id = fixture.get('event'), fixture.get('id')
			if not isinstance(event, str) or not event.strip() or not isinstance(session_id, str) or not session_id: continue
			if re.search(r'\b(?:next|upcoming)\b', name, re.I) and fixture.get('status') != 'Scheduled': continue
			if re.search(r'\blive\b', name, re.I) and fixture.get('status') != 'LIVE': continue
			if not _date_agrees(name, fixture.get('date', '')): continue
			normal_name = _normal(name)
			aliases = fixture.get('aliases', [event])
			if any(isinstance(alias, str) and len(alias.strip()) >= 4 and owners.get(_normal(alias)) == {fixture.get('event_id', event)} and re.search(r'(?<!\w)' + re.escape(_normal(alias)).replace(r'\ ', r'\s+') + r'(?!\w)', normal_name) for alias in aliases): candidates.append(session_id)
		if len(candidates) == 1: result.setdefault(candidates[0], {}).setdefault(identity, stream)
	return result
