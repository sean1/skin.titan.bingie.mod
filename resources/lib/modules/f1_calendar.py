"""Corroborate sponsor-free race names against a public F1 calendar."""
import json
import re
import unicodedata
from collections import Counter
from datetime import date, datetime
from http.client import HTTPException
from urllib.request import Request, urlopen

_URL = 'https://api.jolpi.ca/ergast/f1/%d.json?limit=100'
_ALPHA_SCHEDULE_URL = 'https://api.jolpi.ca/f1/alpha/schedules/%d/'
_LIMIT = 1024 * 1024
_GENERIC_ALIASES = frozenset(('grand prix', 'circuit', 'street circuit', 'international circuit', 'autodrome', 'autodromo', 'raceway', 'speedway', 'formula one', 'formula 1'))
_ARCHIVE_SESSIONS = {'FP1': 'FirstPractice', 'FP2': 'SecondPractice', 'FP3': 'ThirdPractice', 'QUAL': 'Qualifying'}
_ALPHA_CODES = {'R': 'RACE', 'Q': 'QUAL', 'Q1': 'Q1', 'Q2': 'Q2'}


def _normal(value):
	if not isinstance(value, str) or len(value) > 300: return ''
	value = unicodedata.normalize('NFKD', value.casefold())
	return ' '.join(re.sub(r'[^a-z0-9]+', ' ', ''.join(c for c in value if not unicodedata.combining(c))).split())


def circuit_locality(value):
	"""Extract a locality from a circuit name that explicitly identifies it."""
	match = re.fullmatch(r'(.{4,}?)\s+(?:street circuit|international autodrome)', _normal(value))
	return match.group(1) if match else ''


def _calendar_aliases(entry):
	"""Return bounded, normalized race and place names from a calendar row."""
	circuit = entry.get('Circuit') if isinstance(entry, dict) else None
	if not isinstance(circuit, dict): return []
	location = circuit.get('Location') if isinstance(circuit.get('Location'), dict) else {}
	values = (circuit.get('circuitId'), location.get('locality'), location.get('country'), circuit.get('circuitName'), entry.get('raceName'))
	aliases = [_normal(value) for value in values]
	result = [alias for alias in aliases if 4 <= len(alias) <= 100 and alias not in _GENERIC_ALIASES and re.search(r'[a-z]', alias)]
	circuit_name = _normal(circuit.get('circuitName'))
	if circuit.get('circuitId') == 'spa': result.append('spa')
	if circuit_name == 'circuit de spa francorchamps': result.append('spa francorchamps')
	if circuit_name == 'circuit of the americas': result.append('cota')
	return list(dict.fromkeys(result))


def _alias_key(alias):
	return re.sub(r'\s+(?:grand prix|gp)$', '', alias)


def _race_name(value):
	return re.sub(r'\bgp\b', 'grand prix', _normal(value))


def _name_matches(name, row_names):
	"""Allow the full official Grand Prix name after a sponsor prefix."""
	if not name or name in _GENERIC_ALIASES: return False
	return any(other == name or (name.endswith(' grand prix') and other.endswith(' ' + name)) for other in row_names)


def _load_calendar(url):
	with urlopen(Request(url, headers={'User-Agent': 'BINGIE Live TV', 'Accept': 'application/json'}), timeout=4) as response:
		body = response.read(_LIMIT + 1)
	if len(body) > _LIMIT: raise ValueError('F1 calendar too large')
	return json.loads(body)


def _scheduled_session(day, clock):
	calendar_day = day.isoformat()
	if clock is None: return {'date': calendar_day + 'T00:00Z', 'date_only': True, 'calendar_day': calendar_day}
	if not isinstance(clock, str): return None
	try: when = datetime.fromisoformat(calendar_day + 'T' + clock.replace('Z', '+00:00'))
	except (ValueError, OverflowError): return None
	return {'date': when.isoformat()} if when.utcoffset() is not None else None


