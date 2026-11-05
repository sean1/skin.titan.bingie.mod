"""Match sports fixtures to provider events."""
import math
import re
import unicodedata
from datetime import datetime
from modules.livetv_fixtures import LIVE_STATUSES


_SPORT_FIELDS = {'hockey': re.compile(r'^(?:NHL|NHL\.TV)$', re.I), 'football': re.compile(r'^NFL$', re.I), 'basketball': re.compile(r'^(?:NBA|NBA\.TV)$', re.I)}
_SPORT_LEAGUES = {'hockey': 'nhl', 'football': 'nfl', 'basketball': 'nba'}
_MONTHS = ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december')
_MONTH_NUMBERS = {name: index for index, name in enumerate(_MONTHS, 1)}
_MONTH_NUMBERS.update({name[:3]: index for index, name in enumerate(_MONTHS, 1)})
_MONTH_NUMBERS['sept'] = 9
_METADATA_DATE = re.compile(r'(?<![\d/.-])(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/](?:\d{4}|\d{2})|'
	r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:,?\s+\d{4})?|'
	r'\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(?:,?\s+\d{4})?)(?![\d/.-])', re.I)
_EVENT = re.compile(r"^([\w .&'’\-]{2,80}?)\s+(?:vs\.?|v\.?|at|@)\s+([\w .&'’\-]{2,80}?)$", re.I)
_NON_LIVE_TITLE = re.compile(r'\b(?:end|ended|highlights?|recaps?|previews?|replays?|delayed|encore|re-?runs?|repeats?|recorded)\b', re.I)
_WORD = re.compile(r'\w+')


def record_timing(phase, elapsed, sport='', outcome=''):
	"""Log bounded, non-identifying timing data for sports loading stages."""
	if phase not in ('catalog_fetch', 'catalog_queue', 'catalog_open', 'catalog_read', 'catalog_parse', 'catalog_clean', 'catalog_write', 'direct_match', 'guide_match', 'guide_fetch', 'guide_refresh', 'lookup', 'lookup_deadline', 'ui_handoff', 'fixture_fetch', 'first_feed'): return
	if sport not in ('', 'soccer', 'hockey', 'football', 'basketball', 'tennis', 'f1') or outcome not in ('', 'ok', 'unavailable', 'cancelled', 'partial', 'network', 'cache', 'stale', 'catalog', 'guide', 'saved', 'match', 'attempted'): return
	try:
		if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool) or not math.isfinite(elapsed) or not 0 <= elapsed <= 86400: return
		import xbmc
		xbmc.log('BINGIE sports timing phase=%s sport=%s outcome=%s elapsed_ms=%d' % (phase, sport or 'unknown', outcome or 'unknown', round(elapsed * 1000)), xbmc.LOGINFO)
	except Exception: pass


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


def _metadata_dates(value, year):
	"""Parse a channel date, keeping explicit years and both numeric orders."""
	parts = re.fullmatch(r'(\d{4})([-/])(\d{1,2})\2(\d{1,2})', value)
	if parts:
		candidates = [(int(parts[1]), int(parts[3]), int(parts[4]))]
	else:
		parts = re.fullmatch(r'(\d{1,2})([-/])(\d{1,2})(?:\2(\d{4}|\d{2}))?', value)
		if parts:
			first, second = int(parts[1]), int(parts[3])
			if parts[4] and len(parts[4]) == 2:
				base_year = year // 100 * 100 + int(parts[4])
				years = (min((base_year - 100, base_year, base_year + 100), key=lambda candidate: abs(candidate - year)),)
			else: years = (int(parts[4]),) if parts[4] else (year - 1, year, year + 1)
			candidates = [(candidate_year, month, day) for candidate_year in years for month, day in ((first, second), (second, first))]
		else:
			parts = value.casefold().replace(',', '').split()
			if len(parts) not in (2, 3): return []
			years = (int(parts[2]),) if len(parts) == 3 and parts[2].isdigit() else (year - 1, year, year + 1)
			if parts[0] in _MONTH_NUMBERS and parts[1].isdigit(): candidates = [(candidate_year, _MONTH_NUMBERS[parts[0]], int(parts[1])) for candidate_year in years]
			elif parts[1] in _MONTH_NUMBERS and parts[0].isdigit(): candidates = [(candidate_year, _MONTH_NUMBERS[parts[1]], int(parts[0])) for candidate_year in years]
			else: return []
	parsed = []
	for candidate in candidates:
		try: parsed.append(datetime(*candidate).date())
		except (ValueError, OverflowError): pass
	return parsed


def _date_may_match(name, fixture):
	match = _METADATA_DATE.search(name)
	if not match: return True
	try:
		date = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00')).date()
		parsed = _metadata_dates(match.group(), date.year)
		return bool(parsed) and any(abs((value - date).days) <= 1 for value in parsed)
	except (AttributeError, KeyError, TypeError, ValueError): return True


def fixture_groups(streams, sport, fixtures, live_only=False):
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
	alias_heads = None
	if sport == 'tennis':
		# Every word-starting alias needs its first word in the title; keep the full regex as the final match.
		heads = [_WORD.match(alias) for alias in alias_teams]
		if all(heads): alias_heads = {head.group().casefold().replace('ı', 'i') for head in heads}
	pattern = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(alias).replace(r'\ ', r'\s+').replace('/', r'\s*/\s*') if sport == 'tennis' else re.escape(alias).replace(r'\ ', r'\s+') for alias in sorted(alias_teams, key=len, reverse=True)) + r')(?!\w)', re.I)
	separator = re.compile(r'\b(?:vs?\.?|at)\b|@' + (r'|\s+-\s+' if sport == 'tennis' else ''), re.I)
	result = {}
	for stream in streams:
		if not isinstance(stream, dict): continue
		stream_id, name = str(stream.get('stream_id') or ''), stream.get('name')
		if not stream_id.isdecimal() or not isinstance(name, str) or not separator.search(name): continue
		if non_live_title(name): continue
		if sport == 'tennis': name = _tennis_text(name)
		if alias_heads is not None and not any(word.group().casefold().replace('ı', 'i') in alias_heads for word in _WORD.finditer(name)): continue
		found = {}
		for match in pattern.finditer(name):
			alias = _tennis_alias(match.group()) if sport == 'tennis' else ' '.join(match.group().casefold().split())
			for index, side in alias_teams.get(alias, ()):
				found.setdefault(index, {}).setdefault(side, []).append((match.start(), match.end()))
		candidates = []
		for index, sides in found.items():
			if 'home' not in sides or 'away' not in sides: continue
			fixture = fixtures[index]
			if live_only and fixture.get('status') not in LIVE_STATUSES: continue
			if live_only and any(other != index and fixtures[other].get('status') not in LIVE_STATUSES and fixtures[other].get('league') == fixture.get('league') and {fixtures[other][side].get('name', '').casefold() for side in ('home', 'away')} == {fixture[side].get('name', '').casefold() for side in ('home', 'away')} and _date_may_match(name, fixtures[other]) for other in found): continue
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
					parsed = _metadata_dates(value, fixture_date.year)
					if not parsed or all(abs((date - fixture_date).days) > 1 for date in parsed): continue
				except (ValueError, KeyError): continue
			candidates.append(fixture)
		if len(candidates) != 1: continue
		fixture = candidates[0]
		event = tuple(' '.join(fixture[side]['name'].casefold().split()) for side in ('home', 'away'))
		key = (event, str(fixture.get('league') or ''))
		result.setdefault(key, {}).setdefault(stream_id, stream)
	return result
