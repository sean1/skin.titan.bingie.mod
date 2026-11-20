"""Find conservatively matched F1 torrent candidates through an existing index."""
import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import unescape
from http.client import HTTPException
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from modules.f1_calendar import circuit_locality

_URL = 'https://apibay.org/q.php?'
_LIMIT = 2 * 1024 * 1024
_HASH = re.compile(r'^[0-9a-f]{40}$')
_F1 = re.compile(r'\b(?:f\s*1|formula\s*(?:1|one))\b')
_F1TV_EPISODE = re.compile(r'^formula\s+1\s+20\d{2}\s*x\s*[3-9]\d\s+(.+\b(?:f1tv|multi)\s+(?:4k\s+)?(?:hlg\s+)?(?:720p|1080p|2160p)\s+ss(?:\s+(?:mkv|mp4))?)$')
_INDY_EVENT = re.compile(r'\b(?:indianapolis|indy)\s*500\b')
_INDY_TRIALS = re.compile(r'\b(?:time\s+trials?|pole\s+day|bump\s+day)\b')
_INDY_TRIAL_SLOT = re.compile(r'\b(?:time\s+trials?|qualifying)\s+day\s+([1-6])\b')
_TRIAL_MONTHS = {month: index for index, month in enumerate(('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'), 1)}
_TRIAL_MONTH = '|'.join(_TRIAL_MONTHS)
_INDY_TRIAL_DATE = re.compile(rf'\b(?:19\d{{2}}\s+(\d{{2}})\s+(\d{{2}})|({_TRIAL_MONTH})\s+(\d{{1,2}})(?:st|nd|rd|th)?|(\d{{1,2}})(?:st|nd|rd|th)?\s+({_TRIAL_MONTH}))\b')
_INDY_TRIAL_RANGE = re.compile(rf'\b(?:day\s+[1-6]|(?:{_TRIAL_MONTH})\s+\d{{1,2}}(?:st|nd|rd|th)?|19\d{{2}}\s+\d{{2}}\s+\d{{2}})\s+(?:(?:and|or|to|through)\s+(?:day\s+)?)?\d{{1,2}}\b')
_INDY_WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
_INDY_WEEKDAY = re.compile(rf'\b(?:{"|".join(_INDY_WEEKDAYS)})\b')
_WARM_UP_LABEL = r'(?:warm\s*up|wu)'
_WARM_UP = re.compile(rf'\b{_WARM_UP_LABEL}\b')
_QUALIFYING_WARM_UP = re.compile(rf'(?<!sprint )\b(?:pre\s*)?qualifying\s+{_WARM_UP_LABEL}\b')
_SUNDAY_WARM_UP = re.compile(rf'\bsunday\s+{_WARM_UP_LABEL}\b')
_FAMILIARISATION_LABEL = r'(?:track\s+familiari[sz]ation|familiari[sz]ation\s+session)'
_PRE_QUALIFYING_NAME = re.compile(r'\bpre(?:\s*[-‐‑–]\s*)?qualif(?:ying|ication|y)\b', re.IGNORECASE)
_PRE_QUALIFYING = re.compile(r'\bprequalifying\b')
_PRE_QUALIFYING_EXTRAS = re.compile(r'\b(?:pre|post)\s+prequalifying\b|\bprequalifying\s+(?:reports?|results?|summar(?:y|ies)|show)\b')
_QUALIFYING_WEEKDAYS = {'QUAL_THURSDAY': 3, 'QUAL_FRIDAY': 4, 'QUAL_SATURDAY': 5, 'QUAL_SUNDAY': 6}
_WEEKDAY = r'(thursday|friday|saturday|sunday)'
_DAY_PERIOD = r'morning|afternoon|a\s*m|p\s*m'
_QUALIFYING_PERIODS = {'QUAL_MORNING', 'QUAL_AFTERNOON'}
_PRACTICE_LABEL = r'(?:free\s*)?practice(?:\s*session)?'
_FIRST_PRACTICE = r'(?:first|1st)'
_SECOND_PRACTICE = r'(?:second|2nd)'
_PRACTICE_ORDINAL = rf'(?:{_FIRST_PRACTICE}|{_SECOND_PRACTICE})'
_DAILY_PRACTICE_LABEL = rf'(thursday|friday|saturday)\s+({_PRACTICE_ORDINAL})\s+{_PRACTICE_LABEL}'
_DAILY_PRACTICE_SLOT = re.compile(rf'\b{_DAILY_PRACTICE_LABEL}\b')
_DAILY_PRACTICE_WEEKDAYS = {3: 'THURSDAY', 4: 'FRIDAY', 5: 'SATURDAY'}
_DAILY_PRACTICE_SLOTS = {f'FP_SLOT_{day}_{ordinal}' for day in _DAILY_PRACTICE_WEEKDAYS.values() for ordinal in ('FIRST', 'SECOND')}
_AGGREGATE_PRACTICE_DAY = re.compile(rf'\b{_WEEKDAY}\s+{_PRACTICE_LABEL}\b(?!\s+(?:0?[1-4]|one|two|three|four)\b)')
_PRACTICE_2003_LABELS = {
	'FP_THURSDAY': rf'thursday\s+(?:{_PRACTICE_LABEL}|fp\s*0?[1-4]|0?[1-4]\s*practice)',
	'FP_FRIDAY': rf'friday\s+(?:{_PRACTICE_LABEL}|fp\s*0?[1-4]|0?[1-4]\s*practice)',
	'FP_SATURDAY_FIRST': rf'(?:saturday\s+{_FIRST_PRACTICE}|{_FIRST_PRACTICE}\s+saturday)\s+{_PRACTICE_LABEL}',
	'FP_SATURDAY_SECOND': rf'(?:saturday\s+{_SECOND_PRACTICE}|{_SECOND_PRACTICE}\s+saturday)\s+{_PRACTICE_LABEL}',
	'FP_SATURDAY_BOTH': rf'saturday\s+{_FIRST_PRACTICE}\s+and\s+{_SECOND_PRACTICE}\s+{_PRACTICE_LABEL}',
	'FP_SATURDAY_NUMBERED': rf'saturday\s+(?:(?:{_PRACTICE_LABEL}|fp)\s*0?[1-4]|{_PRACTICE_LABEL}\s+(?:one|two|three|four)|0?[1-4]\s*practice)',
}
_PRACTICE_2003_SATURDAY = {'FP_SATURDAY_FIRST', 'FP_SATURDAY_SECOND', 'FP_SATURDAY_BOTH'}
_PRACTICE_2003_PATTERNS = {code: re.compile(rf'\b{label}\b') for code, label in _PRACTICE_2003_LABELS.items()}
_PRACTICE_2003_DAY_LABEL = re.compile(rf'\b(?:{"|".join(_PRACTICE_2003_LABELS.values())})\b')
_PRACTICE_ORDINAL_UNSCOPED = re.compile(rf'\b{_PRACTICE_ORDINAL}\s+{_PRACTICE_LABEL}\b')
_SPANISH_PRACTICE = r'(?:(?:entrenamientos?|practicas?)\s+)?libres'
_SPANISH_QUALIFYING = r'clasificac?ion'
_PORTUGUESE_PRACTICE = r'treinos?\s+livres?'
_PORTUGUESE_RACE = r'corrida(?!\s+(?:de\s+)?sprint\b)'
_PORTUGUESE_QUALIFYING = r'classificacao'
_PORTUGUESE_SPRINT_QUALIFYING = re.compile(rf'\b{_PORTUGUESE_QUALIFYING}\s+sprint\b(?:\s+(?:(?:fase|parte|sessao)\s+)?(?:sq\s*)?0?([123])\b)?')
_FRENCH_QUALIFYING = r'essais\s+qualificatifs'
_FRENCH_RACE = r'(?:la\s+grille\s+course\s+podium|la\s+course\s+grand\s+prix)'
_CZECH_RACE = r'hlavni\s+zavod'
_LANGUAGE_NAMES = r'(?:castellano|cestina|czech|deutsch|english|espanol|francais|french|german|italian|italiano|portuguese|portugues|slovak|slovencina|spanish)'
_LANGUAGE_TAG = re.compile(rf'\[\s*(?:{_LANGUAGE_NAMES}|cze|eng|esp|fra|ger|ita|por|slk)\s*\]')
_SEASON_LANGUAGE_SUFFIX = re.compile(rf'(\b(?:complete|full)\s+season)\s+{_LANGUAGE_NAMES}$')
_PACK_EXTRAS = re.compile(
	rf'\b(?:no\s+qualif(?:ying|ication)|temps\s+forts|resumes?|debrief(?:ing)?|avant\s+course|apres\s+course|(?:avant|apres)\s+(?:la\s+course|les\s+{_FRENCH_QUALIFYING})|course\s+en\s+\d{{1,3}}|{_FRENCH_QUALIFYING}\s+(?:du\s+)?sprint)\b|'
	rf'\b(?:(?:pre|pos|post|(?:antes|depois)\s+d[ao])\s+(?:corrida|{_PORTUGUESE_PRACTICE}|{_PORTUGUESE_QUALIFYING})|corrida(?:\s+sprint)?\s+em\s+\d{{1,3}}|analises?|melhores\s+momentos|resumos?|resultados?\s+d[ao]\s+(?:corrida|{_PORTUGUESE_PRACTICE}|{_PORTUGUESE_QUALIFYING}))\b|'
	rf'\b(?:pre|post)\s*{_WEEKDAY}(?:\s+(?:{_DAY_PERIOD}))?\s+qual(?:ifying|ification|i)?\b|'
	rf'\b(?:pre|post)\s*{_DAILY_PRACTICE_LABEL}\b|'
	rf'\b(?:pre|post)\s*(?:thursday|friday|saturday)\s+{_PRACTICE_LABEL}\b|'
	rf'\b(?:pre|post)\s*(?:{"|".join(_PRACTICE_2003_LABELS.values())})\b|'
	r'\b(?:highlights?|recaps?|previews?|interviews?|analysis|review|documentary|drive to survive|onboard|on board|build\s*up|press\s+conferences?|paddock\s+uncut|season\s+launch|the\s+f\s*1\s+show|samples?|resumen(?:es)?|previ[ao]s?|entrevistas?|analisis|sestrihy?)\b'
	r'|\b(?:ted\s*s\s+(?:(?:qualifying|race|sprint)\s+)?notebook|ruedas?\s+de\s+prensa)\b'
	r'|^(?:pre|post)\s+(?:f\s*1|formula\s*(?:1|one))\b'
	rf'|\b(?:pre|post)\s*(?:race|qualifying|qualification|quali|qual|(?:free\s*)?practice|0?[1-4]\s*practice|sprint|shootout|fp\s*0?[1-4]|s?q\s*0?[123]|(?:first|second|third|1st|2nd|3rd)\s+qual(?:ifying|ification|i)?|{_WARM_UP_LABEL}|{_FAMILIARISATION_LABEL}|show)\b'
	rf'|\b(?:pre|post|(?:antes|despues)\s+de(?:l|\s+l(?:a|os))?)\s*(?:{_SPANISH_PRACTICE}|{_SPANISH_QUALIFYING}|carrera|{_CZECH_RACE})\b'
	rf'|\b(?:{_SPANISH_QUALIFYING}\s+(?:(?:de\s+la|del|para(?:\s+la)?)\s+)?(?:carrera\s+)?sprint|sprint\s+(?:{_SPANISH_QUALIFYING}|{_CZECH_RACE})|{_CZECH_RACE}\s+sprint)\b'
	rf'|\b(?:(?:race|sprint|qualifying|shootout)\s+in\s+\d{{1,3}}|race\s+(?:starts?|radio|reports?|summar(?:y|ies))|starting\s+grid|grid\s+walk|(?:{_WARM_UP_LABEL}|formation|parade)\s+laps?)\b')
