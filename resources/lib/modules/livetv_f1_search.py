"""Find conservatively matched F1 torrent candidates through an existing index."""
import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.client import HTTPException
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

_URL = 'https://apibay.org/q.php?'
_LIMIT = 2 * 1024 * 1024
_HASH = re.compile(r'^[0-9a-f]{40}$')
_REJECT = re.compile(r'\b(?:highlights?|recaps?|previews?|interviews?|analysis|review|documentary|drive to survive|onboard|on board|build\s*up)\b|\b(?:pre|post)\s*(?:race|qualifying|quali|qual|practice|sprint|fp\s*[123])\b|\bsprint\s+(?:weekend|pack|sessions?)\b|\b(?:complete|full)\s+(?:race\s+)?(?:weekend|season)\b|\b(?:weekend|season)\s+(?:pack|collection)\b')
_PACK_REJECT = re.compile(r'\b(?:highlights?|recaps?|previews?|interviews?|analysis|review|documentary|drive to survive|onboard|on board|build\s*up)\b|\b(?:pre|post)\s*(?:race|qualifying|quali|qual|practice|sprint|fp\s*[123])\b')
_SESSIONS = {
	'FP1': r'\b(?:fp\s*1|(?:free\s+)?practice\s*1)\b',
	'FP2': r'\b(?:fp\s*2|(?:free\s+)?practice\s*2)\b',
	'FP3': r'\b(?:fp\s*3|(?:free\s+)?practice\s*3)\b',
	'SS': r'\bsprint\s+(?:qualifying|quali|qual|shootout)\b',
	'SR': r'\bsprint\b(?!\s+(?:qualifying|quali|qual|shootout)\b)',
	'QUAL': r'(?<!sprint )\b(?:qualifying|quali|qual)\b',
	'RACE': r'(?<!sprint )\brace\b',
}


class SearchUnavailable(Exception):
	"""The search index did not return a usable response."""


def _text(value):
	if not isinstance(value, str) or len(value) > 2000: return ''
	value = unicodedata.normalize('NFKD', value.casefold())
	return ' '.join(re.sub(r'[^a-z0-9]+', ' ', ''.join(c for c in value if not unicodedata.combining(c))).split())


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
	return re.sub(r'\s+(?:grand prix|gp)$', '', _text(value))


def _other_names(aliases):
	"""Include distinctive place names from explicitly named local circuits."""
	names = {_alias(value) for value in aliases}
	for name in tuple(names):
		match = re.fullmatch(r'(.{4,}?)\s+(?:street circuit|international autodrome)', name)
		if match: names.add(match.group(1))
	if 'british' in names: names.add('great britain')
	return names


def _race_names(fixture, aliases):
	names = {_alias(value) for value in aliases + [fixture.get('event'), fixture.get('race_name')]}
	# Jolpica's confirmed British GP is also commonly called Great Britain.
	if _text(fixture.get('race_name')) in ('british grand prix', 'british gp'): names.add('great britain')
	return names


def _usa_round(fixture, text, year):
	"""Match USA only when a verified United States GP round is explicit."""
	round_number = fixture.get('race_round')
	if _text(fixture.get('race_name')) != 'united states grand prix' or isinstance(round_number, bool) or not isinstance(round_number, int) or not 1 <= round_number <= 99: return False
	if not re.search(r'\busa\b', text): return False
	year_rounds = re.findall(r'(?<!\d)((?:19|20)\d{2})\s*x\s*(\d{1,3})(?!\d)', text)
	rounds = re.findall(r'\bround\s*(\d{1,3})(?!\d)', text)
	if not year_rounds and not rounds: return False
	short_rounds = re.findall(r'\br\s*(\d{1,3})\b', text)
	return all(found_year == year and int(found_round) == round_number for found_year, found_round in year_rounds) and all(int(found_round) == round_number for found_round in rounds + short_rounds)


