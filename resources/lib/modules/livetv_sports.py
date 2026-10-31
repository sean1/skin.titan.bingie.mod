"""Match sports fixtures to provider events."""
import re
import unicodedata
from datetime import datetime


_SPORT_FIELDS = {'hockey': re.compile(r'^(?:NHL|NHL\.TV)$', re.I), 'football': re.compile(r'^NFL$', re.I), 'basketball': re.compile(r'^(?:NBA|NBA\.TV)$', re.I)}
_SPORT_LEAGUES = {'hockey': 'nhl', 'football': 'nfl', 'basketball': 'nba'}
_METADATA_DATE = re.compile(r'\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4}|'
	r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*)\b', re.I)
_EVENT = re.compile(r"^([\w .&'’\-]{2,80}?)\s+(?:vs\.?|v\.?|at|@)\s+([\w .&'’\-]{2,80}?)$", re.I)
_NON_LIVE_TITLE = re.compile(r'\b(?:end|ended|highlights?|recaps?|previews?|replays?|delayed|encore|re-?runs?|repeats?|recorded)\b', re.I)


def non_live_title(name):
	"""Identify provider labels that describe non-live programming."""
	return bool(_NON_LIVE_TITLE.search(name))


def _tennis_text(value):
	"""Compare tennis names without differences in Unicode accent marks."""
	return ''.join(char for char in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(char))


def _tennis_alias(value):
	return re.sub(r'\s*/\s*', '/', ' '.join(_tennis_text(value).split()))


def _tennis_conflicting_name(name, span, side_start, side_end, team):
	"""Reject a supplied first name or initial that contradicts a surname match."""
	matched = _tennis_alias(name[span[0]:span[1]]).split('/', 1)[0]
	names = team.get('player_names')
	if not isinstance(names, list) or not names: names = [team.get('name', '')]
	people = [_tennis_alias(person) for person in names if isinstance(person, str)]
	people = [person for person in people if len(person.split()) > 1 and person.split()[-1] == matched]
	if len(people) != 1: return False
	given = people[0].split()[:-1]
	before = re.search(r'([^\W\d_]+)(?:\.\s*|\s+)$', name[side_start:span[0]])
	after = re.match(r'(?:\s*,\s*|\s+)([^\W\d_]+)\.?(?:\s|$)', name[span[1]:side_end]) if '/' not in name[span[0]:span[1]] else None
	ignored = {'atp', 'wta', 'tennis', 'live', 'next', 'upcoming', 'match', 'court', 'men', 'women', 'singles', 'doubles', 'v', 'vs', 'at'}
	for match in (before, after):
		if match:
			value = match.group(1)
			if value not in ignored and value not in given and not (len(value) == 1 and value in {word[0] for word in given}): return True
	return False

def _event(name):
	match = _EVENT.fullmatch(name)
	if not match: return None
	teams = tuple(' '.join(side.casefold().split()) for side in match.groups())
	if teams[0] == teams[1] or not all(re.search(r'[^\W\d_]', team) for team in teams): return None
	return teams