_SUPPORT_SERIES = re.compile(r'\b(?:f\s*1\s*academy|formula\s*(?:1|one)\s*academy|f\s*[234]|formula\s*[234]|formula\s+(?:two|three|four)|gp\s*[23])\b')
_PACK_REJECT = re.compile(_PACK_EXTRAS.pattern + '|' + _SUPPORT_SERIES.pattern)
_FILE_REJECT = re.compile(_PACK_REJECT.pattern + r'|\b(?:data\s*channel|driver\s*tracker)\b'
	r'|\bsprint\s+(?:weekend|pack|sessions?)\b|\b(?:complete|full)\s+(?:race\s+)?(?:weekend|season)\b|\b(?:weekend|season)\s+(?:pack|collection)\b')
_QUALIFYING = rf'(?:qualifying|qualification|quali|qual|{_SPANISH_QUALIFYING}|{_FRENCH_QUALIFYING}|{_PORTUGUESE_QUALIFYING})'
_SPRINT_QUALIFYING = re.compile(rf'\bsprint\s+(?:race\s+)?(?:{_QUALIFYING}|qualify|shootout)\b(?:\s+(?:(?:session|stage|part)\s+)?(?:(?:sq|q)\s*)?0?([123])\b)?')
_SPRINT_WEEKEND_INVENTORY = re.compile(r'\b(?:free\s+)?practice(?:\s+1)?\s*[,.]\s*sprint\s+(?:qualifying|qualification|shootout)\s*[,.]\s*sprint(?:\s+race)?\s*[,.]\s*(?:qualifying|qualification)\s*[,.]\s*race\b')
_WEEKEND_STAGE = re.compile(r'(?:^|/)\s*stage\s+(\d{1,3})\s*(?=/|$)')
_WEEKDAY_QUALIFYING = re.compile(rf'\b(?:{_WEEKDAY}(?:\s+({_DAY_PERIOD}))?\s+and\s+)?{_WEEKDAY}(?:\s+({_DAY_PERIOD}))?\s+{_QUALIFYING}\b(?:\s+(?:practice\s+)?({_DAY_PERIOD})\b)?')
_DAY_PERIODS = re.compile(rf'\b(?:{_DAY_PERIOD})\b')
_SPRINT_QUALIFYING_PHASE = re.compile(r'\bsq\s*0?([123])\b')
_QUALIFYING_PHASE = re.compile(rf'\b(?:{_QUALIFYING}\s+(?:phase|stage|part)\s*0?[123]|q\s*0?[123]\s+(?:phase|stage|part))\b')
_NUMBERED_QUALIFYING = re.compile(
	rf'\b(?:{_QUALIFYING}(?:\s+(?:session|day))?(?:\s*(?:q\s*)?0?([123])|\s+(one|two|three))'
	rf'|q\s*0?([123])(?:\s+{_QUALIFYING})?|(first|second|third|1st|2nd|3rd)\s+{_QUALIFYING})\b')