def _match(name, fixture):
	text = _text(name)
	code = fixture.get('session_code')
	date = fixture.get('date', '')
	if code not in _SESSIONS or not isinstance(date, str) or not re.match(r'^\d{4}-', date): return False
	year = date[:4]
	if not re.search(r'(?<!\d)' + year + r'(?!\d)', text) or not re.search(r'\b(?:f\s*1|formula\s*(?:1|one))\b', text) or _REJECT.search(text): return False
	if _conflicting_year(text, year): return False
	rounds = re.findall(r'\bround\s*(\d{1,3})\b', text)
	if rounds:
		round_number = fixture.get('race_round')
		if not isinstance(round_number, bool) and isinstance(round_number, int) and 1 <= round_number <= 99 and any(int(found_round) != round_number for found_round in rounds): return False
	codes = {key for key, pattern in _SESSIONS.items() if re.search(pattern, text)}
	if codes != {code}: return False
	aliases = fixture.get('aliases') if isinstance(fixture.get('aliases'), list) else []
	others = fixture.get('other_event_aliases') if isinstance(fixture.get('other_event_aliases'), list) else []
	other_names = _other_names(others)
	names = _race_names(fixture, aliases)
	unique = {name for name in names if len(name) >= 4 and name not in other_names}
	if not any(_contains_race(text, name) for name in unique) and not _usa_round(fixture, text, year): return False
	# A title that names another event as well is ambiguous, even if it names
	# this event; do not confuse US, Miami and Las Vegas race weekends.
	return not any(len(name) >= 4 and not any(_contains(own, name) for own in names) and _contains_race(text, name) for name in other_names)


