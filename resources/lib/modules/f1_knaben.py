"""Search Knaben's public torrent index for Formula 1 candidates."""
import json
import re
from http.client import HTTPException
from time import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_URL = 'https://api.knaben.org/v2/search?'
_LIMIT = 2 * 1024 * 1024
_HASH = re.compile(r'^[0-9a-f]{40}$')
_PAGE_SIZE = 100
_SEASON_CACHE = 'BingieF1.KnabenSeasonSearch'
_SEASON_CACHE_TTL = 300


def search(query, *, include_dead=False):
	"""Return bounded index rows, raising on an unavailable or invalid response."""
	if not isinstance(query, str) or not query.strip() or len(query) > 200: raise ValueError('Invalid Formula 1 query')
	return _page(query, include_dead=include_dead)[0]


def _page(query, offset=0, timeout=8, *, include_dead=False):
	params = {'q': query, 's': _PAGE_SIZE, 'o': 'seeders'}
	if offset: params['f'] = offset
	url = _URL + urlencode(params)
	if include_dead: url += '&dead'
	request = Request(url, headers={'User-Agent': 'BINGIE F1', 'Accept': 'application/json'})
	with urlopen(request, timeout=timeout) as response:
		body = response.read(_LIMIT + 1)
	if len(body) > _LIMIT: raise ValueError('Search response too large')
	data = json.loads(body)
	if not isinstance(data, dict) or not isinstance(data.get('hits'), list): raise ValueError('Invalid search response')
	results, seen = [], set()
	for hit in data['hits'][:_PAGE_SIZE]:
		if not isinstance(hit, dict): continue
		name, info_hash = hit.get('title'), hit.get('hash')
		if not isinstance(name, str) or not name.strip() or len(name) > 2000 or not isinstance(info_hash, str): continue
		info_hash = info_hash.lower()
		if not _HASH.fullmatch(info_hash) or info_hash == '0' * 40 or info_hash in seen: continue
		try: size = max(0, int(hit.get('bytes') or 0))
		except (ValueError, TypeError, OverflowError): size = 0
		seen.add(info_hash)
		results.append({'name': name, 'info_hash': info_hash, 'size': size, 'source': 'knaben'})
	return results, len(data['hits']) >= _PAGE_SIZE


def search_season(year):
	"""Return up to two pages of season candidates and whether a page failed."""
	if type(year) is int: year = str(year)
	if not isinstance(year, str) or not re.fullmatch(r'[0-9]{4}', year): raise ValueError('Invalid Formula 1 season')
	try: from modules import kodi_utils as ku
	except (ImportError, AttributeError): ku = None
	if ku is not None:
		try:
			raw = ku.get_property(_SEASON_CACHE)
			if len(raw) <= _LIMIT:
				entry = json.loads(raw)
				if isinstance(entry, dict) and entry.get('year') == year and type(entry.get('time')) in (int, float) and 0 <= time() - entry['time'] < _SEASON_CACHE_TTL:
					rows = entry.get('rows')
					if isinstance(rows, list) and len(rows) <= 2 * _PAGE_SIZE and all(isinstance(row, dict) for row in rows): return rows, False
		except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
			pass
	query = 'Formula 1 ' + year
	rows, full = _page(query, timeout=4)
	if full:
		try: continuation, _ = _page(query, offset=_PAGE_SIZE, timeout=4)
		except (OSError, HTTPException, ValueError, TypeError, RecursionError): return rows, True
		seen = {row['info_hash'] for row in rows}
		rows.extend(row for row in continuation if row['info_hash'] not in seen)
	if ku is not None:
		try:
			payload = json.dumps({'year': year, 'time': time(), 'rows': rows}, ensure_ascii=False, separators=(',', ':'))
			if len(payload.encode('utf-8')) <= _LIMIT: ku.set_property(_SEASON_CACHE, payload)
		except (AttributeError, RuntimeError, TypeError, ValueError):
			pass
	return rows, False
