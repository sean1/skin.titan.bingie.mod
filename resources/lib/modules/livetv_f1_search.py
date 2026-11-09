"""Find conservatively matched F1 torrent candidates through an existing index."""
import json
import re
import unicodedata
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

_URL = 'https://apibay.org/q.php?'
_LIMIT = 2 * 1024 * 1024
_HASH = re.compile(r'^[0-9a-f]{40}$')
_REJECT = re.compile(r'\b(?:highlights?|recaps?|previews?|interviews?|analysis|review|documentary|drive to survive|onboard|on board|build\s*up)\b|\b(?:pre|post)\s*(?:race|qualifying|qual|practice|sprint|fp\s*[123])\b')
_SESSIONS = {
	'FP1': r'\b(?:fp\s*1|(?:free\s+)?practice\s*1)\b',
	'FP2': r'\b(?:fp\s*2|(?:free\s+)?practice\s*2)\b',
	'FP3': r'\b(?:fp\s*3|(?:free\s+)?practice\s*3)\b',
	'SS': r'\b(?:sprint\s+(?:qualifying|shootout))\b',
	'SR': r'\b(?:sprint\s+race)\b',
	'QUAL': r'(?<!sprint )\b(?:qualifying|qual)\b',
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


def _alias(value):
	return re.sub(r'\s+(?:grand prix|gp)$', '', _text(value))


def _match(name, fixture):
	text = _text(name)
	code = fixture.get('session_code')
	date = fixture.get('date', '')
	if code not in _SESSIONS or not isinstance(date, str) or not re.match(r'^\d{4}-', date): return False
	year = date[:4]
	if not re.search(r'(?<!\d)' + year + r'(?!\d)', text) or not re.search(r'\b(?:f\s*1|formula\s*(?:1|one))\b', text) or _REJECT.search(text): return False
	codes = {key for key, pattern in _SESSIONS.items() if re.search(pattern, text)}
	if codes != {code}: return False
	aliases = fixture.get('aliases') if isinstance(fixture.get('aliases'), list) else []
	others = fixture.get('other_event_aliases') if isinstance(fixture.get('other_event_aliases'), list) else []
	other_names = {_alias(value) for value in others}
	for value in aliases + [fixture.get('event')]:
		alias = _alias(value)
		if len(alias) >= 4 and alias not in other_names and _contains(text, alias): return True
	return False


def search(fixture):
	"""Search the public index; return candidate hashes matching the supplied session."""
	if not isinstance(fixture, dict): return []
	date = fixture.get('date', '')
	if not isinstance(date, str) or not re.match(r'^\d{4}-', date): return []
	aliases = fixture.get('aliases') if isinstance(fixture.get('aliases'), list) else []
	others = fixture.get('other_event_aliases') if isinstance(fixture.get('other_event_aliases'), list) else []
	other_names = {_alias(value) for value in others}
	names = {_alias(value) for value in aliases + [fixture.get('event')]}
	names = sorted((name for name in names if len(name) >= 4 and name not in other_names), key=len)
	queries = ['Formula.1.' + date[:4]]
	if names: queries.append('Formula 1 ' + names[0])
	rows, successes = [], 0
	for query in queries:
		url = _URL + urlencode({'q': query, 'cat': '0'})
		try:
			request = Request(url, headers={'User-Agent': 'curl/8.0', 'Accept': 'application/json'})
			with urlopen(request, timeout=8) as response:
				body = response.read(_LIMIT + 1)
			if len(body) > _LIMIT: raise ValueError('Search response too large')
			batch = json.loads(body)
			if not isinstance(batch, list): raise ValueError('Invalid search response')
			rows.extend(batch[:100])
			successes += 1
		except (OSError, ValueError, TypeError):
			continue
	if not successes: raise SearchUnavailable('Formula 1 search is unavailable')
	results, seen = [], set()
	for row in rows:
		if not isinstance(row, dict): continue
		name, info_hash = row.get('name'), str(row.get('info_hash') or '').lower()
		if not _HASH.fullmatch(info_hash) or info_hash == '0' * 40 or info_hash in seen or not _match(name, fixture): continue
		seen.add(info_hash)
		try: size = max(0, int(row.get('size') or 0))
		except (ValueError, TypeError, OverflowError): size = 0
		results.append({'name': name, 'hash': info_hash, 'magnet': 'magnet:?xt=urn:btih:%s&dn=%s' % (info_hash, quote(name)), 'size': size, 'source': 'piratebay'})
	return results
