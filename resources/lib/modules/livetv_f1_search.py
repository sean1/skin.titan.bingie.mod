"""Find conservatively matched F1 torrent candidates through an existing index."""
import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.client import HTTPException
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from modules.f1_calendar import circuit_locality

_URL = 'https://apibay.org/q.php?'
_LIMIT = 2 * 1024 * 1024
_HASH = re.compile(r'^[0-9a-f]{40}$')
_WARM_UP_LABEL = r'(?:warm\s*up|wu)'
_WARM_UP = re.compile(rf'\b{_WARM_UP_LABEL}\b')
_FAMILIARISATION_LABEL = r'(?:track\s+familiari[sz]ation|familiari[sz]ation\s+session)'
_PACK_EXTRAS = re.compile(
	r'\b(?:highlights?|recaps?|previews?|interviews?|analysis|review|documentary|drive to survive|onboard|on board|build\s*up|press\s+conferences?|paddock\s+uncut|season\s+launch|samples?)\b'
	rf'|\b(?:pre|post)\s*(?:race|qualifying|qualification|quali|qual|(?:free\s*)?practice|sprint|shootout|fp\s*0?[1-4]|s?q\s*0?[123]|(?:first|second|third)\s+qual(?:ifying|ification|i)?|{_WARM_UP_LABEL}|{_FAMILIARISATION_LABEL}|show)\b'
	rf'|\b(?:(?:race|sprint|qualifying|shootout)\s+in\s+\d{{1,3}}|race\s+(?:starts?|radio|reports?|summar(?:y|ies))|starting\s+grid|grid\s+walk|(?:{_WARM_UP_LABEL}|formation|parade)\s+laps?)\b')
_SUPPORT_SERIES = re.compile(r'\b(?:f\s*1\s*academy|formula\s*(?:1|one)\s*academy|f\s*[23]|formula\s*[23]|formula\s+(?:two|three))\b')
_PACK_REJECT = re.compile(_PACK_EXTRAS.pattern + '|' + _SUPPORT_SERIES.pattern)
_FILE_REJECT = re.compile(_PACK_REJECT.pattern + r'|\bsprint\s+(?:weekend|pack|sessions?)\b|\b(?:complete|full)\s+(?:race\s+)?(?:weekend|season)\b|\b(?:weekend|season)\s+(?:pack|collection)\b')
_QUALIFYING = r'(?:qualifying|qualification|quali|qual)'
_SPRINT_QUALIFYING = re.compile(rf'\bsprint\s+(?:race\s+)?(?:{_QUALIFYING}|shootout)\b(?:\s+(?:(?:session|stage|part)\s+)?(?:(?:sq|q)\s*)?0?([123])\b)?')
_SPRINT_QUALIFYING_PHASE = re.compile(r'\bsq\s*0?([123])\b')
_NUMBERED_QUALIFYING = re.compile(
	rf'\b(?:{_QUALIFYING}(?:\s+(?:session|day))?(?:\s*(?:q\s*)?0?([123])|\s+(one|two|three))'
	rf'|q\s*0?([123])(?:\s+{_QUALIFYING})?|(first|second|third)\s+{_QUALIFYING})\b')
_QUALIFYING_NUMBERS = {'first': '1', 'one': '1', 'second': '2', 'two': '2', 'third': '3', 'three': '3'}
_SESSION_PATTERNS = {
	'FP1': r'\b(?:fp\s*0?1|(?:free\s*)?practice(?:\s*session)?(?:\s*0?1|\s+one))\b',
	'FP2': r'\b(?:fp\s*0?2|(?:free\s*)?practice(?:\s*session)?(?:\s*0?2|\s+two))\b',
	'FP3': r'\b(?:fp\s*0?3|(?:free\s*)?practice(?:\s*session)?(?:\s*0?3|\s+three))\b',
	'FP4': r'\b(?:fp\s*0?4|(?:free\s*)?practice(?:\s*session)?(?:\s*0?4|\s+four))\b',
	'WU': _WARM_UP.pattern,
	'FAM': rf'\b{_FAMILIARISATION_LABEL}\b',
	'SR': r'\bsprint\b(?!\s+(?:qualifying|qualification|quali|qual|shootout|weekend|pack|sessions?)\b)',
	'QUAL': rf'(?<!sprint )\b{_QUALIFYING}\b',
	'RACE': r'(?<!sprint )\brace\b',
}
_SESSIONS = _SESSION_PATTERNS.keys() | {'SS', 'Q1', 'Q2'}


def _historical_session_allowed(code: str, season: str) -> bool:
	return code not in ('FP4', 'WU', 'Q1', 'Q2') or len(season) == 4 and '1950' <= season <= '2005'


def _qatar_familiarisation(fixture):
	return fixture['date'][:10] == '2023-10-07' and (fixture.get('event_id') == '600026765' or _alias(fixture.get('race_name')) == 'qatar')


def _fixture_session_allowed(fixture):
	code = fixture.get('session_code')
	if code == 'FAM': return _qatar_familiarisation(fixture)
	return _historical_session_allowed(code, fixture['date'][:4])