_QUALIFYING_NUMBERS = {'first': '1', 'one': '1', '1st': '1', 'second': '2', 'two': '2', '2nd': '2', 'third': '3', 'three': '3', '3rd': '3'}
_QUALIFYING_PAIR = re.compile(rf'\b{_QUALIFYING}(?:\s+sessions?)?\s+0?1\s+and\s+0?2\b(?:\s+and\s+0?(3)\b)?')
_PRACTICE_NUMBERS = {'1': 'one', '2': 'two', '3': 'three', '4': 'four'}
_PRACTICE_PAIR = re.compile(rf'\b(?:{_PRACTICE_LABEL}|{_SPANISH_PRACTICE}|{_PORTUGUESE_PRACTICE})\s*0?([1-4])\s+(?:and|y|e)\s+0?([1-4])\b')
_SESSION_PATTERNS = {
	'WU': _WARM_UP.pattern,
	'FAM': rf'\b{_FAMILIARISATION_LABEL}\b',
	'SR': r'\bsprint\b(?!\s+(?:qualifying|qualification|quali|qual|shootout|weekend|pack|sessions?)\b)',
	'QUAL': rf'(?<!sprint )\b{_QUALIFYING}\b',
	'RACE': rf'(?<!sprint )\b(?:race|{_CZECH_RACE}|{_PORTUGUESE_RACE}|{_FRENCH_RACE})\b',
}
_PRACTICE_PATTERNS = {f'FP{n}': re.compile(rf'\b(?:fp\s*0?{n}|{_PRACTICE_LABEL}(?:\s*0?{n}|\s+{word})|0?{n}\s*practice|(?:{_SPANISH_PRACTICE}|{_PORTUGUESE_PRACTICE})\s*0?{n})\b') for n, word in _PRACTICE_NUMBERS.items()}
_SESSIONS = _SESSION_PATTERNS.keys() | _PRACTICE_PATTERNS.keys() | {'SS', 'Q1', 'Q2', 'Q3', 'PQ', 'TT'}


def _historical_session_allowed(code: str, season: str) -> bool:
	if code == 'PQ': return season == '1965' or len(season) == 4 and '1977' <= season <= '1992'
	return code not in ('FP4', 'WU', 'Q1', 'Q2') or len(season) == 4 and '1950' <= season <= '2005'


def _fixture_year(fixture):
	date = fixture.get('date')
	if not isinstance(date, str) or not re.match(r'^\d{4}-', date): return ''
	if fixture.get('session_code') == 'PQ' and fixture.get('historical_pre_qualifying') is True and fixture.get('race_season') == '1965' and date[:10] == '1964-12-29': return '1965'
	return date[:4]


def _qatar_familiarisation(fixture):
	return fixture['date'][:10] == '2023-10-07' and (fixture.get('event_id') == '600026765' or _alias(fixture.get('race_name')) == 'qatar')


def _fixture_session_allowed(fixture):
	code = fixture.get('session_code')
	if code == 'FAM': return _qatar_familiarisation(fixture)
	year = _fixture_year(fixture)
	if code == 'TT': return '1950' <= year <= '1960' and type(fixture.get('indy_trial_day')) is int and _INDY_EVENT.search(_text(fixture.get('event'))) is not None
	if code == 'Q3': return fixture.get('qualifying_practice') == 3 and len(year) == 4 and '1950' <= year <= '1975'
	if code == 'PQ' and (fixture.get('historical_pre_qualifying') is not True or year == '1965' and fixture['date'][:10] != '1964-12-29'): return False
	return _historical_session_allowed(code, year)


def _practice_2003_matches(markers, codes, fixture):
	if _fixture_year(fixture) != '2003' or fixture.get('practice_format_2003') not in ('combined', 'split'): return False
	code = fixture.get('session_code')
	if codes - markers - {code}: return False
	if code == 'FP1':
		try: weekday = datetime.fromisoformat(fixture.get('calendar_day') or fixture['date'][:10]).weekday()
		except (TypeError, ValueError): return False
		return (weekday == 3 and markers == {'FP_THURSDAY'}) or (weekday == 4 and markers == {'FP_FRIDAY'})
	if code not in ('FP2', 'FP3'): return False
	if not codes - markers and markers in ({'FP_SATURDAY_BOTH'}, {'FP_SATURDAY_FIRST', 'FP_SATURDAY_SECOND'}): return True
	return markers == ({'FP_SATURDAY_FIRST'} if code == 'FP2' else {'FP_SATURDAY_SECOND'})