def fixture_groups(streams, sport, fixtures):
	"""Match event-labelled channels to unambiguous live scoreboard fixtures."""
	if sport not in _SPORT_FIELDS and sport != 'tennis': raise ValueError('Unknown sport')
	if not isinstance(streams, list) or not isinstance(fixtures, list): return {}
	fixtures = [fixture for fixture in fixtures if isinstance(fixture, dict) and (sport == 'tennis' or str(fixture.get('league') or '').casefold() == _SPORT_LEAGUES[sport])]
	# Ignore nicknames which could identify more than one team in this league.
	alias_owners = {}
	for fixture in fixtures:
		for side in ('home', 'away'):
			team = fixture.get(side, {})
			owner = team.get('name', '').casefold()
			for alias in team.get('aliases', []):
				alias = _tennis_alias(alias) if sport == 'tennis' else ' '.join(alias.casefold().split())
				if len(alias) >= 3: alias_owners.setdefault(alias, set()).add(owner)
	alias_teams = {}
	for index, fixture in enumerate(fixtures):
		for side in ('home', 'away'):
			team = fixture.get(side, {})
			owner = team.get('name', '').casefold()
			for alias in team.get('aliases', []):
				alias = _tennis_alias(alias) if sport == 'tennis' else ' '.join(alias.casefold().split())
				if len(alias) >= 3 and alias_owners.get(alias) == {owner}: alias_teams.setdefault(alias, set()).add((index, side))
	if sport == 'tennis':
		# Only combine two structured players when each alias identifies one person
		# across the schedule; this also permits the provider to reverse teammates.
		players, doubles = {}, []
		for index, fixture in enumerate(fixtures):
			for side in ('home', 'away'):
				team = fixture.get(side, {})
				names = team.get('player_names')
				if not isinstance(names, list) or not names:
					names = [team.get('name', '')] if fixture.get('discipline') != 'doubles' and '/' not in team.get('name', '') else []
				if any(not isinstance(name, str) or not name.strip() for name in names): continue
				keys = []
				for name in names:
					full = _tennis_alias(name)
					aliases = {full, full.split()[-1]} if full else set()
					aliases = {alias for alias in aliases if len(alias) >= 3}
					keys.append((full, aliases))
					for alias in aliases: players.setdefault(alias, set()).add(full)
				if fixture.get('discipline') == 'doubles' and len(keys) == 2 and keys[0][0] != keys[1][0]: doubles.append((index, side, keys))
		for index, side, keys in doubles:
			first = [alias for alias in keys[0][1] if players.get(alias) == {keys[0][0]}]
			second = [alias for alias in keys[1][1] if players.get(alias) == {keys[1][0]}]
			for a in first:
				for b in second:
					alias_teams.setdefault(a + '/' + b, set()).add((index, side))
					alias_teams.setdefault(b + '/' + a, set()).add((index, side))
	if not alias_teams: return {}
	pattern = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(alias).replace(r'\ ', r'\s+').replace('/', r'\s*/\s*') if sport == 'tennis' else re.escape(alias).replace(r'\ ', r'\s+') for alias in sorted(alias_teams, key=len, reverse=True)) + r')(?!\w)', re.I)
	separator = re.compile(r'\b(?:vs?\.?|at)\b|@' + (r'|\s+-\s+' if sport == 'tennis' else ''), re.I)
	result = {}
	for stream in streams:
		if not isinstance(stream, dict): continue
		stream_id, name = str(stream.get('stream_id') or ''), stream.get('name')
		if not stream_id.isdecimal() or not isinstance(name, str) or not separator.search(name): continue
		if non_live_title(name): continue
		if sport == 'tennis': name = _tennis_text(name)
		found = {}
		for match in pattern.finditer(name):
			alias = _tennis_alias(match.group()) if sport == 'tennis' else ' '.join(match.group().casefold().split())
			for index, side in alias_teams.get(alias, ()):
				found.setdefault(index, {}).setdefault(side, []).append((match.start(), match.end()))
		candidates = []
		for index, sides in found.items():
			if 'home' not in sides or 'away' not in sides: continue
			fixture = fixtures[index]
			pairs = [((a, 'home'), (b, 'away')) if a[0] < b[0] else ((b, 'away'), (a, 'home')) for a in sides['home'] for b in sides['away'] if a[1] <= b[0] or b[1] <= a[0]]
			def find_separator(value):
				matches = list(separator.finditer(value))
				if sport == 'tennis': return next((match for match in matches if match.group().strip().rstrip('.') != 'v'), matches[0] if matches else None)
				return matches[0] if matches else None
			pairs = [(a, a_side, b, b_side, find_separator(name[a[1]:b[0]])) for (a, a_side), (b, b_side) in pairs]
			pairs = [(a, a_side, b, b_side, sep) for a, a_side, b, b_side, sep in pairs if sep]
			if not pairs: continue
			if sport == 'tennis':
				tours = set(re.findall(r'\b(?:atp|wta)\b', name))
				tour = str(fixture.get('league') or '').split(':', 1)[0].casefold()
				if len(tours) == 1 and tour in ('atp', 'wta') and tour not in tours: continue
				valid_pairs = []
				for a, a_side, b, b_side, sep in pairs:
					left = max(name.rfind('|', 0, a[0]), name.rfind(':', 0, a[0])) + 1
					right = name.find('|', b[1])
					right = len(name) if right < 0 else right
					if _tennis_conflicting_name(name, a, left, a[1] + sep.start(), fixture.get(a_side, {})) or _tennis_conflicting_name(name, b, a[1] + sep.end(), right, fixture.get(b_side, {})): continue
					valid_pairs.append((a, b, sep, left, right))
				if not valid_pairs: continue
				if fixture.get('discipline') == 'singles' or (not fixture.get('discipline') and all('/' not in fixture.get(side, {}).get('name', '') for side in ('home', 'away'))):
					if re.search(r'\bdoubles\b', name): continue
					def doubles_pair(a, b, sep, left, right):
						return re.search(r'[^\W\d_]\s*/\s*[^\W\d_]', name[left:a[1] + sep.start()]) or re.search(r'[^\W\d_]\s*/\s*[^\W\d_]', name[a[1] + sep.end():right])
					if all(doubles_pair(*pair) for pair in valid_pairs): continue
			if re.search(r'\bupcoming\b', name, re.I) and fixture.get('status') != 'Scheduled': continue
			# Ignore clearly different calendar dates, while allowing timezone differences.
			date_match = _METADATA_DATE.search(name)
			if date_match:
				try:
					fixture_date = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00')).date()
					value = date_match.group()
					parsed = []
					for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%d-%m-%Y', '%d/%m/%Y', '%m-%d-%Y', '%m/%d/%Y', '%b %d %Y', '%B %d %Y', '%d %b %Y', '%d %B %Y'):
						try: parsed.append(datetime.strptime(value if re.search(r'\d{4}', value) else value + ' ' + str(fixture_date.year), fmt).date())
						except ValueError: pass
					if not parsed or all(abs((date - fixture_date).days) > 1 for date in parsed): continue
				except (ValueError, KeyError): continue
			candidates.append(fixture)
		if len(candidates) != 1: continue
		fixture = candidates[0]
		event = tuple(' '.join(fixture[side]['name'].casefold().split()) for side in ('home', 'away'))
		key = (event, str(fixture.get('league') or ''))
		result.setdefault(key, {}).setdefault(stream_id, stream)
	return result
