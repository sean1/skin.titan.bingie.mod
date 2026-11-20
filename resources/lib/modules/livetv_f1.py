"""Fetch Formula 1 sessions and match explicitly labelled provider feeds."""
import json
import math
import os
import re
import tempfile
import threading
import time
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlsplit
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
_SESSIONS = {'FP1': 'Practice 1', 'FP2': 'Practice 2', 'FP3': 'Practice 3', 'FP4': 'Practice 4', 'SS': 'Sprint Qualifying', 'SR': 'Sprint Race', 'QUAL': 'Qualifying', 'Q1': 'Qualifying 1', 'Q2': 'Qualifying 2', 'PQ': 'Pre-Qualifying', 'WU': 'Warm-up', 'RACE': 'Race'}
_RESULTS_ARCHIVE_CODES = ('FP1', 'FP2', 'FP3', 'FP4', 'Q1', 'Q2', 'QUAL', 'WU')
_CANCELLED_PRACTICES = {2015: ('18755', '401022087', '2015-10-23T19:00Z'), 2017: ('18803', '401022305', '2017-04-07T06:00Z')}
_RESCHEDULED_QUALIFYING = {
	2010: ('3905', '400463706', '2010-10-09T06:00Z', '2010-10-10T01:00Z'),
	2015: ('18755', '401022085', '2015-10-25T19:00Z', '2015-10-25T14:00Z'),
}
_INDY_TIME_TRIALS = {
	1955: ('3113', '400462793', '1955-05-30', ((1, '1955-05-14'), (2, '1955-05-15'), (3, '1955-05-21'), (4, '1955-05-22')), 4),
	1956: ('3120', '400462800', '1956-05-30', ((1, '1956-05-19'), (2, '1956-05-20'), (4, '1956-05-27')), None),
	1960: ('3156', '400462836', '1960-05-30', ((1, '1960-05-14'), (2, '1960-05-15'), (3, '1960-05-21'), (4, '1960-05-22')), 4),
}
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


def get_schedule(cache_dir=None, with_expiry=False):
	"""Return sessions from the current or next race weekend."""
	global _CACHE
	entry = _CACHE
	if entry and time.monotonic() - entry[0] < 30:
		fixtures = list(entry[1])
		return (fixtures, time.time() + max(0, 30 - (time.monotonic() - entry[0]))) if with_expiry else fixtures
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
			entry = (time.monotonic(), fixtures)
			_CACHE = entry
			return (list(fixtures), time.time() + max(0, 30 - (time.monotonic() - entry[0]))) if with_expiry else list(fixtures)
	entry = (time.monotonic(), [])
	_CACHE = entry
	return ([], time.time() + max(0, 30 - (time.monotonic() - entry[0]))) if with_expiry else []


def get_show_schedule(year):
	"""Return scheduled, live and finished sessions for a Formula 1 season."""
	if isinstance(year, bool) or not isinstance(year, int) or not 1950 <= year <= datetime.now(timezone.utc).year: raise ValueError('Invalid Formula 1 season')
	url = 'https://site.api.espn.com/apis/site/v2/sports/racing/f1/scoreboard?dates=%s&limit=1000' % year
	with urlopen(Request(url, headers={'User-Agent': 'BINGIE Live TV'}), timeout=10) as response:
		body = response.read(8 * 1024 * 1024 + 1)
	if len(body) > 8 * 1024 * 1024: raise ValueError('Scoreboard response too large')
	data = json.loads(body)
	events = data.get('events') if isinstance(data, dict) else None
	if not isinstance(events, list): raise ValueError('Invalid Formula 1 scoreboard response')
	now = datetime.now(timezone.utc)
	if year <= 2009 and year < now.year:
		known_sessions = set()
		fixtures = _weekends(events, now, known_sessions=known_sessions)
		fixtures += _archive_sessions(fixtures, known_sessions, year)
		fixtures += _classified_archive_sessions(fixtures, known_sessions, year)
		if year in _INDY_TIME_TRIALS: _indy_time_trials(fixtures, year)
		if year == 2004: _suzuka_2004_schedule(fixtures)
		if year <= 2005:
			codes_by_event = {}
			for event_id, code in sorted(known_sessions):
				codes_by_event.setdefault(event_id, []).append(code)
			for race in fixtures:
				if race['session_code'] == 'RACE' and race['status'] == 'Finished':
					race['archive_known_session_codes'] = codes_by_event.get(race['event_id'], [])
		return fixtures
	fixtures = _weekends(events, now)
	if year in _CANCELLED_PRACTICES:
		fixtures = [row for row in fixtures if row['session_code'] != 'FP2' or (row['event_id'], row['id'], row['date']) != _CANCELLED_PRACTICES[year]]
	if year in _RESCHEDULED_QUALIFYING:
		event_id, session_id, previous, corrected = _RESCHEDULED_QUALIFYING[year]
		for row in fixtures:
			if (row['event_id'], row['id'], row['session_code'], row['status']) == (event_id, session_id, 'QUAL', 'Finished') and row['date'] in (previous, corrected):
				row.update(date=corrected, qualifying_weekday=6)
	if year in (2020, 2021) and year < now.year: _historical_event_names(fixtures, year)
	if year == 2023 and year < now.year: _qatar_2023_schedule(fixtures)
	return fixtures


