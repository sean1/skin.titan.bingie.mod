"""Group explicitly live hockey, football, and basketball provider events."""
import re
from datetime import datetime


_SPORT_FIELDS = {'hockey': re.compile(r'^(?:NHL|NHL\.TV)$', re.I), 'football': re.compile(r'^NFL$', re.I), 'basketball': re.compile(r'^(?:NBA|NBA\.TV)$', re.I)}
_SPORT_LEAGUES = {'hockey': 'nhl', 'football': 'nfl', 'basketball': 'nba'}
_METADATA_DATE = re.compile(r'\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4}|'
	r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*)\b', re.I)
_EVENT = re.compile(r"^([\w .&'’\-]{2,80}?)\s+(?:vs\.?|v\.?|at|@)\s+([\w .&'’\-]{2,80}?)$", re.I)

def _event(name):
	match = _EVENT.fullmatch(name)
	if not match: return None
	teams = tuple(' '.join(side.casefold().split()) for side in match.groups())
	if teams[0] == teams[1] or not all(re.search(r'[^\W\d_]', team) for team in teams): return None
	return teams


def fixture_groups(streams, sport, fixtures):
	"""Match event-labelled channels to unambiguous live scoreboard fixtures."""
	if sport not in _SPORT_FIELDS: raise ValueError('Unknown sport')
	if not isinstance(streams, list) or not isinstance(fixtures, list): return {}
	fixtures = [fixture for fixture in fixtures if isinstance(fixture, dict) and str(fixture.get('league') or '').casefold() == _SPORT_LEAGUES[sport]]
	# Ignore nicknames which could identify more than one team in this league.
	alias_owners = {}
	for fixture in fixtures:
		for side in ('home', 'away'):
			team = fixture.get(side, {})
			owner = team.get('name', '').casefold()
			for alias in team.get('aliases', []):
				alias = ' '.join(alias.casefold().split())
				if len(alias) >= 3: alias_owners.setdefault(alias, set()).add(owner)
	alias_teams = {}
	for index, fixture in enumerate(fixtures):
		for side in ('home', 'away'):
			team = fixture.get(side, {})
			owner = team.get('name', '').casefold()
			for alias in team.get('aliases', []):
				alias = ' '.join(alias.casefold().split())
				if len(alias) >= 3 and alias_owners.get(alias) == {owner}: alias_teams.setdefault(alias, set()).add((index, side))
	if not alias_teams: return {}
	pattern = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(alias).replace(r'\ ', r'\s+') for alias in sorted(alias_teams, key=len, reverse=True)) + r')(?!\w)', re.I)
	separator = re.compile(r'\b(?:vs?\.?|at)\b|@', re.I)
	result = {}
	for stream in streams:
		if not isinstance(stream, dict): continue
		stream_id, name = str(stream.get('stream_id') or ''), stream.get('name')
		if not stream_id.isdecimal() or not isinstance(name, str) or not separator.search(name): continue
		if re.search(r'\b(?:end|ended|replay|delayed)\b', name, re.I): continue
		found = {}
		for match in pattern.finditer(name):
			alias = ' '.join(match.group().casefold().split())
			for index, side in alias_teams.get(alias, ()):
				found.setdefault(index, {}).setdefault(side, []).append((match.start(), match.end()))
		candidates = []
		for index, sides in found.items():
			if 'home' not in sides or 'away' not in sides: continue
			if not any(separator.search(name[min(a[1], b[1]):max(a[0], b[0])]) for a in sides['home'] for b in sides['away'] if a[1] <= b[0] or b[1] <= a[0]): continue
			fixture = fixtures[index]
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
