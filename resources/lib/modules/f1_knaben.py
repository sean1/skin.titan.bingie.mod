"""Search Knaben's public torrent index for Formula 1 candidates."""
import json
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_URL = 'https://api.knaben.org/v2/search?'
_LIMIT = 2 * 1024 * 1024
_HASH = re.compile(r'^[0-9a-f]{40}$')


def search(query):
	"""Return bounded index rows, raising on an unavailable or invalid response."""
	if not isinstance(query, str) or not query.strip() or len(query) > 200: raise ValueError('Invalid Formula 1 query')
	url = _URL + urlencode({'q': query, 's': 100, 'o': 'seeders'})
	request = Request(url, headers={'User-Agent': 'BINGIE F1', 'Accept': 'application/json'})
	with urlopen(request, timeout=8) as response:
		body = response.read(_LIMIT + 1)
	if len(body) > _LIMIT: raise ValueError('Search response too large')
	data = json.loads(body)
	if not isinstance(data, dict) or not isinstance(data.get('hits'), list): raise ValueError('Invalid search response')
	results, seen = [], set()
	for hit in data['hits'][:100]:
		if not isinstance(hit, dict): continue
		name, info_hash = hit.get('title'), hit.get('hash')
		if not isinstance(name, str) or not name.strip() or len(name) > 2000 or not isinstance(info_hash, str): continue
		info_hash = info_hash.lower()
		if not _HASH.fullmatch(info_hash) or info_hash == '0' * 40 or info_hash in seen: continue
		try: size = max(0, int(hit.get('bytes') or 0))
		except (ValueError, TypeError, OverflowError): size = 0
		seen.add(info_hash)
		results.append({'name': name, 'info_hash': info_hash, 'size': size, 'source': 'knaben'})
	return results