def _indy_time_trials(fixtures, year):
	event_id, race_id, race_day, trials, bump_day = _INDY_TIME_TRIALS[year]
	races = [row for row in fixtures if row['event_id'] == event_id and row['session_code'] == 'RACE']
	if len(races) != 1 or (races[0]['id'], races[0]['date'][:10], races[0]['status']) != (race_id, race_day, 'Finished'): return
	race = races[0]
	race.update(date_only=True, calendar_day=race_day)
	fixtures[:] = [row for row in fixtures if row['event_id'] != event_id or row['session_code'] not in ('Q1', 'Q2') or not row['id'].startswith('jolpica-archive-')]
	for index, day in trials:
		label = 'Pole Day Time Trials' if index == 1 else 'Time Trials Day %d' % index
		fixtures.append({**race, 'id': 'f1-indy-trials-' + day, 'date': day + 'T00:00Z', 'calendar_day': day, 'session_code': 'TT', 'session_label': label,
			'aliases': [*race['aliases'], 'Indy 500'], 'archive_schedule': True, 'indy_trial_day': index, 'indy_last_trial_day': bump_day})


def _suzuka_2004_schedule(fixtures):
	weekend = [row for row in fixtures if row['event_id'] == '3799' and row['status'] == 'Finished']
	if [(row['id'], row['date']) for row in weekend if row['session_code'] == 'RACE'] != [('400463479', '2004-10-10T04:00Z')]: return
	dates = {'FP1': '2004-10-08T00:00Z', 'FP2': '2004-10-08T00:00Z', 'Q1': '2004-10-10T00:00Z', 'Q2': '2004-10-10T01:00Z', 'RACE': '2004-10-10T04:00Z'}
	for row in weekend:
		if when := dates.get(row['session_code']):
			row.update(date=when, calendar_day=when[:10], date_only=True)
			row.pop('archive_undated', None)


def _historical_event_names(fixtures, year):
	"""Restore the official names of distinct races held at the same circuit."""
	events = {
		2020: (('401220848', '401222744', '2020-07-12', 'Styrian Grand Prix'), ('401220871', '401222777', '2020-08-09', '70th Anniversary Grand Prix')),
		2021: (('600006840', '401275111', '2021-06-27', 'Styrian Grand Prix'), ('600001764', '401275121', '2021-07-04', 'Austrian Grand Prix')),
	}
	for event_id, race_id, day, name in events[year]:
		weekend = [row for row in fixtures if row['event_id'] == event_id]
		races = [row for row in weekend if row['session_code'] == 'RACE']
		if len(races) != 1 or (races[0]['id'], races[0]['date'][:10], races[0]['status']) != (race_id, day, 'Finished'): continue
		for row in weekend:
			old_names = {row['event'], *row['event_names']}
			row['aliases'] = [name, *(alias for alias in row['aliases'] if alias not in old_names)]
			row['event'] = name
			row['event_names'] = [name]