def pack_type(name, fixture):
	"""Identify explicitly labelled season or race-weekend bundles conservatively."""
	if not isinstance(fixture, dict): return ''
	text, date, code = _text(name), fixture.get('date', ''), fixture.get('session_code')
	if not text or code not in _SESSIONS or not isinstance(date, str) or not re.match(r'^\d{4}-', date): return ''
	year = date[:4]
	if not re.search(r'(?<!\d)' + year + r'(?!\d)', text) or not re.search(r'\b(?:f\s*1|formula\s*(?:1|one))\b', text) or _PACK_REJECT.search(text): return ''
	if _conflicting_year(text, year): return ''
	aliases = fixture.get('aliases') if isinstance(fixture.get('aliases'), list) else []
	others = fixture.get('other_event_aliases') if isinstance(fixture.get('other_event_aliases'), list) else []
	names, other_names = _race_names(fixture, aliases), _other_names(others)
	unique = {value for value in names if len(value) >= 4 and value not in other_names}
	selected = any(_contains_race(text, value) for value in unique) or _usa_round(fixture, text, year)
	conflicting = any(len(value) >= 4 and not any(_contains(own, value) for own in names) and _contains_race(text, value) for value in other_names)
	season = bool(re.search(r'\b(?:season\s*' + year + r'|' + year + r'\s*(?:complete\s+|full\s+)?season)\b', text))
	if season:
		# A season bundle must not be a race-specific release with a season tag.
		if selected or conflicting: return ''
		return 'season'
	if not selected or conflicting: return ''
	# Race-weekend packs can have one explicit round; never ignore a conflict.
	year_rounds = [(found_year, found_round) for found_year, found_round in re.findall(r'(?<!\d)((?:19|20)\d{2})\s*x\s*(\d{1,3})(?!\d)', text) if int(found_round) <= 99]
	rounds = re.findall(r'\b(?:round|r)\s*(\d{1,3})\b', text)
	round_number = fixture.get('race_round')
	if year_rounds or rounds:
		if isinstance(round_number, bool) or not isinstance(round_number, int) or not 1 <= round_number <= 99: return ''
		if any(found_year != year or int(found_round) != round_number for found_year, found_round in year_rounds) or any(int(found_round) != round_number for found_round in rounds): return ''
	codes = {key for key, pattern in _SESSIONS.items() if re.search(pattern, text)}
	weekend = bool(re.search(r'\b(?:weekend|pack|bundle|all\s+sessions|complete\s+(?:race\s+)?weekend)\b', text))
	if weekend: return 'weekend'
	# Multiple explicitly named sessions also identify a race-weekend bundle.
	if len(codes) > 1 and (code in codes or code.startswith('FP') and re.search(r'\bpractice\b', text)): return 'weekend'
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
	names = sorted((name for name in names if len(name) >= 4 and name not in other_names), key=len)
	canonical = _alias(fixture.get('race_name'))
	if canonical in names: names.remove(canonical); names.insert(0, canonical)
	queries = ['Formula.1.' + date[:4]]
	if names:
		name, year = names[0], date[:4]
		queries.extend(('Formula 1 ' + name, 'Formula 1 ' + year + ' ' + name, 'F1 ' + year + ' ' + name))
		session = {'FP1': 'practice 1', 'FP2': 'practice 2', 'FP3': 'practice 3', 'SS': 'sprint qualifying', 'SR': 'sprint', 'QUAL': 'qualifying', 'RACE': 'race'}.get(fixture.get('session_code'))
		if session: queries.append('Formula 1 ' + year + ' ' + name + ' ' + session)
	queries = list(dict.fromkeys(queries))
	jobs = [('piratebay', query) for query in queries]
	jobs.append(('knaben', 'Formula 1 ' + date[:4] + ' Season'))
	if names:
		jobs.extend(('knaben', query) for query in ('Formula 1 ' + date[:4] + ' ' + names[0], 'F1 ' + date[:4] + ' ' + names[0]))
		# A short unique locality or country can be indexed under a different
		# spelling from the calendar name, e.g. Brazil versus Brazilian.
		alternates = sorted({_alias(value) for value in aliases}, key=lambda value: (len(value), value))
		alternates = [name for name in alternates if 4 <= len(name) <= 25 and len(name.split()) <= 2 and name != names[0] and name in names and not re.search(r'\b(?:circuit|autodrome|autodromo|speedway)\b', name) and not any(len(other) >= 4 and (_contains_race(other, name) or _contains_race(name, other)) for other in other_names)]
		if alternates: jobs.append(('knaben', 'Formula 1 ' + date[:4] + ' ' + alternates[0]))
		# Calendar aliases are verified for this event and season. Search a small
		# number of them as separate queries so an index can find circuit names
		# even when the official Grand Prix name is absent from the release.
		calendar_aliases = fixture.get('calendar_aliases') if isinstance(fixture.get('calendar_aliases'), list) else []
		calendar_names = list(dict.fromkeys(_alias(value) for value in calendar_aliases))
		calendar_names = [name for name in calendar_names if 4 <= len(name) <= 60 and len(name.split()) <= 6 and name in names and name != names[0] and not any(len(other) >= 4 and (_contains_race(other, name) or _contains_race(name, other)) for other in other_names)]
		jobs.extend(('knaben', 'Formula 1 ' + date[:4] + ' ' + name) for name in calendar_names[:4])
		if 'great britain' in names: jobs.append(('knaben', 'Formula 1 ' + date[:4] + ' great britain'))
		if _text(fixture.get('race_name')) == 'united states grand prix' and isinstance(fixture.get('race_round'), int) and not isinstance(fixture['race_round'], bool) and 1 <= fixture['race_round'] <= 99:
			jobs.append(('knaben', 'Formula 1 ' + date[:4] + ' usa'))
		jobs.append(('blackhole', 'Formula 1 ' + date[:4] + ' ' + names[0]))
		if int(date[:4]) <= datetime.now(timezone.utc).year - 5:
			jobs.extend(('torrentdownload', query) for query in ('Formula 1 ' + date[:4] + ' ' + names[0], 'F1 ' + date[:4] + ' ' + names[0]))
			if 'great britain' in names: jobs.append(('torrentdownload', 'Formula 1 ' + date[:4] + ' great britain'))
	jobs = list(dict.fromkeys(jobs))
	def fetch(job):
		source, query = job
		if source in ('knaben', 'torrentdownload', 'blackhole'):
			try:
				if source == 'knaben':
					from modules import f1_knaben
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
	results, seen = [], set()
	for row in rows:
		if not isinstance(row, dict): continue
		name, info_hash = row.get('name'), str(row.get('info_hash') or '').lower()
		if not _HASH.fullmatch(info_hash) or info_hash == '0' * 40 or info_hash in seen: continue
		pack = pack_type(name, fixture)
		if not pack and not _match(name, fixture): continue
		seen.add(info_hash)
		try: size = max(0, int(row.get('size') or 0))
		except (ValueError, TypeError, OverflowError): size = 0
		results.append({'name': name, 'hash': info_hash, 'magnet': 'magnet:?xt=urn:btih:%s&dn=%s' % (info_hash, quote(name)), 'size': size, 'source': row.get('source', 'piratebay'), 'pack': pack})
	return results
