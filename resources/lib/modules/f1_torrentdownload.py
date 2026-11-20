"""Search TorrentDownload's public index for Formula 1 candidates."""
import re
from html.parser import HTMLParser
from threading import Lock
from time import monotonic, sleep
from urllib.parse import unquote_plus, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


_URL = 'https://www.torrentdownload.info/search?'
_ORIGIN = 'https://www.torrentdownload.info'
_LIMIT = 2 * 1024 * 1024
_PATH = re.compile(r'^/([a-fA-F0-9]{40})/([^/?#]+?)/?$')
_EMPTY = re.compile(r'\b(?:no results?|nothing)\s+(?:found|returned)\b', re.I)
_MAGNET = re.compile(r'^magnet:\?xt=urn:btih:([a-fA-F0-9]{40})(?:&|$)')
_IMAGE = re.compile(r'^/images/docs/(dir\.gif|f_[a-z0-9_]+\.(?:png|gif))$')
_manifest_lock = Lock()
_last_manifest_request = 0.0


def _detail_path(info_hash, path):
	if not isinstance(info_hash, str) or not isinstance(path, str) or len(path) > 650 or any(char.isspace() or ord(char) < 32 or ord(char) >= 127 for char in path): return ''
	match = _PATH.fullmatch(path)
	if not match or match[1].lower() != info_hash or info_hash == '0' * 40: return ''
	slug = unquote_plus(match[2], errors='strict')
	if not slug.strip() or slug in ('.', '..') or any(char in '/\\?#' or ord(char) < 32 or ord(char) == 127 for char in slug): return ''
	if re.search(r'%(?![a-fA-F0-9]{2})', match[2]) or re.search(r'%[a-fA-F0-9]{2}', slug): return ''
	return path


class _NoRedirects(HTTPRedirectHandler):
	def redirect_request(self, req, fp, code, msg, headers, newurl):
		return None


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
		row = {'name': name, 'info_hash': info_hash, 'size': 0, 'source': 'torrentdownload'}
		try: detail = _detail_path(info_hash, href)
		except UnicodeError: detail = ''
		if detail: row['detail_path'] = detail
		self.rows.append(row)

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


class _Manifest(HTMLParser):
	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.paths, self.directory, self.hashes = [], [], set()
		self.section, self.ended, self.heading = False, False, False
		self.flat_indented = False
		self.header = self.hidden = ''
		self.line, self.div_depth, self.indent, self.image, self.text = False, 0, 0, '', ''

	def handle_starttag(self, tag, attrs):
		if self.ended or self.hidden: return
		if tag in ('script', 'style', 'template', 'svg'):
			self.hidden = tag
			return
		values = dict(attrs)
		classes = (values.get('class') or '').split()
		if tag == 'a' and not self.section:
			magnet = _MAGNET.match(values.get('href') or '')
			if magnet: self.hashes.add(magnet[1].lower())
		if tag == 'table':
			if self.section: raise ValueError('Unexpected nested file table')
			self.section = 'torrentcontent' in classes
		if not self.section: return
		if tag == 'th': self.header = tag
		if tag == 'div':
			if self.line:
				self._entry()
				self.div_depth += 1
			elif 'fileline' in classes and self.heading:
				self.line, self.div_depth = True, 1
		if self.line and self.div_depth == 1:
			if tag == 'br': self._entry()
			if tag == 'img':
				icon = _IMAGE.fullmatch(values.get('src') or '')
				if icon:
					if self.image: raise ValueError('Ambiguous file entry')
					self.image = icon[1]

	def handle_data(self, text):
		if not self.section or self.hidden: return
		if self.header and text.strip() == 'Torrent Files':
			self.heading = True
		if not self.line or self.div_depth != 1: return
		if self.image:
			if len(self.text) + len(text) > 512: raise ValueError('File entry is too long')
			self.text += text
		elif not text.strip(): self.indent += text.count('\u00a0')

	def handle_endtag(self, tag):
		if self.hidden:
			if tag == self.hidden: self.hidden = ''
			return
		if not self.section: return
		if tag == self.header: self.header = ''
		if tag == 'div' and self.line:
			if self.div_depth == 1:
				self._entry()
				self.line = False
			self.div_depth -= 1
		if tag == 'table':
			if self.line: raise ValueError('Incomplete file entry')
			self.section, self.ended = False, True

	def _entry(self):
		if self.image:
			name = self.text.strip()
			level = self.indent // 4
			is_directory = self.image == 'dir.gif'
			if not is_directory: name = name.removesuffix(' -').rstrip()
			if not self.paths and not self.directory and level == 1 and not is_directory: self.flat_indented = True
			if self.flat_indented:
				if level != 1 or is_directory: raise ValueError('Mixed file layout')
				level = 0
			if self.indent % 4 or level > len(self.directory) or level > 12 or not _valid_component(name): raise ValueError('Invalid file entry')
			if is_directory:
				self.directory[level:] = [name]
			else:
				path = '/'.join(self.directory[:level] + [name])
				if len(path) > 1024 or len(self.paths) >= 1000: raise ValueError('File list limit exceeded')
				self.paths.append(path)
		self.indent, self.image, self.text = 0, '', ''


def _valid_component(value):
	return 0 < len(value) <= 255 and value not in ('.', '..') and all(c not in '/\\' and ord(c) >= 32 and ord(c) != 127 for c in value)


def manifest_files(info_hash, detail_path):
	"""Read bounded file metadata for a validated search result."""
	global _last_manifest_request
	path = _detail_path(info_hash, detail_path)
	if not path: raise ValueError('Invalid file list path')
	url = _ORIGIN + path
	request = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html'})
	with _manifest_lock:
		delay = _last_manifest_request + 1 - monotonic()
		if delay > 0: sleep(delay)
		_last_manifest_request = monotonic()
	with build_opener(_NoRedirects()).open(request, timeout=3) as response:
		if response.geturl() != url or response.headers.get_content_type() != 'text/html': raise ValueError('Unexpected file list response')
		body = response.read(_LIMIT + 1)
	if len(body) > _LIMIT: raise ValueError('File list response too large')
	parser = _Manifest()
	parser.feed(body.decode('utf-8'))
	parser.close()
	if not parser.ended or not parser.heading or parser.hashes != {info_hash} or not parser.paths: raise ValueError('Unverified file list')
	return parser.paths