def _session_matches(codes, fixture):
	code = fixture.get('session_code')
	if 'FAM' in codes:
		return _qatar_familiarisation(fixture) and code in codes and codes in ({'FAM'}, {'FAM', 'SS'})
	return codes == {code}


def _recording_rejected(text: str, pattern: re.Pattern, *, code: str, season: str) -> bool:
	return bool(pattern.search(text) or (_WARM_UP.search(text) and (code != 'WU' or not _historical_session_allowed(code, season))))


def _session_codes(normalized_text: str, *, season: str = '') -> set[str]:
	codes = set()
	for match in _SPRINT_QUALIFYING.finditer(normalized_text):
		phase = match.group(1)
		if phase: codes.add('SQ' + phase)
		elif season == '2021' and match.group() == 'sprint qualifying': codes.add('SR')
		else: codes.add('SS')
	normalized_text = _SPRINT_QUALIFYING.sub(' ', normalized_text)
	if season == '2023' and re.search(r'\bshootout\b(?!\s+(?:(?:session|stage|part)\s+)?0?[123]\b)', normalized_text): codes.add('SS')
	codes.update('SQ' + phase for phase in _SPRINT_QUALIFYING_PHASE.findall(normalized_text))
	for match in _NUMBERED_QUALIFYING.finditer(normalized_text):
		number = next(value for value in match.groups() if value)
		codes.add('Q' + _QUALIFYING_NUMBERS.get(number, number))
	normalized_text = _NUMBERED_QUALIFYING.sub(' ', normalized_text)
	codes.update(code for code, pattern in _SESSION_PATTERNS.items() if re.search(pattern, normalized_text))
	return codes


class SearchUnavailable(Exception):
	"""The search index did not return a usable response."""


def _text(value):
	if not isinstance(value, str) or len(value) > 2000: return ''
	value = unicodedata.normalize('NFKD', value.casefold())
	value = ' '.join(re.sub(r'[^a-z0-9]+', ' ', ''.join(c for c in value if not unicodedata.combining(c))).split())
	return re.sub(r'\bcaesar s palace\b', 'caesars palace', value)


def _contains(text, phrase):
	return bool(phrase and re.search(r'(?<!\w)' + re.escape(phrase).replace(r'\ ', r'\s+') + r'(?!\w)', text))


def _conflicting_year(text, year):
	# Frame dimensions like 1920x1080 are not references to another season.
	text = re.sub(r'\b(?:19|20)\d{2}\s*x\s*(?:720|1080|1200|1440|2160|4320)\b', ' ', text)
	return any(value != year for value in re.findall(r'(?<!\d)((?:19|20)\d{2})(?!\d)', text))


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
	return [int(value) for value in re.findall(r'\b(?:round|rd|r)\s*(\d{1,3})\b', text)]


def _round_numbers(text):
	explicit = _explicit_rounds(text)
	if explicit: return explicit
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
	date = fixture.get('date', '')
	if code not in _SESSIONS or not isinstance(date, str) or not re.match(r'^\d{4}-', date): return False
	year = date[:4]
	if not _fixture_session_allowed(fixture): return False
	if not re.search(r'(?<!\d)' + year + r'(?!\d)', text) or not re.search(r'\b(?:f\s*1|formula\s*(?:1|one))\b', text) or _recording_rejected(text, _FILE_REJECT, code=code, season=year): return False
	if _conflicting_year(text, year): return False
	event_named = bool(fixture.get('round_aliases')) and event_name_matches(text, fixture)
	if not _rounds_agree(_round_numbers(text), fixture, event_named=event_named): return False
	return _session_matches(_session_codes(text, season=year), fixture) and _event_matches(text, fixture)


def pack_type(name, fixture):
	"""Identify recording bundles and listings that require file verification."""
	if not isinstance(fixture, dict): return ''
	text, date, code = _text(name), fixture.get('date', ''), fixture.get('session_code')
	if not text or code not in _SESSIONS or not isinstance(date, str) or not re.match(r'^\d{4}-', date): return ''
	year = date[:4]
	if not _fixture_session_allowed(fixture): return ''
	if not re.search(r'(?<!\d)' + year + r'(?!\d)', text) or not re.search(r'\b(?:f\s*1|formula\s*(?:1|one))\b', text) or _PACK_REJECT.search(text): return ''
	if _WARM_UP.search(text) and not _historical_session_allowed('WU', year): return ''
	if _conflicting_year(text, year): return ''
	selected = _event_matches(text, fixture)
	season = re.search(rf'\b(?:season\s*{year}|{year}\s*(?:(?:f\s*1|formula\s*(?:1|one))\s+)?(?:complete\s+|full\s+)?season)\b', text)
	if season:
		# A season bundle must not be a race-specific release with a season tag.
		if selected or _event_conflicts(text, fixture): return ''
		return 'season'
	rounds = _round_numbers(text)
	event_named = bool(fixture.get('round_aliases')) and event_name_matches(text, fixture)
	if not selected or not _rounds_agree(rounds, fixture, require_known=True, event_named=event_named): return ''
	codes = _session_codes(text, season=year)
	if 'FAM' in codes and not _qatar_familiarisation(fixture): return ''
	weekend = bool(re.search(r'\b(?:weekend|pack|bundle|all\s+sessions|complete\s+(?:race\s+)?weekend)\b', text))
	if weekend and (code in codes or codes not in ({'WU'}, {'FAM'})): return 'weekend'
	if codes == {'FAM', 'SS'}: return ''
	# Multiple explicitly named sessions also identify a race-weekend bundle.
	if len(codes) > 1 and (code in codes or code.startswith('FP') and re.search(r'\bpractice\b', text)): return 'weekend'
	if codes == {'QUAL'} and (code == 'QUAL' or code.startswith('FP')) and re.search(r'\bpractice\b', text): return 'weekend'
	if _explicit_rounds(text) and not codes and re.search(r'\b(?:grand prix|gp)\b', text): return 'unlabelled'
	if int(year) < 2006 and not codes and event_name_matches(text, fixture): return 'unlabelled'
	return ''


