"""Group explicitly live hockey and American football provider events."""
import re
from datetime import datetime


_SPORT_FIELDS = {'hockey': re.compile(r'^(?:NHL|NHL\.TV)$', re.I), 'football': re.compile(r'^(?:NFL|NCAA\s+Football)$', re.I)}
_METADATA_DATE = re.compile(r'\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4}|'
	r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*)\b', re.I)
_EVENT = re.compile(r"^([\w .&'’\-]{2,80}?)\s+(?:vs\.?|v\.?|at|@)\s+([\w .&'’\-]{2,80}?)$", re.I)

_FRANCHISES = {
	'hockey': ('anaheim ducks', 'boston bruins', 'buffalo sabres', 'calgary flames', 'carolina hurricanes', 'chicago blackhawks', 'colorado avalanche', 'columbus blue jackets',
		'dallas stars', 'detroit red wings', 'edmonton oilers', 'florida panthers', 'los angeles kings', 'minnesota wild', 'montreal canadiens', 'nashville predators',
		'new jersey devils', 'new york islanders', 'new york rangers', 'ottawa senators', 'philadelphia flyers', 'pittsburgh penguins', 'san jose sharks', 'seattle kraken',
		'st louis blues', 'tampa bay lightning', 'toronto maple leafs', 'vancouver canucks', 'vegas golden knights', 'washington capitals', 'winnipeg jets', 'utah mammoth'),
	'football': ('arizona cardinals', 'atlanta falcons', 'baltimore ravens', 'buffalo bills', 'carolina panthers', 'chicago bears', 'cincinnati bengals', 'cleveland browns',
		'dallas cowboys', 'denver broncos', 'detroit lions', 'green bay packers', 'houston texans', 'indianapolis colts', 'jacksonville jaguars', 'kansas city chiefs',
		'las vegas raiders', 'los angeles chargers', 'los angeles rams', 'miami dolphins', 'minnesota vikings', 'new england patriots', 'new orleans saints', 'new york giants',
		'new york jets', 'philadelphia eagles', 'pittsburgh steelers', 'san francisco 49ers', 'seattle seahawks', 'tampa bay buccaneers', 'tennessee titans', 'washington commanders'),
}
_MULTIWORD_NICKNAMES = ('blue jackets', 'red wings', 'maple leafs', 'golden knights')


def _nickname_event(event, sport):
	"""Canonicalize only recognized franchise names and their exact nicknames."""
	result = []
	for side in event:
		normalized = ' '.join(re.findall(r'[^\W_]+', side))
		matches = []
		for franchise in _FRANCHISES[sport]:
			nickname = next((name for name in _MULTIWORD_NICKNAMES if franchise.endswith(' ' + name)), franchise.rsplit(' ', 1)[-1])
			if normalized in (franchise, nickname): matches.append(nickname)
		if len(matches) != 1: return event
		result.append(matches[0])
	return tuple(sorted(result)) if result[0] != result[1] else event


def _event(name):
	match = _EVENT.fullmatch(name)
	if not match: return None
	teams = tuple(' '.join(side.casefold().split()) for side in match.groups())
	if teams[0] == teams[1] or not all(re.search(r'[^\W\d_]', team) for team in teams): return None
	return teams


def groups(streams, sport, now=None, *, evidence=None, matches=None, soccer_event=None, scope=None):
	"""Return live event feeds; optional callbacks identify mislabeled PPV feeds.

	Evidence is the set returned by the existing catalog evidence helper. All
	callbacks are supplied together, keeping this module independent of menus.
	"""
	if sport not in _SPORT_FIELDS: raise ValueError('Unknown sport')
	if not isinstance(streams, list): return {}
	today = (now or datetime.now().astimezone()).date()
	fixtures = evidence(streams, today) if evidence and matches and soccer_event and scope else set()
	allowed = {'nhl'} if sport == 'hockey' else {'nfl', 'ncaa football'}
	fixtures = {fixture for fixture in fixtures if fixture[0] in allowed}
	result = {}
	for stream in streams:
		if not isinstance(stream, dict): continue
		stream_id = str(stream.get('stream_id') or '')
		name = stream.get('name')
		if not stream_id.isdecimal() or not isinstance(name, str): continue
		parts = [part.strip() for part in name.split('|')]
		if len(parts) < 3 or parts[0].casefold() != 'live': continue
		if re.search(r'\b(?:end|ended|upcoming|replay|delayed)\b', ' '.join(parts[2:]), re.I): continue
		event = _event(parts[1])
		if not event: continue
		explicit = any(_SPORT_FIELDS[sport].fullmatch(part) for part in parts[2:])
		if not explicit:
			candidate = soccer_event(name) if soccer_event else None
			if _METADATA_DATE.search(' | '.join(parts[2:])) or not candidate or not matches(candidate, fixtures): continue
			event = candidate
		league_evidence = explicit and not any(re.fullmatch(r'NCAA\s+Football', part, re.I) for part in parts[2:])
		if not explicit: league_evidence = matches(event, {fixture for fixture in fixtures if fixture[0] == ('nhl' if sport == 'hockey' else 'nfl')})
		fixture_scope = scope(name) if scope else ''
		if sport == 'football' and not league_evidence: fixture_scope = '|'.join(filter(None, ('ncaa football', fixture_scope)))
		date = _METADATA_DATE.search(' | '.join(parts[2:]))
		if date and date.group().casefold() not in fixture_scope.casefold(): fixture_scope = '|'.join(filter(None, (fixture_scope, date.group().casefold())))
		key = (_nickname_event(event, sport) if league_evidence else event, fixture_scope)
		result.setdefault(key, {}).setdefault(stream_id, stream)
	return result
