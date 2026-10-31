"""Fetch live and upcoming ATP and WTA tennis matches from ESPN."""
import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from modules import livetv_american_fixtures as _scoreboard

_TOURS = ('atp', 'wta')
_CACHE = None


def _singles_aliases(name, short):
	"""Use ESPN's abbreviated name to identify a full surname safely."""
	if not isinstance(short, str): return []
	match = re.fullmatch(r'(?:[^\W\d_]\.\s*)+(.+)', short.strip(), re.UNICODE)
	if not match: return []
	surname = match.group(1).strip()
	if not surname or not name.casefold().endswith(' ' + surname.casefold()): return []
	given = name[:-(len(surname) + 1)].strip()
	if not given: return []
	return [surname, surname + ', ' + given]


def get_schedule(cache_dir=None):
	"""Return known live matches and matches starting within the next 24 hours."""
	global _CACHE
	if _CACHE and time.monotonic() - _CACHE[0] < 30: return list(_CACHE[1])
	path = os.path.join(cache_dir, 'livetv-scoreboard-tennis-schedule.json') if cache_dir else None
	if path:
		fixtures = _scoreboard._cached(path)
		if fixtures is not None: return fixtures
	now = datetime.now(timezone.utc)
	fixtures = []
	seen = set()
	for tour in _TOURS:
		url = 'https://site.api.espn.com/apis/site/v2/sports/tennis/%s/scoreboard' % tour
		with urlopen(Request(url, headers={'User-Agent': 'BINGIE Live TV'}), timeout=10) as response:
			body = response.read(8 * 1024 * 1024 + 1)
		if len(body) > 8 * 1024 * 1024: raise ValueError('Scoreboard response too large')
		data = json.loads(body)
		events = data.get('events') if isinstance(data, dict) else None
		if not isinstance(events, list): raise ValueError('Invalid tennis scoreboard response')
		for event in events:
			if not isinstance(event, dict): continue
			tournament = event.get('name')
			tournament_id = str(event.get('id') or '')
			if not isinstance(tournament, str) or not tournament.strip() or not tournament_id: continue
			groupings = event.get('groupings')
			if not isinstance(groupings, list): continue
			for grouping in groupings:
				if not isinstance(grouping, dict) or not isinstance(grouping.get('competitions'), list): continue
				group_info = grouping.get('grouping')
				slug = group_info.get('slug') if isinstance(group_info, dict) else ''
				discipline = {'mens-singles': 'singles', 'womens-singles': 'singles', 'mens-doubles': 'doubles', 'womens-doubles': 'doubles'}.get(slug, '')
				for match in grouping['competitions']:
					if not isinstance(match, dict): continue
					identity = str(match.get('id') or '')
					if not identity or (tour, identity) in seen: continue
					status = match.get('status') or {}
					kind = status.get('type') if isinstance(status, dict) else None
					if not isinstance(kind, dict): continue
					state = kind.get('state')
					if state not in ('in', 'pre') or kind.get('name') in ('STATUS_POSTPONED', 'STATUS_CANCELED', 'STATUS_CANCELLED'): continue
					date = match.get('date')
					if not isinstance(date, str): continue
					if state == 'pre':
						try:
							start = datetime.fromisoformat(date.replace('Z', '+00:00'))
							if start.utcoffset() is None or not 0 < (start - now).total_seconds() <= 24 * 3600: continue
						except (ValueError, TypeError, OverflowError): continue
					competitors = match.get('competitors')
					if not isinstance(competitors, list): continue
					sides = {}
					for competitor in competitors:
						if not isinstance(competitor, dict): continue
						side = competitor.get('homeAway')
						if side not in ('home', 'away') or side in sides: continue
						person = competitor.get('athlete') or competitor.get('roster')
						if not isinstance(person, dict): continue
						name = person.get('displayName')
						if not isinstance(name, str) or not name.strip() or 'TBD' in name.upper(): continue
						aliases = [name]
						short = person.get('shortName') or person.get('shortDisplayName')
						if isinstance(short, str) and short.strip(): aliases.append(short)
						if isinstance(competitor.get('athlete'), dict): aliases.extend(_singles_aliases(name, short))
						player_ids = []
						player_names = []
						if discipline == 'singles' and isinstance(competitor.get('athlete'), dict):
							player_id = str(competitor.get('id') or '')
							if re.fullmatch(r'[0-9]{1,12}', player_id) and int(player_id) > 0: player_ids.append(player_id)
							player_names = [name]
						elif discipline == 'doubles' and isinstance(competitor.get('roster'), dict):
							athletes = person.get('athletes')
							if isinstance(athletes, list) and len(athletes) == 2 and all(isinstance(athlete, dict) and isinstance(athlete.get('displayName'), str) for athlete in athletes):
								player_names = [athlete['displayName'] for athlete in athletes]
						sides[side] = {'name': name, 'aliases': aliases, 'player_ids': player_ids, 'player_names': player_names}
					if len(sides) != 2: continue
					fixtures.append({'league': '%s:%s' % (tour, tournament_id), 'league_label': '%s · %s' % (tour.upper(), tournament.strip()), 'tour': tour, 'discipline': discipline, 'date': date, 'id': identity, 'home': sides['home'], 'away': sides['away'], 'status': 'LIVE' if state == 'in' else 'Scheduled'})
					seen.add((tour, identity))
	_CACHE = (time.monotonic(), fixtures)
	if path: _scoreboard._save(path, fixtures)
	return list(fixtures)