def search(fixture, status=None):
	"""Search the public index; return candidate hashes matching the supplied session."""
	if not isinstance(fixture, dict): return []
	date = fixture.get('date', '')
	if not isinstance(date, str) or not re.match(r'^\d{4}-', date): return []
	aliases = fixture.get('aliases') if isinstance(fixture.get('aliases'), list) else []
	others = fixture.get('other_event_aliases') if isinstance(fixture.get('other_event_aliases'), list) else []
	other_names = _other_names(others)
	names = _race_names(fixture, aliases)
	canonical = _alias(fixture.get('race_name'))
	localities = _localities(aliases)
	names = sorted((name for name in names if _valid_alias(name) and (name == canonical or name not in other_names)), key=lambda name: (name != canonical, name not in localities, len(name), name))
	event = _text(fixture.get('event'))
	if not names and 4 <= len(event) <= 150: names = [event]
	year = date[:4]
	jobs = [('piratebay', 'Formula.1.' + year), ('knaben', 'Formula 1 ' + year + ' Season'), ('blackhole', 'Formula 1 ' + year + ' Season')]
	jobs.append(('knaben_season', year))
	current_year = datetime.now(timezone.utc).year
	legacy = int(year) <= current_year - 5
	if legacy: jobs.append(('torrentdownload', 'F1 ' + year + ' Season'))
	round_number = fixture.get('race_round')
	if type(round_number) is int and 1 <= round_number <= 99:
		for number in [round_number] + (fixture.get('round_aliases') or [])[:1]:
			jobs.append(('torrentdownload', 'F1 %s r%02d' % (year, number)))
	if names:
		primary = names[0]
		full_query, short_query = 'Formula 1 ' + year + ' ' + primary, 'F1 ' + year + ' ' + primary
		jobs.extend(('piratebay', query) for query in ('Formula 1 ' + primary, full_query, short_query))
		jobs.extend((('knaben_archive' if int(year) < current_year else 'knaben', full_query), ('knaben', short_query), ('blackhole', full_query), ('blackhole', short_query)))
		code = fixture.get('session_code')
		session = {'FP1': 'practice 1', 'FP2': 'practice 2', 'FP3': 'practice 3', 'FP4': 'practice 4', 'FAM': 'familiarisation', 'SS': 'sprint qualifying', 'SR': 'sprint', 'QUAL': 'qualifying', 'Q1': 'qualifying 1', 'Q2': 'qualifying 2', 'WU': 'warm up', 'RACE': 'race'}.get(code)
		if session: jobs.append(('piratebay', full_query + ' ' + session))
		if code in ('FP1', 'FP2', 'FP3', 'FP4', 'Q1', 'Q2', 'WU'): jobs.append(('piratebay', short_query + ' ' + code.lower()))
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
		if legacy:
			jobs.extend(('torrentdownload', query) for query in (full_query, short_query))
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
	if status is not None and (successes < len(batches) or any(degraded for _, degraded in batches)): status.append('partial' if successes else 'unavailable')
	if not successes: raise SearchUnavailable('Formula 1 search is unavailable')
	rows = [row for batch, _ in batches if batch is not None for row in batch]
	results, seen, unlabelled = [], set(), {}
	for row in rows:
		if not isinstance(row, dict): continue
		name, info_hash = row.get('name'), str(row.get('info_hash') or '').lower()
		if not _HASH.fullmatch(info_hash) or info_hash == '0' * 40 or info_hash in seen: continue
		pack = pack_type(name, fixture)
		if not pack and not _match(name, fixture): continue
		try: size = max(0, int(row.get('size') or 0))
		except (ValueError, TypeError, OverflowError): size = 0
		candidate = {'name': name, 'hash': info_hash, 'magnet': 'magnet:?xt=urn:btih:%s&dn=%s' % (info_hash, quote(name)), 'size': size, 'source': row.get('source', 'piratebay'), 'pack': pack}
		if pack == 'unlabelled':
			unlabelled.setdefault(info_hash, candidate)
			continue
		seen.add(info_hash)
		results.append(candidate)
	return results + [candidate for info_hash, candidate in unlabelled.items() if info_hash not in seen]