def _qatar_2023_schedule(fixtures):
	"""Apply the Qatar track familiarisation and shootout changes from FIA Decision 30."""
	weekend = [row for row in fixtures if row['event_id'] == '600026765']
	races = [row for row in weekend if row['session_code'] == 'RACE']
	shootouts = [row for row in weekend if row['session_code'] == 'SS']
	if len(races) != 1 or len(shootouts) != 1: return
	race, shootout = races[0], shootouts[0]
	if (race['id'], race['date'], race['status']) != ('401505969', '2023-10-08T17:00Z', 'Finished'): return
	if shootout['id'] != '401511341' or shootout['status'] != 'Finished' or shootout['date'] not in ('2023-10-07T13:00Z', '2023-10-07T13:20Z'): return
	shootout['date'] = '2023-10-07T13:20Z'
	identity = 'fia-decision-2023-qatar-30-fam'
	if any(row['id'] == identity for row in fixtures) or any(row['session_code'] == 'FAM' for row in weekend): return
	fixtures.append({**race, 'id': identity, 'date': '2023-10-07T13:00Z', 'session_code': 'FAM', 'session_label': 'Track Familiarisation', 'archive_schedule': True})


def _archive_sessions(fixtures, known_sessions, year):
	from modules import f1_calendar
	verified = f1_calendar.aliases_by_event(fixtures, include_archive_sessions=True)
	existing_ids = {row['id'] for row in fixtures}
	added = []
	for race in fixtures:
		if race['session_code'] != 'RACE' or race['status'] != 'Finished': continue
		event_id = race['event_id']
		details = verified.get(event_id, {})
		round_number = details.get('round')
		if round_number is None: continue
		for code, session in details['sessions'].items():
			identity = 'jolpica-archive-%d-%d-%s' % (year, round_number, code.lower())
			if (event_id, code) in known_sessions or identity in existing_ids: continue
			if code in ('Q1', 'Q2') and (event_id, 'QUAL') in known_sessions: continue
			if code == 'QUAL' and any((event_id, half) in known_sessions for half in ('Q1', 'Q2')): continue
			added.append({**race, **session, 'id': identity, 'session_code': code, 'session_label': _SESSIONS[code], 'archive_schedule': True})
			existing_ids.add(identity)
	return added


def _archive_session_label(code, year):
	return 'Qualifying Warm-up' if code == 'WU' and year == 2003 else _SESSIONS[code]


