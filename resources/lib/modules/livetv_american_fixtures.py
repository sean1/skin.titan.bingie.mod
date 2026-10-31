"""Fetch current American sports fixtures from ESPN scoreboards."""
import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
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
	return _get(sport, cache_dir, False)


def get_schedule(sport, cache_dir=None):
	"""Return live games and games scheduled to start in the next 24 hours."""
	return _get(sport, cache_dir, True)


def _get(sport, cache_dir, schedule):
	if sport not in _SCOREBOARDS: raise ValueError('Unknown sport')
	cache_key = sport + ('-schedule' if schedule else '')
	cached = _CACHE.get(cache_key)
	allowed = {league for league, _ in _SCOREBOARDS[sport]}
	if cached and time.monotonic() - cached[0] < 30: return [fixture for fixture in cached[1] if fixture.get('league') in allowed]
	cache_path = os.path.join(cache_dir, 'livetv-scoreboard-' + cache_key + '.json') if cache_dir else None
	if cache_path:
		fixtures = _cached(cache_path)
		if fixtures is not None:
			# Do not extend a disk entry's lifetime when copying it into memory.
			return [fixture for fixture in fixtures if fixture.get('league') in allowed]
	fixtures = []
	now = datetime.now(timezone.utc)
	for league, scoreboard_path in _SCOREBOARDS[sport]:
		url = 'https://site.api.espn.com/apis/site/v2/sports/' + scoreboard_path + '/scoreboard'
		urls = [url + '?dates=' + (now + timedelta(days=offset)).strftime('%Y%m%d') + '&limit=1000' for offset in (-1, 0, 1)] if schedule else [url]
		events = []
		for request_url in urls:
			with urlopen(Request(request_url, headers={'User-Agent': 'BINGIE Live TV'}), timeout=10) as response:
				body = response.read(8 * 1024 * 1024 + 1)
			if len(body) > 8 * 1024 * 1024: raise ValueError('Scoreboard response too large')
			data = json.loads(body)
			day_events = data.get('events') if isinstance(data, dict) else None
			if not isinstance(day_events, list): raise ValueError('Invalid scoreboard response')
			events.extend(day_events)
		seen = set()
		for event in events:
			if not isinstance(event, dict): continue
			event_id = str(event.get('id') or '')
			if event_id and event_id in seen: continue
			status = event.get('status')
			if not isinstance(status, dict) or not isinstance(status.get('type'), dict): continue
			event_type = status['type']
			state = event_type.get('state')
			if state != 'in':
				if not schedule or state != 'pre': continue
				if event_type.get('name') in ('STATUS_POSTPONED', 'STATUS_CANCELED', 'STATUS_CANCELLED'): continue
				try:
					start = datetime.fromisoformat(str(event.get('date') or '').replace('Z', '+00:00'))
					if start.utcoffset() is None or not 0 < (start - now).total_seconds() <= 24 * 3600: continue
				except (ValueError, TypeError, OverflowError): continue
			competitions = event.get('competitions') or []
			if not isinstance(competitions, list) or not competitions or not isinstance(competitions[0], dict): continue
			competitors = competitions[0].get('competitors') or []
			if not isinstance(competitors, list): continue
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
			fixture = {'league': league, 'date': str(event.get('date') or ''), 'id': str(event.get('id') or ''), 'home': sides['home'], 'away': sides['away']}
			if schedule: fixture['status'] = 'LIVE' if state == 'in' else 'Scheduled'
			fixtures.append(fixture)
			if event_id: seen.add(event_id)
	_CACHE[cache_key] = (time.monotonic(), fixtures)
	if cache_path: _save(cache_path, fixtures)
	return fixtures
