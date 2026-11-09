"""Search TorrentDownload's public index for Formula 1 candidates."""
import re
from html.parser import HTMLParser
from urllib.parse import unquote_plus, urlencode, urlsplit
from urllib.request import Request, urlopen


_URL = 'https://www.torrentdownload.info/search?'
_LIMIT = 2 * 1024 * 1024
_PATH = re.compile(r'^/([a-fA-F0-9]{40})/([^/?#]+?)/?$')
_EMPTY = re.compile(r'\b(?:no results?|nothing)\s+(?:found|returned)\b', re.I)


class _Results(HTMLParser):
	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.rows, self.seen, self.text = [], set(), []

	def handle_starttag(self, tag, attrs):
		if tag != 'a' or len(self.rows) >= 100: return
		values = dict(attrs)
		if 'nofollow' in (values.get('rel') or '').casefold().split(): return
		href = values.get('href') or ''
		if not href.startswith('/') or href.startswith('//'): return
		parts = urlsplit(href)
		if parts.query or parts.fragment: return
		match = _PATH.fullmatch(parts.path)
		if not match: return
		info_hash = match.group(1).lower()
		name = unquote_plus(match.group(2)).strip()
		if info_hash == '0' * 40 or info_hash in self.seen or not name or len(name) > 2000: return
		self.seen.add(info_hash)
		self.rows.append({'name': name, 'info_hash': info_hash, 'size': 0, 'source': 'torrentdownload'})

	def handle_data(self, data):
		if len(self.text) < 4096: self.text.append(data)


def search(query):
	"""Return validated results; raise if the public search is unavailable."""
	if not isinstance(query, str) or not query.strip() or len(query) > 200: raise ValueError('Invalid Formula 1 query')
	request = Request(_URL + urlencode({'q': query}), headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html'})
	with urlopen(request, timeout=15) as response:
		body = response.read(_LIMIT + 1)
	if len(body) > _LIMIT: raise ValueError('Search response too large')
	parser = _Results()
	parser.feed(body.decode('utf-8', errors='replace'))
	parser.close()
	if not parser.rows and not _EMPTY.search(' '.join(parser.text)): raise ValueError('Invalid search response')
	return parser.rows