def _classified_archive_sessions(fixtures, known_sessions, year):
	"""Supplement the schedule with timed historical classifications."""
	try:
		with open(os.path.join(os.path.dirname(__file__), 'f1_archive_sessions.json'), encoding='utf-8') as source:
			inventory = json.load(source).get(str(year), {})
	except (OSError, ValueError):
		return []
	races = [row for row in fixtures if row['session_code'] == 'RACE']
	counts = Counter(row['event_id'] for row in races)
	identities = {row['id'] for row in fixtures}
	known = known_sessions | {(row['event_id'], row['session_code']) for row in fixtures}
	added = []
	for race in races:
		event_id = race['event_id']
		if counts[event_id] != 1 or race['status'] != 'Finished' or event_id not in inventory: continue
		race_day, round_number, source_id, sessions = inventory[event_id]
		if race['date'][:10] != race_day: continue
		practice_slots_2002 = {'FP1': 'opening-first', 'FP2': 'opening-second', 'FP3': 'saturday-first', 'FP4': 'saturday-second'} if year == 2002 else {}
		if practice_slots_2002:
			fixtures[:] = [row for row in fixtures if row['event_id'] != event_id or row['session_code'] not in practice_slots_2002]
			known.difference_update((event_id, code) for code in practice_slots_2002)
		qualifying_practice = year <= 1975 and sessions.get('Q1') and sessions.get('Q2') and (event_id, 'QUAL') not in known
		if (qualifying_practice or sessions.get('WU') == race_day) and race['date'] in (race_day + 'T04:00Z', race_day + 'T05:00Z'):
			race.update(date_only=True, calendar_day=race_day)
		for code, day in sessions.items():
			identity = 'f1db-archive-%d-%s' % (source_id, code.lower())
			if qualifying_practice and code in ('Q1', 'Q2', 'Q3'):
				day, period = day if isinstance(day, list) else (day, '')
				when = datetime.fromisoformat(day)
				label = ' '.join(part for part in (when.strftime('%A'), period.title(), 'Qualifying Practice') if part)
				fields = dict(date=day + 'T00:00Z', calendar_day=day, date_only=True, qualifying_practice=3 if 'Q3' in sessions else 2, qualifying_period=period,
					qualifying_weekday=when.weekday(), session_label=label, race_round=round_number)
				if (event_id, code) in known:
					previous = [row for row in fixtures if row['event_id'] == event_id and row['session_code'] == code and row.get('archive_schedule') and row['status'] == 'Finished']
					if len(previous) == 1: previous[0].update(fields)
				elif identity not in identities:
					added.append({**race, **fields, 'id': identity, 'session_code': code, 'archive_schedule': True})
					identities.add(identity)
				continue
			if code == 'Q3' or isinstance(day, list): continue
			if code in practice_slots_2002: identity = 'f1-2002-%d-%s' % (source_id, practice_slots_2002[code])
			if year == 2002 and day is not None and code in ('QUAL', 'WU') and (event_id, code) in known:
				previous = [row for row in fixtures if row['event_id'] == event_id and row['session_code'] == code and row['status'] == 'Finished']
				if len(previous) == 1:
					previous[0].update(date=day + 'T00:00Z', date_only=True, calendar_day=day, race_round=round_number)
					if code == 'QUAL': previous[0]['qualifying_weekday'] = datetime.fromisoformat(day).weekday()
			practice_2003 = year == 2003 and day is not None and code in ('FP1', 'FP2', 'FP3')
			if practice_2003 and round_number <= 8 and code == 'FP2': identity = 'f1-2003-%d-saturday-first' % source_id
			if (event_id, code) in known or identity in identities: continue
			if code in ('Q1', 'Q2') and (event_id, 'QUAL') in known: continue
			if code == 'QUAL' and any((event_id, half) in known or half in sessions for half in ('Q1', 'Q2')): continue
			row = {**race, 'id': identity, 'session_code': code, 'session_label': _archive_session_label(code, year), 'archive_schedule': True, 'race_round': round_number}
			if day is None: row['archive_undated'] = True
			else:
				row.update(date=day + 'T00:00Z', date_only=True, calendar_day=day)
				if day[:4] != str(year): row['race_season'] = str(year)
			if code == 'PQ': row['historical_pre_qualifying'] = True
			if code in practice_slots_2002:
				when = datetime.fromisoformat(day)
				ordinal = practice_slots_2002[code].rsplit('-', 1)[1].title()
				row.update(practice_format_2002=True, practice_weekday=when.weekday(), session_label='%s %s Practice' % (when.strftime('%A'), ordinal))
			if year == 2002 and code == 'QUAL' and day is not None: row['qualifying_weekday'] = datetime.fromisoformat(day).weekday()
			if practice_2003:
				row['practice_format_2003'] = 'combined' if round_number <= 8 else 'split'
				opening = 'Thursday Practice' if datetime.fromisoformat(day).weekday() == 3 else 'Friday Practice'
				row['session_label'] = {'FP1': opening, 'FP2': 'Saturday First Practice', 'FP3': 'Saturday Second Practice'}[code]
			added.append(row)
			identities.add(identity)
	return added


