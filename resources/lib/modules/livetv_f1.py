"""Fetch Formula 1 sessions and match explicitly labelled provider feeds."""
import json
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

_CACHE = None
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
_REJECT = re.compile(r'\b(?:replay|repeat|delayed|ended|end|highlights|archive|on[ -]demand)\b', re.I)
_LABEL_DATE = re.compile(r'\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*(?:\s+(\d{4}))?\b', re.I)
_MONTH_FIRST = re.compile(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})(?:,?\s+(\d{4}))?\b', re.I)
_NUMERIC_DATE = re.compile(r'\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b')
_ZONES = {'UTC': 0, 'GMT': 0, 'EST': -5, 'EDT': -4, 'CST': -6, 'CDT': -5, 'MST': -7, 'MDT': -6, 'PST': -8, 'PDT': -7}


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
		if not identity.isdecimal() or not isinstance(name, str) or _REJECT.search(name): continue
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
