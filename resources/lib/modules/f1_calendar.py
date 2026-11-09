"""Corroborate sponsor-free race names against a public F1 calendar."""
import json
import re
import unicodedata
from collections import Counter
from datetime import date, datetime
from http.client import HTTPException
from urllib.request import Request, urlopen

_URL = 'https://api.jolpi.ca/ergast/f1/%d.json?limit=100'
_LIMIT = 1024 * 1024
_GENERIC_ALIASES = frozenset(('grand prix', 'circuit', 'street circuit', 'international circuit', 'autodrome', 'autodromo', 'raceway', 'speedway', 'formula one', 'formula 1'))


def _normal(value):
	if not isinstance(value, str) or len(value) > 300: return ''
	value = unicodedata.normalize('NFKD', value.casefold())
	return ' '.join(re.sub(r'[^a-z0-9]+', ' ', ''.join(c for c in value if not unicodedata.combining(c))).split())


def _calendar_aliases(entry):
	"""Return bounded, normalized race and place names from a calendar row."""
	circuit = entry.get('Circuit') if isinstance(entry, dict) else None
	if not isinstance(circuit, dict): return set()
	location = circuit.get('Location') if isinstance(circuit.get('Location'), dict) else {}
	values = (circuit.get('circuitId'), location.get('locality'), location.get('country'), circuit.get('circuitName'), entry.get('raceName'))
	aliases = [_normal(value) for value in values]
	return list(dict.fromkeys(alias for alias in aliases if 4 <= len(alias) <= 100 and alias not in _GENERIC_ALIASES and re.search(r'[a-z]', alias)))


def _alias_key(alias):
	return re.sub(r'\s+(?:grand prix|gp)$', '', alias)


def aliases_by_event(fixtures):
	"""Return corroborated event IDs to race names and unique round numbers.

	The race date must be within one day and the circuit or city must match an
	ESPN alias exactly. Country names alone do not distinguish nearby races.
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
		with urlopen(Request(_URL % year, headers={'User-Agent': 'BINGIE Live TV', 'Accept': 'application/json'}), timeout=4) as response:
			body = response.read(_LIMIT + 1)
		if len(body) > _LIMIT: return {}
		data = json.loads(body)
		calendar = data['MRData']['RaceTable']['Races']
		if not isinstance(calendar, list) or len(calendar) > 100: return {}
		def round_number(entry):
			value = entry.get('round') if isinstance(entry, dict) and str(entry.get('season')) == str(year) else None
			if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 2: return None
			number = int(value)
			return number if 1 <= number <= 99 else None
		rounds = Counter(round_number(entry) for entry in calendar)
		# A venue or country can host more than one event. Count aliases over
		# the whole calendar, not just events ESPN happened to return.
		alias_owners = Counter(key for entry in calendar if isinstance(entry, dict) and str(entry.get('season')) == str(year) for key in {_alias_key(alias) for alias in _calendar_aliases(entry)})
		candidates = []
		for entry in calendar:
			if not isinstance(entry, dict) or str(entry.get('season')) != str(year): continue
			name, circuit = entry.get('raceName'), entry.get('Circuit')
			if not isinstance(name, str) or not name.strip() or len(name) > 200 or not isinstance(circuit, dict): continue
			location = circuit.get('Location') if isinstance(circuit.get('Location'), dict) else {}
			places = {_normal(circuit.get('circuitName')), _normal(location.get('locality'))} - {'', _normal(location.get('country'))}
			places = {place for place in places if len(place) >= 4}
			if not places: continue
			try: day = date.fromisoformat(entry['date'])
			except (KeyError, ValueError, TypeError): continue
			number = round_number(entry)
			aliases = [alias for alias in _calendar_aliases(entry) if alias_owners[_alias_key(alias)] == 1]
			candidates.append((name.strip(), day, places, number if rounds[number] == 1 else None, aliases))
		matches = {}
		for row in races:
			aliases = row.get('aliases') if isinstance(row.get('aliases'), list) else []
			places = {_normal(value) for value in aliases} - {''}
			day = datetime.fromisoformat(row['date'].replace('Z', '+00:00')).date()
			matches[row['event_id']] = [index for index, (_, race_day, locations, _, _) in enumerate(candidates) if abs((day - race_day).days) <= 1 and places.intersection(locations)]
		used = Counter(index for indexes in matches.values() for index in indexes)
		return {identity: {'name': candidates[indexes[0]][0], 'round': candidates[indexes[0]][3], 'aliases': candidates[indexes[0]][4]} for identity, indexes in matches.items() if len(indexes) == 1 and used[indexes[0]] == 1}
	except (OSError, HTTPException, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError): return {}