def _session_matches(codes, fixture):
	code = fixture.get('session_code')
	if code == 'QUAL' and (portion := fixture.get('qualifying_portion')):
		if portion == 'q1': return codes in ({'Q1'}, {'QUAL_SATURDAY'}, {'QUAL_SATURDAY', 'Q1'})
		if portion == 'q2-q3': return codes in ({'Q2', 'Q3'}, {'QUAL_SUNDAY'}, {'QUAL_SUNDAY', 'Q2', 'Q3'})
		return codes == {'QUAL'}
	if code == 'TT':
		identity = fixture.get('indy_trial_day')
		weekdays = {marker for marker in codes if marker.startswith('TT_WEEKDAY_')}
		markers = codes - weekdays - {'TT', 'QUAL'}
		valid = {'TT_DATE_' + fixture['date'][5:10].replace('-', ''), 'TT_SLOT_%s' % identity}
		if identity == fixture.get('indy_last_trial_day'): valid.add('TT_BUMP')
		weekday = 'TT_WEEKDAY_' + _INDY_WEEKDAYS[datetime.fromisoformat(fixture['date'][:10]).weekday()]
		return 'TT' in codes and bool(markers) and markers <= valid and (not weekdays or weekdays == {weekday})
	practice_slots = codes.intersection(_PRACTICE_2003_PATTERNS)
	if practice_slots: return _practice_2003_matches(practice_slots, codes, fixture)
	if fixture.get('split_daily_practice') is True and code in _PRACTICE_PATTERNS:
		weekday = _DAILY_PRACTICE_WEEKDAYS.get(fixture.get('practice_weekday'))
		ordinal = 'FIRST' if code in ('FP1', 'FP3') else 'SECOND'
		return bool(weekday) and codes == {f'FP_SLOT_{weekday}_{ordinal}'}
	if fixture.get('aggregate_daily_practice') is True:
		weekday = _DAILY_PRACTICE_WEEKDAYS.get(fixture.get('practice_weekday'))
		if not weekday or code not in ('FP1', 'FP2'): return False
		day = {'FP_DAY_' + weekday}
		pair = {'FP1', 'FP2'} if code == 'FP1' else {'FP3', 'FP4'}
		return codes in (day, pair, day | pair)
	if code in ('FP2', 'FP3') and _fixture_year(fixture) == '2003' and fixture.get('practice_format_2003') == 'combined': return False
	weekdays = codes.intersection(_QUALIFYING_WEEKDAYS)
	period = fixture.get('qualifying_period')
	if weekdays:
		if len(weekdays) != 1: return False
		periods = codes.intersection(_QUALIFYING_PERIODS)
		if len(periods) > 1 or period and periods != {'QUAL_' + period.upper()}: return False
		other_codes = codes - weekdays - periods
		weekday = fixture.get('qualifying_weekday')
		selected_day = _QUALIFYING_WEEKDAYS[next(iter(weekdays))]
		if code == 'QUAL': return not other_codes and (weekday is None or weekday == selected_day)
		return code in ('Q1', 'Q2', 'Q3') and type(weekday) is int and weekday == selected_day and not other_codes - {code} and _fixture_session_allowed(fixture)
	if period or code == 'Q2' and fixture.get('qualifying_practice') == 3: return False
	if codes == {'QUALIFYING_PAIR'}:
		return code in ('Q1', 'Q2') and fixture.get('split_qualifying') is True and _fixture_session_allowed(fixture)
	if codes == {'FREE_PRACTICE'}:
		return code == 'FP1' and fixture.get('single_practice') is True and _fixture_year(fixture) >= '2023'
	if 'FAM' in codes:
		return _qatar_familiarisation(fixture) and code in codes and codes in ({'FAM'}, {'FAM', 'SS'})
	return codes == {code}


def _recording_rejected(text: str, pattern: re.Pattern, *, code: str, season: str) -> bool:
	if _QUALIFYING_PHASE.search(text): return True
	if code == 'TT' and re.search(r'\b(?:clips?|laps?\s+in\s+(?:time\s+trials?|qualifying))\b', text): return True
	if season == '2003':
		text = _QUALIFYING_WARM_UP.sub('warm up', text)
		if _SUNDAY_WARM_UP.search(text): return True
	if _PRE_QUALIFYING.search(text):
		if code != 'PQ' or not _historical_session_allowed(code, season) or _PRE_QUALIFYING_EXTRAS.search(text): return True
		text = _PRE_QUALIFYING.sub(' ', text)
	return bool(pattern.search(text) or (_WARM_UP.search(text) and (code != 'WU' or not _historical_session_allowed(code, season))))


def _session_codes(normalized_text: str, *, season: str = '') -> set[str]:
	codes = set()
	trial_text = ''
	if '1950' <= season <= '1960' and (_INDY_TRIALS.search(normalized_text) or _INDY_EVENT.search(normalized_text) and re.search(r'\bqualif(?:ying|ications?)\b', normalized_text)):
		trial_text = normalized_text
		normalized_text = _INDY_TRIAL_SLOT.sub(' ', normalized_text)
	if season == '2003': normalized_text = _QUALIFYING_WARM_UP.sub('warm up', normalized_text)
	if _PRE_QUALIFYING.search(normalized_text): codes.add('PQ')
	normalized_text = _PRE_QUALIFYING.sub(' ', normalized_text)
	for phase in _PORTUGUESE_SPRINT_QUALIFYING.findall(normalized_text):
		codes.add('SQ' + phase if phase else 'SS')
	normalized_text = _PORTUGUESE_SPRINT_QUALIFYING.sub(' ', normalized_text)
	for match in _SPRINT_QUALIFYING.finditer(normalized_text):
		phase = match.group(1)
		if phase: codes.add('SQ' + phase)
		elif season == '2021' and match.group() == 'sprint qualifying': codes.add('SR')
		else: codes.add('SS')
	normalized_text = _SPRINT_QUALIFYING.sub(' ', normalized_text)
	if season == '2023' and re.search(r'\bshootout\b(?!\s+(?:(?:session|stage|part)\s+)?0?[123]\b)', normalized_text): codes.add('SS')
	codes.update('SQ' + phase for phase in _SPRINT_QUALIFYING_PHASE.findall(normalized_text))
	for first, first_period, day, period, trailing_period in _WEEKDAY_QUALIFYING.findall(normalized_text):
		codes.update('QUAL_' + value.upper() for value in (first, day) if value)
		if '1950' <= season <= '1975' and (first_period or period or trailing_period):
			codes.update('QUAL_MORNING' if value.replace(' ', '') in ('am', 'morning') else 'QUAL_AFTERNOON' for value in _DAY_PERIODS.findall(normalized_text))
	for third in _QUALIFYING_PAIR.findall(normalized_text):
		codes.add('QUALIFYING_PAIR')
		if third: codes.add('Q3')
	normalized_text = _QUALIFYING_PAIR.sub(' ', normalized_text)
	for match in _NUMBERED_QUALIFYING.finditer(normalized_text):
		number = next(value for value in match.groups() if value)
		codes.add('Q' + _QUALIFYING_NUMBERS.get(number, number))
	if '1950' <= season <= '1975' and codes.intersection({'Q1', 'Q2', 'Q3'}):
		codes.update('QUAL_' + day.upper() for day in re.findall(rf'\b{_WEEKDAY}\b', normalized_text))
	normalized_text = _NUMBERED_QUALIFYING.sub(' ', normalized_text)
	normalized_text = _WEEKDAY_QUALIFYING.sub(' ', normalized_text)
	if '1998' <= season <= '2002':
		for day, ordinal in _DAILY_PRACTICE_SLOT.findall(normalized_text):
			codes.add('FP_SLOT_' + day.upper() + ('_FIRST' if ordinal in ('first', '1st') else '_SECOND'))
		normalized_text = _DAILY_PRACTICE_SLOT.sub(' ', normalized_text)
		if _PRACTICE_ORDINAL_UNSCOPED.search(normalized_text): codes.add('FP_ORDINAL_UNSCOPED')
	if season == '2003':
		codes.update(code for code, pattern in _PRACTICE_2003_PATTERNS.items() if pattern.search(normalized_text))
		if _PRACTICE_ORDINAL_UNSCOPED.search(_PRACTICE_2003_DAY_LABEL.sub(' ', normalized_text)): codes.add('FP_ORDINAL_UNSCOPED')
	for pair in _PRACTICE_PAIR.findall(normalized_text):
		codes.update('FP' + number for number in pair)
	normalized_text = _PRACTICE_PAIR.sub(' ', normalized_text)
	for code, pattern in _PRACTICE_PATTERNS.items():
		if pattern.search(normalized_text): codes.add(code)
		normalized_text = pattern.sub(' ', normalized_text)
	if re.search(rf'\b(?:free\s+practice|{_PORTUGUESE_PRACTICE})\b', normalized_text) and codes.isdisjoint(_PRACTICE_PATTERNS.keys() | _PRACTICE_2003_PATTERNS.keys() | {'FP_ORDINAL_UNSCOPED'}): codes.add('FREE_PRACTICE')
	codes.update(code for code, pattern in _SESSION_PATTERNS.items() if re.search(pattern, normalized_text))
	if trial_text:
		codes.add('TT')
		codes.update('TT_SLOT_' + number for number in _INDY_TRIAL_SLOT.findall(trial_text))
		if 'pole day' in trial_text: codes.add('TT_SLOT_1')
		if 'bump day' in trial_text or 'final day' in trial_text: codes.add('TT_BUMP')
		if _INDY_TRIAL_RANGE.search(trial_text): codes.add('TT_RANGE')
		codes.update('TT_WEEKDAY_' + value for value in _INDY_WEEKDAY.findall(trial_text))
		codes.update('TT_DATE_' + value for value in _indy_dates(trial_text))
	return codes