def get_archive_event_sessions(year, fixtures, event_id):
	"""Return additional verified sessions, or None when official results are unavailable."""
	if isinstance(year, bool) or not isinstance(year, int) or not 1950 <= year <= 2005 or not event_id: return []
	event = [row for row in fixtures if row.get('event_id') == event_id]
	races = [row for row in event if row.get('session_code') == 'RACE']
	if len(races) != 1 or races[0].get('status') != 'Finished': return []
	race = races[0]
	try:
		when = datetime.fromisoformat(race['date'].replace('Z', '+00:00'))
		if when.utcoffset() is None or when.year != year or when > datetime.now(timezone.utc): return []
	except (KeyError, AttributeError, ValueError, OverflowError):
		return []

	from modules import f1_results_archive
	links = f1_results_archive.race_sessions(year, race)
	if links is None: return None
	known_codes = set(race.get('archive_known_session_codes', ())) | {row.get('session_code') for row in event}
	existing_ids = {row.get('id') for row in fixtures}
	official_ids = set()
	added = []
	for code in _RESULTS_ARCHIVE_CODES:
		if code not in links: continue
		link = links[code]
		match = re.match(r'^/en/results/%d/races/([0-9]+)/[^/]+/' % year, urlsplit(link).path)
		if not match: return None
		official_id = match.group(1)
		official_ids.add(official_id)
		identity = 'f1-results-%d-%s-%s' % (year, official_id, code.lower())
		if code in known_codes or identity in existing_ids: continue
		if code in ('Q1', 'Q2', 'QUAL') and 'TT' in known_codes: continue
		if code in ('Q1', 'Q2') and 'QUAL' in known_codes: continue
		if code == 'QUAL' and any(half in known_codes or half in links for half in ('Q1', 'Q2')): continue
		added.append({**race, 'id': identity, 'session_code': code, 'session_label': _archive_session_label(code, year), 'archive_schedule': True, 'archive_undated': True, 'archive_result_url': link})
	return None if len(official_ids) > 1 else added


def get_season_schedule(year):
	"""Return finished sessions for a Formula 1 season."""
	now = datetime.now(timezone.utc)
	return [fixture for fixture in get_show_schedule(year) if fixture['status'] == 'Finished' and datetime.fromisoformat(fixture['date'].replace('Z', '+00:00')) <= now]


def _weekends(events, now, *, known_sessions=None):
	fixtures = []
	for event in events:
		if not isinstance(event, dict): continue
		name, competitions = event.get('name'), event.get('competitions')
		if not isinstance(name, str) or not name.strip() or not isinstance(competitions, list): continue
		event_id = str(event.get('id') or name.strip())
		circuit = event.get('circuit') if isinstance(event.get('circuit'), dict) else {}
		address = circuit.get('address') if isinstance(circuit.get('address'), dict) else {}
		event_names = [value.strip() for value in (name, event.get('shortName')) if isinstance(value, str) and len(value.strip()) >= 4]
		aliases = event_names + [value.strip() for value in (circuit.get('fullName'), address.get('city'), address.get('country')) if isinstance(value, str) and len(value.strip()) >= 4]
		identities = Counter(str(session.get('id') or '') for session in competitions if isinstance(session, dict) and session.get('id'))
		for session in competitions:
			if not isinstance(session, dict): continue
			identity, date = str(session.get('id') or ''), session.get('date')
			kind, status = session.get('type'), session.get('status')
			if not isinstance(kind, dict): continue
			code = str(kind.get('abbreviation') or '').upper()
			if code == 'SPRINT': code = 'SR'
			if code not in _SESSIONS: continue
			if known_sessions is not None: known_sessions.add((event_id, code))
			if not identity or identities[identity] != 1 or not isinstance(date, str) or not isinstance(status, dict): continue
			status = status.get('type')
			if not isinstance(status, dict) or status.get('name') in ('STATUS_POSTPONED', 'STATUS_CANCELED', 'STATUS_CANCELLED'): continue
			state = status.get('state')
			if state not in ('in', 'pre', 'post'): continue
			try:
				when = datetime.fromisoformat(date.replace('Z', '+00:00'))
				if when.utcoffset() is None: continue
			except (ValueError, TypeError, OverflowError): continue
			if state == 'pre' and when + timedelta(hours=6) <= now: continue
			fixtures.append({
				'id': identity, 'event_id': event_id, 'event': name.strip(), 'event_names': event_names, 'aliases': aliases,
				'session_code': code, 'session_label': _SESSIONS[code], 'date': date, 'status': {'in': 'LIVE', 'pre': 'Scheduled', 'post': 'Finished'}[state]
			})
	return fixtures


def fixture_groups(streams, fixtures, live_only=False):
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
			if live_only and fixture.get('status') != 'LIVE': continue
			if live_only and any(isinstance(other, dict) and other is not fixture and other.get('status') == 'Scheduled' and other.get('session_code') == fixture.get('session_code') and other.get('event_id', other.get('event')) == fixture.get('event_id', fixture.get('event')) and _date_agrees(name, other.get('date', '')) for other in fixtures): continue
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
