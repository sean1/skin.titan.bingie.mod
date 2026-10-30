"""Bounded read-only standings for NHL, NBA and American football."""
import json
import math
import re
import threading
import time
from urllib.request import Request, urlopen

_URLS = {
	'nhl': 'https://api-web.nhle.com/v1/standings/now',
	'nfl': 'https://site.api.espn.com/apis/v2/sports/football/nfl/standings',
	'nfl_nfc': 'https://site.api.espn.com/apis/v2/sports/football/nfl/standings?group=7',
	'nfl_afc': 'https://site.api.espn.com/apis/v2/sports/football/nfl/standings?group=8',
	'ncaa': 'https://site.api.espn.com/apis/v2/sports/football/college-football/standings',
	'nba': 'https://site.api.espn.com/apis/v2/sports/basketball/nba/standings',
}
_LIMIT = 4 * 1024 * 1024
_CACHE_SECONDS = 300
_cache = {}
_cache_lock = threading.Lock()


def _text(value):
	if isinstance(value, dict): value = value.get('default', '')
	return value.strip() if isinstance(value, str) else ''


def _key(value):
	return ' '.join(re.findall(r'[^\W_]+', _text(value).casefold()))


def _fetch(sport):
	"""Cache each successful league payload in memory for five minutes."""
	with _cache_lock:
		now = time.monotonic()
		cached = _cache.get(sport)
		if cached and 0 <= now - cached[0] < _CACHE_SECONDS: return cached[1]
		request = Request(_URLS[sport], headers={'User-Agent': 'Kodi/21.0', 'Accept': 'application/json'})
		with urlopen(request, timeout=6) as response:
			data = response.read(_LIMIT + 1)
		if len(data) > _LIMIT: raise ValueError('Standings response too large')
		payload = json.loads(data)
		field = 'standings' if sport == 'nhl' else 'children'
		if not isinstance(payload, dict) or not isinstance(payload.get(field), list) or not payload[field]: raise ValueError('Invalid standings response')
		parsed = _nhl(payload) if sport == 'nhl' else _espn(payload)
		if not parsed: raise ValueError('No usable standings entries')
		if sport in ('nfl_afc', 'nfl_nfc'):
			sections = {item['section'] for item in parsed}
			if len(sections) != 4 or any(len([item for item in parsed if item['section'] == section]) != 4 or len({_key(item['team']) for item in parsed if item['section'] == section}) != 4 for section in sections): raise ValueError('Incomplete divisions')
		if sport in ('ncaa', 'nfl', 'nba'):
			counts = _raw_sections(payload)
			if any(not count or sum(item.get('section_path') == path for item in parsed) != count for path, count in counts.items()): return payload
			if sport == 'nba' and (set(counts) != {('Eastern Conference',), ('Western Conference',)} or any(not _nba_order([item for item in parsed if item.get('section_path') == path]) for path in counts)): return payload
		if sport == 'nhl':
			sections = {item['section'] for item in parsed}
			if len(parsed) != len(payload['standings']) or len(sections) != 4 or any(sorted(item['rank'] for item in parsed if item['section'] == section) != list(range(1, 9)) for section in sections): return payload
		_cache[sport] = (time.monotonic(), payload)
		return payload


def _match(entries, name):
	name = _key(name)
	if not name: return None
	matches = [item for item in entries if name in item['aliases']]
	return matches[0] if len(matches) == 1 else None


def _nhl(data):
	entries = []
	for raw in data.get('standings', []):
		if not isinstance(raw, dict): continue
		name, division = _text(raw.get('teamName')), _text(raw.get('divisionName'))
		rank, points = raw.get('divisionSequence'), raw.get('points')
		if not name or not division or not isinstance(rank, int) or isinstance(rank, bool) or rank < 1 or not isinstance(points, int) or isinstance(points, bool): continue
		aliases = {_key(raw.get(field)) for field in ('teamName', 'teamCommonName', 'teamAbbrev')}
		entries.append({'team': name, 'section': division, 'aliases': aliases - {''}, 'rank': rank, 'points': str(points)})
	return entries