def _archive_sessions(entry, race_day):
	sessions = {}
	for code, field in _ARCHIVE_SESSIONS.items():
		session = entry.get(field)
		if not isinstance(session, dict): continue
		try: day = date.fromisoformat(session.get('date'))
		except (TypeError, ValueError): continue
		if not 0 <= (race_day - day).days <= 4: continue
		scheduled = _scheduled_session(day, session.get('time'))
		if scheduled: sessions[code] = scheduled
	return sessions


def _alpha_sessions(rows):
	"""Read the race date and the distinct qualifying sessions for one historical round."""
	if not isinstance(rows, list) or len(rows) > 24: return None
	rows = [row for row in rows if isinstance(row, dict) and isinstance(row.get('code'), str) and row['code'] in _ALPHA_CODES]
	counts = Counter(row['code'] for row in rows)
	scheduled = {}
	for row in rows:
		code, stamp, date_only = row['code'], row.get('timestamp'), row.get('missing_time_data', False)
		if counts[code] != 1 or not isinstance(stamp, str) or not isinstance(date_only, bool): continue
		parts = row.get('sessions')
		if not isinstance(parts, list) or not parts or not all(isinstance(part, dict) and part.get('is_cancelled') is False for part in parts): continue
		day_text, separator, clock = stamp.partition('T')
		if not separator: continue
		try: day = date.fromisoformat(day_text)
		except ValueError: continue
		session = _scheduled_session(day, None if date_only else clock)
		if session: scheduled[_ALPHA_CODES[code]] = (day, session)
	race = scheduled.pop('RACE', None)
	if not race or ('QUAL' in scheduled and ('Q1' in scheduled or 'Q2' in scheduled)): return None
	race_day = race[0]
	return race_day, {code: session for code, (day, session) in scheduled.items() if 0 <= (race_day - day).days <= 4}


def _alpha_schedule(year):
	try:
		data = _load_calendar(_ALPHA_SCHEDULE_URL % year)['data']
		events = data['events']
		if data.get('year') != year or not isinstance(events, list) or len(events) > 100: return {}
		rounds = {}
		seen = set()
		for event in events:
			round_info = event.get('round') if isinstance(event, dict) else None
			if not isinstance(round_info, dict): continue
			number = round_info.get('number')
			if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 99: continue
			if number in seen:
				rounds.pop(number, None)
				continue
			seen.add(number)
			name = _race_name(round_info.get('name'))
			if not name or round_info.get('is_cancelled') is not False: continue
			sessions = _alpha_sessions(event.get('schedule'))
			if sessions: rounds[number] = (name, *sessions)
		return rounds
	except (OSError, HTTPException, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError): return {}


def _alternate_rounds_2023(year, calendar, rounds):
	"""Map the published and held-race numbering after Imola's cancellation."""
	if year != 2023: return {}
	monaco = [entry.get('round') for entry in calendar if isinstance(entry, dict) and _race_name(entry.get('raceName')) == 'monaco grand prix' and entry.get('date') == '2023-05-28']
	numbers = set(rounds)
	if monaco == ['6'] and len(calendar) == 22 and numbers == set(range(1, 23)):
		return {number: number + 1 for number in range(6, 23)}
	if monaco != ['7']: return {}
	published = set(range(1, 24))
	if len(calendar) == 22 and numbers == published - {6}:
		return {number: number - 1 for number in range(7, 24)}
	if len(calendar) == 23 and numbers == published and any(
		entry.get('round') == '6' and _race_name(entry.get('raceName')) == 'emilia romagna grand prix' and entry.get('date') == '2023-05-21' for entry in calendar):
		return {number: number - 1 for number in range(7, 24)}
	return {}


