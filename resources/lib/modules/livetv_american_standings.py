"""Bounded read-only standings for NHL and American football."""
import json
import re
import threading
import time
from urllib.request import Request, urlopen

_URLS = {
	'nhl': 'https://api-web.nhle.com/v1/standings/now',
	'nfl': 'https://site.api.espn.com/apis/v2/sports/football/nfl/standings',
	'ncaa': 'https://site.api.espn.com/apis/v2/sports/football/college-football/standings',
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
		if not (_nhl(payload) if sport == 'nhl' else _espn(payload)): raise ValueError('No usable standings entries')
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


def _espn(data):
	entries = []
	pending = list(data.get('children', []))
	while pending:
		group = pending.pop(0)
		if not isinstance(group, dict): continue
		children = group.get('children', [])
		if isinstance(children, list): pending.extend(children)
		section = _text(group.get('name'))
		standings = group.get('standings')
		if not section or not isinstance(standings, dict): continue
		for raw in standings.get('entries', []):
			if not isinstance(raw, dict) or not isinstance(raw.get('team'), dict) or not isinstance(raw.get('stats'), list): continue
			team = raw['team']
			name = _text(team.get('displayName'))
			stats = [item for item in raw['stats'] if isinstance(item, dict)]
			overall = next((item for item in stats if item.get('name') == 'overall' and item.get('type') == 'total'), {})
			record = re.fullmatch(r'(\d+)-(\d+)(?:-(\d+))?', str(overall.get('summary') or overall.get('displayValue') or ''))
			if not name or not record: continue
			wins, losses, ties = (int(record.group(1)), int(record.group(2)), int(record.group(3) or 0))
			aliases = {_key(team.get(field)) for field in ('displayName', 'shortDisplayName', 'name', 'abbreviation', 'location')}
			entries.append({'team': name, 'section': section, 'aliases': aliases - {''}, 'points': '%d-%d-%d' % (wins, losses, ties)})
	return entries


def get(sport, team_a, team_b):
	"""Return (rows, heading, metric), or empty rows when no reliable match exists."""
	metric = 'PTS' if sport == 'nhl' else 'OVERALL' if sport == 'ncaa' else 'W-L-T'
	if sport not in _URLS: return [], '', metric
	try:
		data = _fetch(sport)
		if not isinstance(data, dict): return [], '', metric
		entries = _nhl(data) if sport == 'nhl' else _espn(data)
		first, second = _match(entries, team_a), _match(entries, team_b)
		selected = first or second
		if not selected: return [], '', metric
		section = selected['section']
		items = [entry for entry in entries if entry['section'] == section]
		if sport == 'nhl': items.sort(key=lambda item: (item['rank'], item['team']))
		# ESPN returns its own tie-break order, which record sorting would lose.
		rows = [{'position': index, 'team': item['team'], 'points': item['points'], 'group': '', 'highlight': item is first or item is second} for index, item in enumerate(items, 1)]
		prefix = {'nhl': 'NHL', 'nfl': 'NFL', 'ncaa': 'College football'}[sport]
		return rows, prefix + ' · ' + section + (' Division' if sport == 'nhl' and not section.casefold().endswith('division') else ''), metric
	except (OSError, ValueError, TypeError, AttributeError, KeyError):
		return [], '', metric