def _espn(data, sport=''):
	entries = []
	pending = [(item, ()) for item in data.get('children', [])]
	while pending:
		group, parents = pending.pop(0)
		if not isinstance(group, dict): continue
		section = _text(group.get('name'))
		children = group.get('children', [])
		if isinstance(children, list): pending.extend((child, parents + ((section,) if section else ())) for child in children)
		standings = group.get('standings')
		if not section or not isinstance(standings, dict) or not isinstance(standings.get('entries'), list): continue
		for raw in standings['entries']:
			if not isinstance(raw, dict) or not isinstance(raw.get('team'), dict) or not isinstance(raw.get('stats'), list): continue
			team = raw['team']
			name = _text(team.get('displayName'))
			stats = [item for item in raw['stats'] if isinstance(item, dict)]
			overall = next((item for item in stats if item.get('name') == 'overall' and item.get('type') == 'total'), {})
			seed = next((item.get('value') for item in stats if item.get('name') == 'playoffSeed'), None)
			record = re.fullmatch(r'(\d+)-(\d+)(?:-(\d+))?', str(overall.get('summary') or overall.get('displayValue') or ''))
			if not name or not record: continue
			wins, losses, ties = (int(record.group(1)), int(record.group(2)), int(record.group(3) or 0))
			aliases = {_key(team.get(field)) for field in ('displayName', 'shortDisplayName', 'name', 'abbreviation', 'location')}
			points = '%d-%d' % (wins, losses) if sport == 'nba' else '%d-%d-%d' % (wins, losses, ties)
			entries.append({'team': name, 'section': section, 'section_path': parents + (section,), 'aliases': aliases - {''}, 'points': points, 'seed': seed})
	return entries


def _nba_order(items):
	"""Use ESPN's playoff seed; omit incomplete or ambiguous rankings."""
	seeds = [item.get('seed') for item in items]
	if len(seeds) != 15 or any(not isinstance(seed, (int, float)) or isinstance(seed, bool) or not math.isfinite(seed) or seed != int(seed) for seed in seeds): return []
	if sorted(seeds) != list(range(1, len(items) + 1)) or len({_key(item['team']) for item in items}) != 15: return []
	return sorted(items, key=lambda item: item['seed'])


def _raw_sections(data):
	counts = {}
	pending = [(item, ()) for item in data.get('children', [])]
	while pending:
		group, parents = pending.pop(0)
		if not isinstance(group, dict): continue
		name = _text(group.get('name'))
		path = parents + ((name,) if name else ())
		children = group.get('children', [])
		if isinstance(children, list): pending.extend((child, path) for child in children)
		standings = group.get('standings')
		if name and isinstance(standings, dict) and isinstance(standings.get('entries'), list): counts[path] = len(standings['entries'])
		elif name and not children: counts[path] = 0
	return counts


def get(sport, team_a, team_b):
	"""Return (rows, heading, metric), or empty rows when no reliable match exists."""
	metric = 'PTS' if sport == 'nhl' else 'OVERALL' if sport == 'ncaa' else 'W-L' if sport == 'nba' else 'W-L-T'
	if sport not in ('nhl', 'nfl', 'ncaa', 'nba'): return [], '', metric
	try:
		data = _fetch(sport)
		if not isinstance(data, dict): return [], '', metric
		entries = _nhl(data) if sport == 'nhl' else _espn(data, sport)
		first, second = _match(entries, team_a), _match(entries, team_b)
		selected = first or second
		if not selected: return [], '', metric
		section = selected['section']
		items = [entry for entry in entries if entry['section'] == section]
		if sport == 'nba': items = _nba_order(items)
		if sport == 'nhl': items.sort(key=lambda item: (item['rank'], item['team']))
		# ESPN returns its own tie-break order, which record sorting would lose.
		rows = [{'position': index, 'team': item['team'], 'points': item['points'], 'group': '', 'highlight': item is first or item is second} for index, item in enumerate(items, 1)]
		prefix = {'nhl': 'NHL', 'nfl': 'NFL', 'ncaa': 'College football', 'nba': 'NBA'}[sport]
		return rows, prefix + ' · ' + section + (' Division' if sport == 'nhl' and not section.casefold().endswith('division') else ''), metric
	except (OSError, ValueError, TypeError, AttributeError, KeyError):
		return [], '', metric