def _codes_for_fixture(text, fixture, year):
	if fixture.get('aggregate_daily_practice') is True:
		days = {'FP_DAY_' + day.upper() for day in re.findall(rf'\b{_WEEKDAY}\b', text)}
		return _session_codes(_AGGREGATE_PRACTICE_DAY.sub(' ', text), season=year) | days
	codes = _session_codes(text, season=year)
	if fixture.get('qualifying_portion') and codes.intersection(_QUALIFYING_WEEKDAYS.keys() | {'QUAL', 'Q1', 'Q2', 'Q3'}):
		days = {'QUAL_' + day.upper() for day in re.findall(rf'\b{_WEEKDAY}\b', text)}
		if days: return (codes - {'QUAL'}) | days
	return codes


class SearchUnavailable(Exception):
	"""The search index did not return a usable response."""


def _unaccented(value):
	value = unicodedata.normalize('NFKD', value.casefold())
	return ''.join(c for c in value if not unicodedata.combining(c))


def _text(value):
	if not isinstance(value, str) or len(value) > 2000: return ''
	value = _LANGUAGE_TAG.sub(' ', _unaccented(value))
	value = ' '.join(re.sub(r'[^a-z0-9]+', ' ', value).split())
	return re.sub(r'\bcaesar s palace\b', 'caesars palace', value)


def _session_text(value):
	return _text(_PRE_QUALIFYING_NAME.sub('prequalifying', value.replace('&', ' and '))) if isinstance(value, str) else ''


def _contains(text, phrase):
	return bool(phrase and re.search(r'(?<!\w)' + re.escape(phrase).replace(r'\ ', r'\s+') + r'(?!\w)', text))


def _conflicting_year(text, year):
	# Frame dimensions like 1920x1080 are not references to another season.
	text = re.sub(r'\b(?:19|20)\d{2}\s*x\s*(?:720|1080|1200|1440|2160|4320)\b', ' ', text)
	return any(value != year for value in re.findall(r'(?<!\d)((?:19|20)\d{2})(?!\d)', text))


def _indy_dates(text):
	return {'%02d%02d' % (int(month) if month else _TRIAL_MONTHS[month_first or month_second], int(day or day_first or day_second))
		for month, day, month_first, day_second, day_first, month_second in _INDY_TRIAL_DATE.findall(text)}


def _indy_race_date_conflicts(text, fixture):
	if fixture.get('session_code') != 'RACE' or not '1950' <= _fixture_year(fixture) <= '1960' or not _INDY_EVENT.search(_text(fixture.get('event'))): return False
	return bool(_indy_dates(text) - {fixture['date'][5:10].replace('-', '')})


def _contains_race(text, phrase):
	"""Accept verified aliases with joined words or an attached GP suffix."""
	if not phrase: return False
	variants = {phrase, phrase.replace(' ', '')}
	return any(re.search(r'(?<!\w)' + re.escape(value).replace(r'\ ', r'\s+') + r'(?:gp)?(?!\w)', text) for value in variants)


def _alias(value):
	return re.sub(r'\s+(?:grand prix|gp)(?:\s+in\s+.+)?$', '', _text(value))


def _valid_alias(value):
	return len(value) >= 4 or value == 'spa'


def _localities(aliases):
	return {circuit_locality(value) for value in aliases} - {''}


def _other_names(aliases):
	"""Include distinctive place names from explicitly named local circuits."""
	names = {_alias(value) for value in aliases} | _localities(aliases)
	if 'british' in names: names.add('great britain')
	return names


def _race_names(fixture, aliases):
	names = {_alias(value) for value in aliases + [fixture.get('event'), fixture.get('race_name')]} | _localities(aliases)
	# Jolpica's confirmed British GP is also commonly called Great Britain.
	if _text(fixture.get('race_name')) in ('british grand prix', 'british gp'): names.add('great britain')
	return names


_GRAND_PRIX = re.compile(r'\b(?:grand prix|gp)\b')


