"""Fetch current American sports fixtures from ESPN scoreboards."""
import json
import os
import tempfile
import time
from urllib.request import Request, urlopen

_SCOREBOARDS = {
	'hockey': (('nhl', 'hockey/nhl'),),
	'basketball': (('nba', 'basketball/nba'),),
	'football': (('nfl', 'football/nfl'),),
}
_CACHE = {}


def _cached(path):
	try:
		with open(path, encoding='utf-8') as source:
			payload = json.loads(source.read(1024 * 1024 + 1))
		age = time.time() - payload['created']
		fixtures = payload['fixtures']
		if not 0 <= age < 30 or not isinstance(fixtures, list) or len(fixtures) > 300: return None
		for fixture in fixtures:
			if not isinstance(fixture, dict) or not all(isinstance(fixture.get(key), str) for key in ('league', 'date', 'id')): return None
			for side in ('home', 'away'):
				team = fixture.get(side)
				if not isinstance(team, dict) or not isinstance(team.get('name'), str) or not isinstance(team.get('aliases'), list) or not all(isinstance(a, str) for a in team['aliases']): return None
		return fixtures
	except (OSError, ValueError, TypeError, KeyError):
		return None


def _save(path, fixtures):
	temporary = None
	try:
		with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=os.path.dirname(path), prefix='.livetv-scoreboard-', delete=False) as target:
			temporary = target.name
			json.dump({'created': time.time(), 'fixtures': fixtures}, target)
		os.replace(temporary, path)
		temporary = None
	except (OSError, ValueError, TypeError):
		pass
	finally:
		if temporary:
			try: os.unlink(temporary)
			except OSError: pass


def get_live(sport, cache_dir=None):
	"""Return currently live fixtures, or raise when a scoreboard is unavailable."""
	if sport not in _SCOREBOARDS: raise ValueError('Unknown sport')
	cached = _CACHE.get(sport)
	allowed = {league for league, _ in _SCOREBOARDS[sport]}
	if cached and time.monotonic() - cached[0] < 30: return [fixture for fixture in cached[1] if fixture.get('league') in allowed]
	cache_path = os.path.join(cache_dir, 'livetv-scoreboard-' + sport + '.json') if cache_dir else None
	if cache_path:
		fixtures = _cached(cache_path)
		if fixtures is not None:
			# Do not extend a disk entry's lifetime when copying it into memory.
			return [fixture for fixture in fixtures if fixture.get('league') in allowed]
	fixtures = []
	for league, scoreboard_path in _SCOREBOARDS[sport]:
		url = 'https://site.api.espn.com/apis/site/v2/sports/' + scoreboard_path + '/scoreboard'
		with urlopen(Request(url, headers={'User-Agent': 'BINGIE Live TV'}), timeout=10) as response:
			body = response.read(8 * 1024 * 1024 + 1)
		if len(body) > 8 * 1024 * 1024: raise ValueError('Scoreboard response too large')
		data = json.loads(body)
		events = data.get('events') if isinstance(data, dict) else None
		if not isinstance(events, list): raise ValueError('Invalid scoreboard response')
		for event in events:
			if not isinstance(event, dict): continue
			if event.get('status', {}).get('type', {}).get('state') != 'in': continue
			competitions = event.get('competitions') or []
			if not competitions or not isinstance(competitions[0], dict): continue
			competitors = competitions[0].get('competitors') or []
			sides = {}
			for competitor in competitors:
				if not isinstance(competitor, dict): continue
				team = competitor.get('team') or {}
				if not isinstance(team, dict): continue
				name = team.get('displayName')
				side = competitor.get('homeAway')
				if not isinstance(name, str) or side not in ('home', 'away'): continue
				names = [team.get(k) for k in ('displayName', 'shortDisplayName', 'name')]
				sides[side] = {'name': name, 'aliases': [n for n in names if isinstance(n, str) and n.strip()]}
			if len(sides) != 2: continue
			fixtures.append({'league': league, 'date': str(event.get('date') or ''), 'id': str(event.get('id') or ''), 'home': sides['home'], 'away': sides['away']})
	_CACHE[sport] = (time.monotonic(), fixtures)
	if cache_path: _save(cache_path, fixtures)
	return fixtures