def get_all(sport):
	"""Return every available (heading, rows) section and its record metric."""
	metric = 'PTS' if sport == 'nhl' else 'OVERALL' if sport == 'ncaa' else 'W-L' if sport == 'nba' else 'W-L-T'
	if sport not in ('nhl', 'nfl', 'ncaa', 'nba'): return [], metric
	try:
		data = _fetch(sport)
		entries = _nhl(data) if sport == 'nhl' else _espn(data, sport)
		if not entries and sport != 'nhl': return [], metric
		prefix = {'nhl': 'NHL', 'nfl': 'NFL', 'ncaa': 'College football', 'nba': 'NBA'}[sport]
		division_entries = []
		unavailable = set()
		if sport == 'nfl':
			for key, conference in (('nfl_afc', 'American Football Conference'), ('nfl_nfc', 'National Football Conference')):
				try:
					divisions = _espn(_fetch(key))
					sections = {item['section'] for item in divisions}
					if len(sections) != 4 or any(len([item for item in divisions if item['section'] == section]) != 4 or len({_key(item['team']) for item in divisions if item['section'] == section}) != 4 for section in sections): raise ValueError('Incomplete divisions')
					division_entries.extend(divisions)
				except (OSError, ValueError, TypeError, AttributeError, KeyError):
					unavailable.add(conference)
		groups = []
		for source in (entries, division_entries):
			sections = list(dict.fromkeys(entry.get('section_path', (entry['section'],)) for entry in source))
			if sport in ('nfl', 'ncaa', 'nba') and source is entries: sections = list(_raw_sections(data))
			if sport == 'nba' and source is entries:
				for path in (('Eastern Conference',), ('Western Conference',)):
					if path not in sections: sections.append(path)
			if sport == 'nhl' and source is entries:
				sections = list(dict.fromkeys((_text(item.get('divisionName')),) for item in data['standings'] if isinstance(item, dict) and _text(item.get('divisionName'))))
			for path in sections:
				section = path[-1]
				items = [entry for entry in source if entry.get('section_path', (entry['section'],)) == path]
				if sport == 'nba': items = _nba_order(items)
				if sport == 'nhl':
					items.sort(key=lambda item: (item['rank'], item['team']))
					raw_count = sum(isinstance(item, dict) and _text(item.get('divisionName')) == section for item in data['standings'])
					if raw_count != 8 or len(items) != 8 or [item['rank'] for item in items] != list(range(1, 9)):
						groups.append((prefix + ' · ' + section + (' Division' if not section.casefold().endswith('division') else '') + ' (unavailable)', []))
						continue
				if sport in ('nfl', 'ncaa', 'nba') and source is entries and (not _raw_sections(data).get(path) or _raw_sections(data)[path] != len(items)):
					groups.append((prefix + ' · ' + section + ' (unavailable)', []))
					continue
				display = ' · '.join(path) if sum(other[-1] == section for other in sections) > 1 else section
				heading = prefix + ' · ' + display + (' Division' if sport == 'nhl' and not section.casefold().endswith('division') else '')
				if section in unavailable: heading += ' (divisions unavailable)'
				rows = [{'position': index, 'team': item['team'], 'points': item['points'], 'group': '', 'highlight': False} for index, item in enumerate(items, 1)]
				groups.append((heading, rows))
		return groups, metric
	except (OSError, ValueError, TypeError, AttributeError, KeyError):
		return [], metric