def _event_title_spans(text, name):
	if not _GRAND_PRIX.sub('', name).strip(): return
	if _GRAND_PRIX.search(name):
		titles = [_GRAND_PRIX.sub(label, name) for label in ('grand prix', 'gp')]
	else:
		titles = [name + ' ' + label for label in ('grand prix', 'gp')]
	variants = [r'\s*'.join(re.escape(word) for word in title.split()) for title in titles]
	for match in re.finditer(r'(?<!\w)(?:' + '|'.join(variants) + r')(?!\w)', text):
		yield match.span()


def _event_conflicts(text, fixture):
	aliases = fixture.get('aliases') or []
	others = fixture.get('other_event_aliases') or []
	names = _race_names(fixture, aliases)
	own_titles = {_alias(value) for value in (fixture.get('event_names') or [fixture.get('event')]) + [fixture.get('race_name')]}
	rivals = fixture.get('other_event_names') or [value for value in others if _text(value) != _alias(value)]
	rival_spans = [span for rival in map(_alias, rivals) if rival not in own_titles for span in _event_title_spans(text, rival)]
	if rival_spans:
		own_spans = [span for name in own_titles for span in _event_title_spans(text, name)]
		if any(not any(own_start <= start and end <= own_end and (own_start, own_end) != (start, end) for own_start, own_end in own_spans) for start, end in rival_spans): return True
	return any(_valid_alias(name) and not any(_contains(own, name) for own in names) and _contains_race(text, name) for name in _other_names(others))


def event_name_matches(text, fixture):
	"""Recognize a named event without relying on a round number."""
	return _event_matches(text, fixture, require_name=True)


def _event_matches(text, fixture, folder_rounds=(), *, require_name=False):
	if _event_conflicts(text, fixture): return False
	aliases = fixture.get('aliases') or []
	other_names = _other_names(fixture.get('other_event_aliases') or [])
	names = {name for name in _race_names(fixture, aliases) if _valid_alias(name)}
	unique = names - other_names
	event_names = [fixture['race_name']] if fixture.get('race_name') else fixture.get('event_names') or [fixture.get('event')]
	if any(_contains_race(text, name) for name in unique) or any(any(_event_title_spans(text, _alias(name))) for name in event_names): return True
	if require_name: return False
	usa = _alias(fixture.get('race_name')) == 'united states' and _contains(text, 'usa')
	if not usa and re.search(r'\bgrand prix\b|\b[a-z0-9]*gp\b', text) and not any(_contains_race(text, name) for name in names): return False
	rounds = _explicit_rounds(text) or folder_rounds
	if usa and not rounds: rounds = _round_numbers(text)
	return bool(rounds and _rounds_agree(rounds, fixture, require_known=True))


def _explicit_rounds(text):
	rounds = re.findall(r'\b(?:round|rd|r)\s*(\d{1,3})\b', text)
	rounds += re.findall(r'\b(?:f\s*1|formula\s*(?:1|one))\s+(?:19|20)\d{2}\s+(\d{1,2})gp\b', text)
	return [int(value) for value in rounds]


def _round_numbers(text):
	explicit = _explicit_rounds(text)
	if explicit: return explicit
	if episode := _F1TV_EPISODE.fullmatch(text): text = episode[1]
	return [int(value) for value in re.findall(r'(?<!\d)(?:19|20)\d{2}\s*x\s*(\d{1,3})(?!\d)', text) if int(value) <= 99]


def _rounds_agree(numbers, fixture, require_known=False, *, event_named=False):
	if not numbers: return True
	expected = fixture.get('race_round')
	if isinstance(expected, bool) or not isinstance(expected, int) or not 1 <= expected <= 99: return not require_known
	allowed = (expected, *fixture.get('round_aliases', ())) if event_named else fixture.get('round_only', (expected,))
	return all(number in allowed for number in numbers)


def _match(name, fixture):
	text = _text(name)
	code = fixture.get('session_code')
	year = _fixture_year(fixture)
	if code not in _SESSIONS or not year: return False
	if not _fixture_session_allowed(fixture): return False
	session_text = _session_text(name)
	series = _INDY_EVENT if code == 'TT' else _F1
	if not re.search(r'(?<!\d)' + year + r'(?!\d)', text) or not series.search(text) or _recording_rejected(session_text, _FILE_REJECT, code=code, season=year): return False
	if _conflicting_year(text, year) or _indy_race_date_conflicts(text, fixture): return False
	event_named = bool(fixture.get('round_aliases')) and event_name_matches(text, fixture)
	if not _rounds_agree(_round_numbers(text), fixture, event_named=event_named): return False
	needs_name = fixture.get('qualifying_portion') in ('q1', 'q2-q3') or code == 'PQ' and year == '1965'
	return _session_matches(_codes_for_fixture(session_text, fixture, year), fixture) and _event_matches(text, fixture, require_name=needs_name)


