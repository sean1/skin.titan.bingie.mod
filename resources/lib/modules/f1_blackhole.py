"""Search Black Hole Bay's public index for Formula 1 candidates."""
import json
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_URL = 'https://theblackholebay.lol/api/search?'
_LIMIT = 2 * 1024 * 1024
_HASH = re.compile(r'^[0-9a-f]{40}$')


def search(query):
	"""Return bounded index rows and whether the index reports a fallback."""
	if not isinstance(query, str) or not query.strip() or len(query) > 200: raise ValueError('Invalid Formula 1 query')
	url = _URL + urlencode({'q': query, 'category': '200'})
	request = Request(url, headers={'User-Agent': 'BINGIE F1', 'Accept': 'application/json'})
	with urlopen(request, timeout=8) as response:
		body = response.read(_LIMIT + 1)
	if len(body) > _LIMIT: raise ValueError('Search response too large')
	data = json.loads(body)
	if not isinstance(data, dict) or not isinstance(data.get('results'), list) or not isinstance(data.get('source'), str) or not data['source'].strip(): raise ValueError('Invalid search response')
	if not isinstance(data.get('query'), str) or ' '.join(data['query'].casefold().split()) != ' '.join(query.casefold().split()): raise ValueError('Unexpected search response')
	results, seen = [], set()
	for item in data['results'][:100]:
		if not isinstance(item, dict): continue
		name, info_hash = item.get('name'), item.get('info_hash')
		if not isinstance(name, str) or not name.strip() or len(name) > 2000 or not isinstance(info_hash, str): continue
		info_hash = info_hash.lower()
		if not _HASH.fullmatch(info_hash) or info_hash == '0' * 40 or info_hash in seen: continue
		try: size = max(0, int(item.get('size') or 0))
		except (ValueError, TypeError, OverflowError): size = 0
		seen.add(info_hash)
		results.append({'name': name, 'info_hash': info_hash, 'size': size, 'source': 'blackhole'})
	return results, 'fallback' in data['source'].casefold()
