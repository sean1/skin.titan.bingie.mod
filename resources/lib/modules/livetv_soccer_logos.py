"""Resolve verified soccer team names to bundled artwork without network access."""
import json
import os
import re
import unicodedata
from functools import lru_cache

from modules.livetv_fixtures import _team

_DIRECTORY = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'media', 'livetv', 'soccer')
_LEAGUES = {
	'english premier league': 'eng.1', 'premier league': 'eng.1', 'spanish la liga': 'esp.1', 'la liga': 'esp.1', 'primera division': 'esp.1',
	'german bundesliga': 'ger.1', 'bundesliga': 'ger.1', 'italian serie a': 'ita.1', 'serie a': 'ita.1', 'french ligue 1': 'fra.1', 'ligue 1': 'fra.1',
	'major league soccer': 'usa.1', 'mls': 'usa.1', 'usa mls': 'usa.1', 'us major league soccer': 'usa.1', 'american major league soccer': 'usa.1',
	'saudi pro league': 'ksa.1', 'saudi arabian league': 'ksa.1', 'saudi arabian pro league': 'ksa.1', 'roshn saudi league': 'ksa.1', 'pro league': 'ksa.1', 'saudi league': 'ksa.1',
	'canadian premier league': 'can.1', 'canada premier league': 'can.1', 'canadian championship': 'can.championship',
	'uefa champions league': 'uefa.champions', 'champions league': 'uefa.champions', 'uefa europa league': 'uefa.europa', 'europa league': 'uefa.europa',
	'uefa conference league': 'uefa.europa.conf', 'uefa europa conference league': 'uefa.europa.conf', 'concacaf champions league': 'concacaf.champions',
	'concacaf champions cup': 'concacaf.champions', 'concacaf champions league cup': 'concacaf.champions',
}
_ALIASES = {
	'ger.1': {'borussia m gladbach': '268'},
	'ita.1': {'roma': '104'},
	'usa.1': {'orlando city': '12011', 'dc united': '193', 'new york city': '17606', 'montreal': '9720', 'sporting kc': '186', 'st louis city': '21812', 'sj earthquakes': '191', 'chicago fire': '182'},
}


def _normalize(value):
	if not isinstance(value, str): return ''
	value = unicodedata.normalize('NFKD', value.casefold())
	return ' '.join(re.findall(r'[^\W_]+', ''.join(c for c in value if not unicodedata.combining(c))))


@lru_cache(maxsize=1)
def _catalog():
	try:
		with open(os.path.join(_DIRECTORY, 'teams.json'), encoding='utf-8') as source: data = json.load(source)
		return data if isinstance(data, list) else []
	except (OSError, ValueError, TypeError): return []


def team_logo(fixture, side):
	if not isinstance(fixture, dict) or side not in ('home', 'away'): return ''
	name = fixture.get(side)
	key = _normalize(name)
	if not key: return ''
	_, women, youth = _team(name)
	_, league_women, league_youth = _team(str(fixture.get('league') or ''))
	women, youth = women or league_women, youth | league_youth
	league = _LEAGUES.get(_normalize(fixture.get('league')))
	if not league: return ''
	alias_id = _ALIASES.get(league, {}).get(key)
	matches = {}
	for item in _catalog():
		if not isinstance(item, dict): continue
		identity = item.get('id')
		if not isinstance(identity, str) or not re.fullmatch(r'\d+', identity): continue
		if league not in item.get('leagues', []): continue
		aliases = [item.get('name'), item.get('short_name')]
		if isinstance(item.get('aliases'), list): aliases.extend(item['aliases'])
		if key not in {_normalize(alias) for alias in aliases} and identity != alias_id: continue
		_, candidate_women, candidate_youth = _team(item.get('name') or '')
		if (women, youth) != (candidate_women, candidate_youth): continue
		matches[identity] = True
	if len(matches) != 1: return ''
	filename = next(iter(matches)) + '.png'
	return 'livetv/soccer/' + filename if os.path.isfile(os.path.join(_DIRECTORY, filename)) else ''