def pack_type(name, fixture):
	"""Identify recording bundles and listings that require file verification."""
	if not isinstance(fixture, dict): return ''
	text, year, code = _text(name), _fixture_year(fixture), fixture.get('session_code')
	if not text or code not in _SESSIONS or not year: return ''
	if not _fixture_session_allowed(fixture): return ''
	session_text = _session_text(name)
	if year == '2003':
		session_text = _QUALIFYING_WARM_UP.sub('warm up', session_text)
		if _SUNDAY_WARM_UP.search(session_text): return ''
	if not re.search(r'(?<!\d)' + year + r'(?!\d)', text) or not (_F1.search(text) or code == 'TT' and _INDY_EVENT.search(text)): return ''
	if (_recording_rejected(session_text, _PACK_REJECT, code=code, season=year) if code == 'TT' or _PRE_QUALIFYING.search(session_text) else _PACK_REJECT.search(session_text)): return ''
	if _WARM_UP.search(text) and not _historical_session_allowed('WU', year): return ''
	if _conflicting_year(text, year): return ''
	if year >= '2023' and _SPRINT_WEEKEND_INVENTORY.search(raw_title := _unaccented(name)):
		if code not in {'FP1', 'SS', 'SR', 'QUAL', 'RACE'} or not event_name_matches(text, fixture): return ''
		rounds = _round_numbers(text) + [int(stage) for stage in _WEEKEND_STAGE.findall(raw_title)]
		return 'weekend' if _rounds_agree(rounds, fixture, require_known=True, event_named=True) else ''
	season = re.search(rf'\b(?:season\s*{year}|{year}\s*(?:(?:f\s*1|formula\s*(?:1|one))\s+)?(?:complete\s+|full\s+)?season)\b', text)
	if season:
		# A season bundle must not be a race-specific release with a season tag.
		season_title = _SEASON_LANGUAGE_SUFFIX.sub(r'\1', text)
		if _event_matches(season_title, fixture) or _event_conflicts(season_title, fixture): return ''
		return 'season'
	selected = _event_matches(text, fixture, require_name=code == 'PQ' and year == '1965')
	rounds = _round_numbers(text)
	event_named = bool(fixture.get('round_aliases')) and event_name_matches(text, fixture)
	if not selected or not _rounds_agree(rounds, fixture, require_known=True, event_named=event_named): return ''
	codes = _codes_for_fixture(session_text, fixture, year) if fixture.get('aggregate_daily_practice') is True else _session_codes(session_text, season=year)
	if 'FAM' in codes and not _qatar_familiarisation(fixture): return ''
	if code == 'TT' and 'TT' in codes:
		return 'unlabelled' if codes <= {'TT', 'QUAL'} and event_name_matches(text, fixture) else ''
	weekend = bool(re.search(r'\b(?:weekend|pack|bundle|all\s+sessions|complete\s+(?:race\s+)?weekend)\b', text))
	if weekend and (code in codes or _session_matches(codes, fixture) or codes not in ({'WU'}, {'FAM'}, {'FREE_PRACTICE'}, {'QUALIFYING_PAIR'})): return 'weekend'
	if _session_matches(codes, fixture): return ''
	if year == '2003' and code in ('FP2', 'FP3') and fixture.get('practice_format_2003') == 'combined' and codes == {code}: return 'weekend'
	if codes.intersection(_PRACTICE_2003_PATTERNS) and not codes.difference(_PRACTICE_2003_PATTERNS, _PRACTICE_PATTERNS, {'FREE_PRACTICE'}): return ''
	if codes.intersection(_QUALIFYING_WEEKDAYS) and not codes.difference(_QUALIFYING_WEEKDAYS, _QUALIFYING_PERIODS, {'QUAL', 'Q1', 'Q2', 'Q3', 'QUALIFYING_PAIR'}): return ''
	# Multiple explicitly named sessions also identify a race-weekend bundle.
	if len(codes) > 1 and (code in codes or code.startswith('FP') and re.search(r'\bpractice\b', text)): return 'weekend'
	if codes == {'QUAL'} and (code == 'QUAL' or code.startswith('FP')) and re.search(r'\bpractice\b', text): return 'weekend'
	if _explicit_rounds(text) and not codes and re.search(r'\b(?:grand prix|gp)\b', text): return 'unlabelled'
	if not codes and event_name_matches(text, fixture):
		if int(year) < 2006 or _explicit_rounds(text) or (_GRAND_PRIX.search(text) and _LANGUAGE_TAG.search(_unaccented(name))): return 'unlabelled'
		unique = _race_names(fixture, fixture.get('aliases') or []) - _other_names(fixture.get('other_event_aliases') or [])
		canonical = _alias(fixture.get('race_name'))
		if any(any(_event_title_spans(text, alias)) for alias in unique | {canonical} if _valid_alias(alias)): return 'unlabelled'
	return ''


def _archive_rows(year, event_id):
	try:
		with Path(__file__).with_name('f1_pack_manifests.json').open(encoding='utf-8') as source:
			rows = json.load(source).get(year, {}).get(event_id, [])
	except (OSError, ValueError, TypeError, AttributeError, RecursionError): return {}
	if not isinstance(rows, list): return {}
	return {row['info_hash']: row for row in rows if isinstance(row, dict) and isinstance(row.get('info_hash'), str) and _HASH.fullmatch(row['info_hash'])
		and row['info_hash'] != '0' * 40 and _text(row.get('name')) and isinstance(row.get('files'), list) and row['files']
		and all(isinstance(filename, str) and 0 < len(filename) <= 2000 and '\x00' not in filename for filename in row['files'])}