def aliases_by_event(fixtures, *, include_archive_sessions=False):
	"""Return corroborated event IDs to race names and unique round numbers.

	The race date must be within one day. Use the venue when it matches and
	the official event name when ESPN supplies an incompatible venue.
	"""
	if not isinstance(fixtures, list) or len(fixtures) > 500: return {}
	races = [row for row in fixtures if isinstance(row, dict) and row.get('session_code') == 'RACE' and isinstance(row.get('event_id'), str) and row['event_id']]
	counts = Counter(row['event_id'] for row in races)
	races = [row for row in races if counts[row['event_id']] == 1]
	try:
		years = {datetime.fromisoformat(row['date'].replace('Z', '+00:00')).year for row in races}
		if len(years) != 1: return {}
		year = years.pop()
		if not 1950 <= year <= 2100: return {}
		data = _load_calendar(_URL % year)
		calendar = data['MRData']['RaceTable']['Races']
		if not isinstance(calendar, list) or len(calendar) > 100: return {}
		def round_number(entry):
			value = entry.get('round') if isinstance(entry, dict) and str(entry.get('season')) == str(year) else None
			if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 2: return None
			number = int(value)
			return number if 1 <= number <= 99 else None
		rounds = Counter(round_number(entry) for entry in calendar)
		alternate_rounds = _alternate_rounds_2023(year, calendar, rounds)
		# A venue or country can host more than one event. Count aliases over
		# the whole calendar, not just events ESPN happened to return.
		alias_owners = Counter(key for entry in calendar if isinstance(entry, dict) and str(entry.get('season')) == str(year) for key in {_alias_key(alias) for alias in _calendar_aliases(entry)})
		candidates = []
		for entry in calendar:
			if not isinstance(entry, dict) or str(entry.get('season')) != str(year): continue
			name, circuit = entry.get('raceName'), entry.get('Circuit')
			if not isinstance(name, str) or not name.strip() or len(name) > 200 or not isinstance(circuit, dict): continue
			location = circuit.get('Location') if isinstance(circuit.get('Location'), dict) else {}
			city = _normal(location.get('locality'))
			places = {_normal(circuit.get('circuitName')), city} - {'', _normal(location.get('country'))}
			places = {place for place in places if len(place) >= 4}
			if not places: continue
			try: day = date.fromisoformat(entry['date'])
			except (KeyError, ValueError, TypeError): continue
			number = round_number(entry)
			aliases = _calendar_aliases(entry)
			verified = {
				'name': name.strip(), 'round': number if rounds[number] == 1 else None, 'city': city,
				'aliases': [alias for alias in aliases if alias_owners[_alias_key(alias)] == 1],
				'shared_aliases': [alias for alias in aliases if alias_owners[_alias_key(alias)] > 1]
			}
			if include_archive_sessions: verified['sessions'] = _archive_sessions(entry, day)
			candidates.append((day, places, verified))
		matches = {}
		methods = {}
		for row in races:
			aliases = row.get('aliases') if isinstance(row.get('aliases'), list) else []
			places = ({_normal(value) for value in aliases} | {circuit_locality(value) for value in aliases}) - {''}
			day = datetime.fromisoformat(row['date'].replace('Z', '+00:00')).date()
			nearby = [index for index, (race_day, _, _) in enumerate(candidates) if abs((day - race_day).days) <= 1]
			event_id = row['event_id']
			matches[event_id] = [index for index in nearby if places.intersection(candidates[index][1])]
			methods[event_id] = 'venue'
			if matches[event_id]: continue
			row_names = row.get('event_names') if isinstance(row.get('event_names'), list) else []
			row_names = {_race_name(value) for value in [row.get('event'), *row_names]}
			matches[event_id] = [index for index in nearby if _name_matches(_race_name(candidates[index][2]['name']), row_names)]
			methods[event_id] = 'name'
		used = Counter(index for indexes in matches.values() for index in indexes)
		confirmed = {identity: indexes[0] for identity, indexes in matches.items() if len(indexes) == 1 and used[indexes[0]] == 1}
		alpha = _alpha_schedule(year) if confirmed and include_archive_sessions and year < 2006 else {}
		result = {}
		for identity, index in confirmed.items():
			day, _, verified = candidates[index]
			archive = alpha.get(verified['round'])
			if archive and archive[0] == _race_name(verified['name']) and abs((day - archive[1]).days) <= 1:
				verified['sessions'].update(archive[2])
			if verified['round'] in alternate_rounds:
				verified.update({'round_aliases': [alternate_rounds[verified['round']]], 'round_only': []})
			result[identity] = {**verified, 'match': methods[identity]}
		return result
	except (OSError, HTTPException, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError): return {}