def get_nhl_groups(team_a, team_b):
	"""Return the complete four-division NHL table with selected teams marked."""
	try:
		data = _fetch('nhl')
		entries = _nhl(data)
		first, second = _match(entries, team_a), _match(entries, team_b)
		sections = list(dict.fromkeys(_text(item.get('divisionName')) for item in data['standings'] if isinstance(item, dict) and _text(item.get('divisionName'))))
		if len(sections) != 4: return [], 'PTS'
		groups = []
		for section in sections:
			items = sorted((entry for entry in entries if entry['section'] == section), key=lambda entry: entry['rank'])
			raw_count = sum(isinstance(item, dict) and _text(item.get('divisionName')) == section for item in data['standings'])
			if raw_count != 8 or len(items) != 8 or [item['rank'] for item in items] != list(range(1, 9)):
				groups.append((section + ' Division (unavailable)', []))
				continue
			rows = [{'position': item['rank'], 'team': item['team'], 'points': item['points'], 'highlight': item is first or item is second} for item in items]
			groups.append((section + (' Division' if not section.casefold().endswith('division') else ''), rows))
		return groups, 'PTS'
	except (OSError, ValueError, TypeError, AttributeError, KeyError):
		return [], 'PTS'


def get_nfl_groups(team_a, team_b):
	"""Return eight NFL division tables, marking unavailable divisions explicitly."""
	groups = []
	by_conference = []
	for key in ('nfl_afc', 'nfl_nfc'):
		try: by_conference.append(_espn(_fetch(key)))
		except (OSError, ValueError, TypeError, AttributeError, KeyError): by_conference.append([])
	entries = [entry for conference in by_conference for entry in conference]
	first, second = _match(entries, team_a), _match(entries, team_b)
	for prefix, conference in zip(('AFC', 'NFC'), by_conference):
		for division in ('East', 'North', 'South', 'West'):
			section = prefix + ' ' + division
			items = [entry for entry in conference if entry['section'] == section]
			if len(items) != 4 or len({_key(item['team']) for item in items}) != 4:
				groups.append((section + ' (unavailable)', []))
				continue
			rows = [{'position': index, 'team': item['team'], 'points': item['points'], 'highlight': item is first or item is second} for index, item in enumerate(items, 1)]
			groups.append((section, rows))
	return groups, 'W-L-T'


def get_ncaa_groups(team_a, team_b):
	"""Return all college sections with unambiguous game teams highlighted."""
	try:
		data = _fetch('ncaa')
		entries = _espn(data)
		first, second = _match(entries, team_a), _match(entries, team_b)
		sections = list(dict.fromkeys(entry.get('section_path', (entry['section'],)) for entry in entries))
		raw_counts = _raw_sections(data)
		sections = list(raw_counts)
		groups = []
		for path in sections:
			items = [entry for entry in entries if entry.get('section_path', (entry['section'],)) == path]
			name = ' · '.join(path) if sum(other[-1] == path[-1] for other in sections) > 1 else path[-1]
			if not raw_counts.get(path) or raw_counts.get(path) != len(items):
				groups.append((name + ' (unavailable)', []))
				continue
			rows = [{'position': index, 'team': item['team'], 'points': item['points'], 'highlight': item is first or item is second} for index, item in enumerate(items, 1)]
			groups.append((name, rows))
		return groups, 'OVERALL'
	except (OSError, ValueError, TypeError, AttributeError, KeyError):
		return [], 'OVERALL'


def get_nba_groups(team_a, team_b):
	"""Return NBA conferences with both game teams highlighted when recognized."""
	try:
		data = _fetch('nba')
		entries = _espn(data, 'nba')
		first, second = _match(entries, team_a), _match(entries, team_b)
		counts = _raw_sections(data)
		for path in (('Eastern Conference',), ('Western Conference',)): counts.setdefault(path, 0)
		groups = []
		for path, count in counts.items():
			items = [entry for entry in entries if entry.get('section_path') == path]
			name = ' · '.join(path) if sum(other[-1] == path[-1] for other in counts) > 1 else path[-1]
			items = _nba_order(items)
			if not count or count != len(items):
				groups.append((name + ' (unavailable)', []))
				continue
			rows = [{'position': index, 'team': item['team'], 'points': item['points'], 'highlight': item is first or item is second} for index, item in enumerate(items, 1)]
			groups.append((name, rows))
		return groups, 'W-L'
	except (OSError, ValueError, TypeError, AttributeError, KeyError):
		return [], 'W-L'