def search(fixture, status=None):
	"""Search the public index; return candidate hashes matching the supplied session."""
	if not isinstance(fixture, dict): return []
	year = _fixture_year(fixture)
	if not year: return []
	archived = _archive_rows(year, fixture.get('event_id')) if isinstance(fixture.get('event_id'), str) else {}
	archived = {info_hash: row for info_hash, row in archived.items() if pack_type(row['name'], fixture)}
	aliases = fixture.get('aliases') if isinstance(fixture.get('aliases'), list) else []
	others = fixture.get('other_event_aliases') if isinstance(fixture.get('other_event_aliases'), list) else []
	other_names = _other_names(others)
	names = _race_names(fixture, aliases)
	canonical = _alias(fixture.get('race_name'))
	localities = _localities(aliases)
	names = sorted((name for name in names if _valid_alias(name) and (name == canonical or name not in other_names)), key=lambda name: (name != canonical, name not in localities, len(name), name))
	event = _text(fixture.get('event'))
	if not names and 4 <= len(event) <= 150: names = [event]
	jobs = [('piratebay', 'Formula.1.' + year), ('knaben', 'Formula 1 ' + year + ' Season'), ('blackhole', 'Formula 1 ' + year + ' Season')]
	jobs.append(('knaben_season', year))
	current_year = datetime.now(timezone.utc).year
	legacy = int(year) <= current_year - 5
	if legacy: jobs.append(('torrentdownload', 'F1 ' + year + ' Season'))
	code = fixture.get('session_code')
	if code in _PRACTICE_PATTERNS:
		jobs.extend((('knaben_archive', 'Formula 1 ' + year + ' practice'), ('knaben_archive', 'F1 ' + year + ' ' + code.lower())))
	round_number = fixture.get('race_round')
	if type(round_number) is int and 1 <= round_number <= 99:
		for number in [round_number] + (fixture.get('round_aliases') or [])[:1]:
			jobs.append(('torrentdownload', 'F1 %s r%02d' % (year, number)))
	if names:
		primary = names[0]
		full_query, short_query = 'Formula 1 ' + year + ' ' + primary, 'F1 ' + year + ' ' + primary
		jobs.extend(('piratebay', query) for query in ('Formula 1 ' + primary, full_query, short_query))
		jobs.extend((('knaben_archive' if int(year) < current_year else 'knaben', full_query), ('knaben', short_query), ('blackhole', full_query), ('blackhole', short_query)))
		session = {'FP1': 'practice 1', 'FP2': 'practice 2', 'FP3': 'practice 3', 'FP4': 'practice 4', 'FAM': 'familiarisation', 'SS': 'sprint qualifying', 'SR': 'sprint', 'QUAL': 'qualifying', 'Q1': 'qualifying 1', 'Q2': 'qualifying 2', 'Q3': 'qualifying 3', 'WU': 'warm up', 'RACE': 'race'}.get(code)
		if fixture.get('qualifying_practice') or fixture.get('split_daily_practice') or fixture.get('aggregate_daily_practice'): session = fixture['session_label'].lower()
		if session: jobs.append(('piratebay', full_query + ' ' + session))
		if fixture.get('qualifying_portion'):
			qualifying_query = 'Formula 1 ' + year + ' ' + _text(fixture.get('race_name') or fixture.get('event')) + ' qualifying'
			jobs.extend((source, qualifying_query) for source in ('knaben_archive', 'torrentdownload'))
		if code == 'FP1' and fixture.get('single_practice') is True: jobs.append(('piratebay', full_query + ' free practice'))
		if code == 'PQ' and _fixture_session_allowed(fixture):
			jobs.extend((('piratebay', full_query + ' pre-qualifying'), ('torrentdownload', full_query + ' prequalifying')))
		if code == 'TT':
			jobs.extend((('knaben_archive', year + ' Indianapolis 500 Time Trials'), ('torrentdownload', year + ' Indy 500 Time Trials')))
		if code in ('Q1', 'Q2') and fixture.get('split_qualifying') is True: jobs.append(('piratebay', full_query + ' qualifying'))
		if code in ('FP1', 'FP2', 'FP3', 'FP4', 'Q1', 'Q2', 'Q3', 'WU'): jobs.append(('piratebay', short_query + ' ' + code.lower()))
		if fixture.get('qualifying_practice'): jobs.append(('torrentdownload', short_query + ' ' + session))
		if fixture.get('split_daily_practice') or fixture.get('aggregate_daily_practice'): jobs.append(('knaben_archive', full_query + ' ' + session))
		if code in ('FP4', 'WU') and int(year) <= 2005: jobs.append(('torrentdownload', full_query + ' ' + session))
		if code in ('FAM', 'SS') and _qatar_familiarisation(fixture):
			jobs.extend((source, full_query + ' familiarisation') for source in ('knaben', 'torrentdownload'))
		if code == 'SR' and year == '2021':
			jobs.extend((('piratebay', full_query + ' sprint qualifying'), ('torrentdownload', 'Formula 1 2021 Sprint Qualifying')))
		calendar_aliases = fixture.get('calendar_aliases') if isinstance(fixture.get('calendar_aliases'), list) else []
		calendar_names = {_alias(value) for value in calendar_aliases}
		city = _alias(fixture.get('race_city'))
		alternates = [name for name in names[1:] if len(name) <= 60 and len(name.split()) <= 6 and not any(_valid_alias(other) and (_contains_race(other, name) or _contains_race(name, other)) for other in other_names)]
		alternates.sort(key=lambda name: (name != city, name not in calendar_names, len(name), name))
		jobs.extend(('knaben', 'Formula 1 ' + year + ' ' + name) for name in alternates[:4])
		for source in ('piratebay', 'blackhole'):
			jobs.extend((source, 'F1 ' + year + ' ' + name) for name in alternates[:2])
		jobs.extend(('blackhole', 'Formula 1 ' + year + ' ' + name) for name in alternates[:2])
		if 'great britain' in names: jobs.append(('knaben', 'Formula 1 ' + year + ' great britain'))
		if canonical == 'united states': jobs.append(('knaben', 'Formula 1 ' + year + ' usa'))
		jobs.append(('torrentdownload', full_query))
		if legacy:
			jobs.append(('torrentdownload', short_query))
			if 'great britain' in names: jobs.append(('torrentdownload', 'Formula 1 ' + year + ' great britain'))
	jobs = list(dict.fromkeys(jobs))
	def fetch(job):
		source, query = job
		if source in ('knaben', 'knaben_archive', 'knaben_season', 'torrentdownload', 'blackhole'):
			try:
				if source in ('knaben', 'knaben_archive', 'knaben_season'):
					from modules import f1_knaben
					if source == 'knaben_season': return f1_knaben.search_season(query)
					if source == 'knaben_archive': return f1_knaben.search(query, include_dead=True), False
					return f1_knaben.search(query), False
				if source == 'torrentdownload':
					from modules import f1_torrentdownload
					return f1_torrentdownload.search(query), False
				from modules import f1_blackhole
				return f1_blackhole.search(query)
			except (ImportError, OSError, HTTPException, ValueError, TypeError, RecursionError): return None, False
		url = _URL + urlencode({'q': query, 'cat': '0'})
		try:
			request = Request(url, headers={'User-Agent': 'curl/8.0', 'Accept': 'application/json'})
			with urlopen(request, timeout=8) as response:
				body = response.read(_LIMIT + 1)
			if len(body) > _LIMIT: raise ValueError('Search response too large')
			batch = json.loads(body)
			if not isinstance(batch, list): raise ValueError('Invalid search response')
			return batch[:100], False
		except (OSError, HTTPException, ValueError, TypeError, RecursionError):
			return None, False
	with ThreadPoolExecutor(max_workers=min(18, len(jobs))) as executor:
		batches = list(executor.map(fetch, jobs))
	successes = sum(batch is not None for batch, _ in batches)
	if status is not None and (successes < len(batches) or any(degraded for _, degraded in batches)): status.append('partial' if successes or archived else 'unavailable')
	if not successes and not archived: raise SearchUnavailable('Formula 1 search is unavailable')
	rows = [*archived.values(), *(row for batch, _ in batches if batch is not None for row in batch)]
	detail_paths = {str(row.get('info_hash') or '').lower(): row['detail_path'] for row in rows if isinstance(row, dict) and row.get('source') == 'torrentdownload' and row.get('detail_path')}
	results, seen, unlabelled = [], set(), {}
	for row in rows:
		if not isinstance(row, dict): continue
		name, info_hash = row.get('name'), str(row.get('info_hash') or '').lower()
		if not isinstance(name, str) or not _HASH.fullmatch(info_hash) or info_hash == '0' * 40 or info_hash in seen: continue
		if info_hash in archived and row is not archived[info_hash]: continue
		name = unescape(name).strip()
		pack = pack_type(name, fixture)
		if not pack and not _match(name, fixture): continue
		try: size = max(0, int(row.get('size') or 0))
		except (ValueError, TypeError, OverflowError): size = 0
		candidate = {'name': name, 'hash': info_hash, 'magnet': 'magnet:?xt=urn:btih:%s&dn=%s' % (info_hash, quote(name)), 'size': size, 'source': row.get('source', 'piratebay'), 'pack': pack}
		if info_hash in archived:
			candidate.update(source='archive', manifest_files=row['files'])
		if pack and info_hash in detail_paths: candidate['detail_path'] = detail_paths[info_hash]
		if pack == 'unlabelled':
			unlabelled.setdefault(info_hash, candidate)
			continue
		seen.add(info_hash)
		results.append(candidate)
	return results + [candidate for info_hash, candidate in unlabelled.items() if info_hash not in seen]
