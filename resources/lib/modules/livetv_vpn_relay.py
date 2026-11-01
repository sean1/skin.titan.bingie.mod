"""Local, fail-closed HTTP relay for IPTV requests through the VPN proxy."""
import base64
import collections
import hmac
import http.client
from http.cookiejar import CookieJar, DefaultCookiePolicy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import secrets
import socket
import tempfile
import threading
import time
from urllib.parse import urljoin, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

_MAX_ITEMS = 10000
_TOKEN_TTL = 12 * 60 * 60
_MAX_PLAYLIST = 4 * 1024 * 1024
_MAX_CONTROL = 65536
_REDIRECTS = frozenset((301, 302, 303, 307, 308))
_HEADERS = frozenset(('accept', 'accept-encoding', 'user-agent'))
_RESPONSE_HEADERS = frozenset(('content-type', 'content-encoding', 'content-length', 'content-range', 'accept-ranges', 'etag', 'last-modified', 'cache-control', 'expires'))
_HLS_URI = re.compile(r'(?i)(?<![a-z0-9_-])([a-z0-9_-]*uri)=("[^"]*"|[^,\s]*)')


class _Server(ThreadingHTTPServer):
	daemon_threads = True

	def handle_error(self, request, client_address): pass


def default_descriptor_path():
	# The manager imports this module, so resolve lazily after its initialization.
	from modules import livetv_vpn
	return livetv_vpn.descriptor_path()


def _url(value):
	if not isinstance(value, str) or not value or len(value) > 32768 or any(ord(c) < 32 or ord(c) == 127 for c in value): raise ValueError('Invalid IPTV URL')
	parsed = urlsplit(value)
	if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None: raise ValueError('Unsupported IPTV URL')
	try: port = parsed.port
	except ValueError: raise ValueError('Invalid IPTV URL') from None
	if port is not None and not 0 < port < 65536: raise ValueError('Invalid IPTV URL')
	return parsed


def _sniff(response):
	"""Read enough of a full representation to identify even disguised HLS."""
	prefix = response.read(3)
	while True:
		probe = prefix
		while True:
			probe = probe.lstrip(b' \t\r\n')
			if not probe.startswith(b'\xef\xbb\xbf'): break
			probe = probe[3:]
		if (probe and not b'\xef\xbb\xbf'.startswith(probe)) or not prefix: break
		if len(prefix) >= 4096: raise ValueError('Unrecognized IPTV response')
		byte = response.read(1)
		if not byte: break
		prefix += byte
	if probe.startswith(b'#') and len(probe) < 7:
		more = response.read(7 - len(probe))
		prefix += more
		probe += more
	if prefix.startswith((b'\xff\xfe', b'\xfe\xff')): return prefix, False, True
	xml = probe.startswith(b'<')
	if b'\x00' in prefix[:3]:
		prefix += response.read(max(0, 64 - len(prefix)))
		for encoding in ('utf-16-le', 'utf-16-be', 'utf-32-le', 'utf-32-be'):
			remaining = prefix
			spaces = tuple(character.encode(encoding) for character in '\ufeff \t\r\n')
			while remaining:
				space = next((value for value in spaces if remaining.startswith(value)), None)
				if not space: break
				remaining = remaining[len(space):]
			if not remaining or remaining.startswith('<'.encode(encoding)):
				xml = True
				break
	return prefix, probe.startswith(b'#EXTM3U'), xml


def _range(value, size):
	"""Return one satisfiable byte range; unsupported requests receive full 200."""
	if not value or size <= 0: return None
	match = re.fullmatch(r'bytes=(\d*)-(\d*)', value.strip())
	if not match or not (match.group(1) or match.group(2)): return None
	try:
		if not match.group(1):
			count = int(match.group(2))
			if count <= 0: return None
			return max(0, size - count), size - 1
		start = int(match.group(1))
		end = min(size - 1, int(match.group(2))) if match.group(2) else size - 1
		return (start, end) if start < size and start <= end else None
	except ValueError: return None


def _descriptor(path=None):
	with open(path or default_descriptor_path(), encoding='utf-8') as handle: data = json.load(handle)
	port, secret = data.get('port'), data.get('secret')
	if not isinstance(port, int) or not 0 < port < 65536 or not isinstance(secret, str) or len(secret) < 32: raise OSError('IPTV VPN relay is unavailable')
	return port, secret


def register(url, descriptor_path=None):
	"""Register a remote IPTV URL and return a local opaque capability URL."""
	parsed = _url(url)
	port, secret = _descriptor(descriptor_path)
	if parsed.scheme.lower() == 'http' and parsed.hostname == '127.0.0.1' and parsed.port == port and not parsed.query and not parsed.fragment and re.fullmatch(r'/r/[A-Za-z0-9_-]{32,}/resource(?:\.[A-Za-z0-9]{1,8})?', parsed.path): return url
	conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
	try:
		body = json.dumps({'url': url}, separators=(',', ':')).encode('utf-8')
		conn.request('POST', '/register', body, {'Authorization': 'Bearer ' + secret, 'Content-Type': 'application/json'})
		response = conn.getresponse()
		data = response.read(_MAX_CONTROL + 1)
		if response.status != 200 or len(data) > _MAX_CONTROL: raise OSError('IPTV VPN relay is unavailable')
		path = json.loads(data.decode('utf-8')).get('path')
		if not isinstance(path, str) or not re.fullmatch(r'/r/[A-Za-z0-9_-]{32,}/resource(?:\.[A-Za-z0-9]{1,8})?', path): raise OSError('Invalid IPTV VPN relay response')
		return 'http://127.0.0.1:%d%s' % (port, path)
	finally: conn.close()


def open_url(url, headers=None, timeout=20, descriptor_path=None):
	"""Open an IPTV URL through the VPN; never use environment proxy settings."""
	local = register(url, descriptor_path)
	port, secret = _descriptor(descriptor_path)
	if urlsplit(local).port != port: raise OSError('IPTV VPN relay changed')
	request_headers = dict(headers or {})
	request_headers['X-Bingie-Timeout'] = str(max(1, min(120, float(timeout))))
	request_headers['X-Bingie-Data-Request'] = secret
	request = Request(local, headers=request_headers)
	return build_opener(ProxyHandler({})).open(request, timeout=timeout)


class Relay:
	"""Serve registered IPTV resources through a loopback-only CONNECT proxy."""
	def __init__(self, proxy_port, proxy_password, descriptor_path=None):
		if not isinstance(proxy_port, int) or not 0 < proxy_port < 65536 or not isinstance(proxy_password, str) or not proxy_password: raise ValueError('Invalid IPTV VPN proxy')
		self.proxy_port, self.proxy_password = proxy_port, proxy_password
		self.descriptor_path = descriptor_path or default_descriptor_path()
		self.secret = secrets.token_urlsafe(32)
		self.items = collections.OrderedDict()
		self.tokens = {}
		self.lock = threading.RLock()
		self.stopping = threading.Event()
		self.connections = set()
		self.server = None
		self.thread = None

	def _register(self, url):
		parsed = _url(url)
		ext = os.path.splitext(parsed.path)[1]
		if not re.fullmatch(r'\.[A-Za-z0-9]{1,8}', ext or ''): ext = ''
		token = secrets.token_urlsafe(32)
		now = time.monotonic()
		with self.lock:
			old = self.tokens.get(url)
			item = self.items.pop(old, None) if old else None
			if item and now - item[1] <= _TOKEN_TTL:
				self.items[old] = (url, now)
				return '/r/%s/resource%s' % (old, ext)
			if old: self.tokens.pop(url, None)
			while self.items and (len(self.items) >= _MAX_ITEMS or now - next(iter(self.items.values()))[1] > _TOKEN_TTL):
				removed_token, (removed_url, _) = self.items.popitem(last=False)
				if self.tokens.get(removed_url) == removed_token: self.tokens.pop(removed_url, None)
			self.items[token] = (url, now)
			self.tokens[url] = token
		return '/r/%s/resource%s' % (token, ext)

	def _resolve(self, token):
		now = time.monotonic()
		with self.lock:
			item = self.items.pop(token, None)
			if not item: return None
			if now - item[1] > _TOKEN_TTL:
				if self.tokens.get(item[0]) == token: self.tokens.pop(item[0], None)
				return None
			self.items[token] = (item[0], now)
			return item[0]

	def _open(self, url, method, headers, timeout=30, data_request=False):
		"""Follow redirects internally; each destination uses explicit CONNECT."""
		headers = {key: value for key, value in headers.items() if key.lower() in _HEADERS and '\r' not in value and '\n' not in value}
		if not data_request:
			headers = {key: value for key, value in headers.items() if key.lower() != 'accept-encoding'}
		if not any(key.lower() == 'accept-encoding' for key in headers): headers['Accept-Encoding'] = 'identity'
		cookies_by_host = {}
		for _ in range(9):
			parsed = _url(url)
			cookies = cookies_by_host.setdefault(parsed.hostname, CookieJar(policy=DefaultCookiePolicy(strict_ns_domain=DefaultCookiePolicy.DomainStrictNonDomain)))
			request = Request(url, headers=headers, method=method)
			cookies.add_cookie_header(request)
			hop_headers = dict(request.header_items())
			target_port = parsed.port or (443 if parsed.scheme.lower() == 'https' else 80)
			connection = (http.client.HTTPSConnection if parsed.scheme.lower() == 'https' else http.client.HTTPConnection)('127.0.0.1', self.proxy_port, timeout=timeout)
			with self.lock:
				if self.stopping.is_set(): raise OSError('IPTV VPN relay is unavailable')
				self.connections.add(connection)
			auth = base64.b64encode(('bingie:' + self.proxy_password).encode('utf-8')).decode('ascii')
			connection.set_tunnel(parsed.hostname, target_port, headers={'Proxy-Authorization': 'Basic ' + auth})
			path = parsed.path or '/'
			if parsed.query: path += '?' + parsed.query
			keep_open = False
			try:
				connection.request(method, path, headers=hop_headers)
				response = connection.getresponse()
				cookies.extract_cookies(response, request)
				if response.status not in _REDIRECTS:
					keep_open = True
					return connection, response, url
				location = response.getheader('Location')
				if not location: raise OSError('IPTV redirect is missing a destination')
				url = urljoin(url, location)
				_url(url)
			finally:
				if not keep_open: self._release(connection)
		raise OSError('Too many IPTV redirects')

	def _release(self, connection):
		with self.lock: self.connections.discard(connection)
		connection.close()

	def _rewrite(self, data, base):
		text = data.decode('utf-8-sig').lstrip('\ufeff \t\r\n')
		if not text.startswith('#EXTM3U'): raise ValueError('Invalid HLS playlist')
		def local(value):
			if '{$' in value: raise ValueError('HLS variables are unsupported')
			return self._register(urljoin(base, value))
		lines = []
		for line in text.splitlines(keepends=True):
			stripped = line.strip()
			if not stripped: lines.append(line); continue
			if not stripped.startswith('#'):
				ending = '\r\n' if line.endswith('\r\n') else '\n' if line.endswith('\n') else ''
				lines.append(local(stripped) + ending)
				continue
			def replace(match):
				value = match.group(2)
				quoted = value.startswith('"') and value.endswith('"')
				value = value[1:-1] if quoted else value
				return match.group(1) + '=' + ('"' if quoted else '') + local(value) + ('"' if quoted else '')
			lines.append(_HLS_URI.sub(replace, line))
		return ''.join(lines).encode('utf-8')

	def start(self):
		if self.server: return self
		self.stopping.clear()
		owner = self
		class Handler(BaseHTTPRequestHandler):
			def log_message(self, *args): pass

			def _error(self, status):
				self.send_response(status)
				self.send_header('X-Bingie-Relay-Error', '1')
				self.send_header('Content-Length', '0')
				self.send_header('Connection', 'close')
				self.end_headers()
				self.close_connection = True

			def do_POST(self):
				try:
					if self.path != '/register' or not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + owner.secret): return self._error(403)
					length = int(self.headers.get('Content-Length', '0'))
					if not 0 < length <= _MAX_CONTROL: return self._error(400)
					data = json.loads(self.rfile.read(length).decode('utf-8'))
					body = json.dumps({'path': owner._register(data.get('url'))}).encode('utf-8')
					self.send_response(200)
					self.send_header('Content-Type', 'application/json')
					self.send_header('Content-Length', str(len(body)))
					self.end_headers()
					self.wfile.write(body)
				except Exception:
					try: self._error(400)
					except OSError: pass

			def do_HEAD(self): self._serve('HEAD')
			def do_GET(self): self._serve('GET')

			def _serve(self, method):
				connection = None
				sent = False
				try:
					match = re.fullmatch(r'/r/([A-Za-z0-9_-]{32,})/resource(?:\.[A-Za-z0-9]{1,8})?', self.path)
					url = owner._resolve(match.group(1)) if match else None
					if not url: return self._error(404)
					try: timeout = max(1, min(120, float(self.headers.get('X-Bingie-Timeout', '30'))))
					except ValueError: timeout = 30
					data_request = hmac.compare_digest(self.headers.get('X-Bingie-Data-Request', ''), owner.secret)
					connection, response, effective_url = owner._open(url, method, dict(self.headers), timeout=timeout, data_request=data_request)
					content_type = (response.getheader('Content-Type') or '').lower()
					if not data_request and ('dash+xml' in content_type or urlsplit(effective_url).path.lower().endswith('.mpd')): return self._error(502)
					if response.status == 206: return self._error(502)
					encoding = (response.getheader('Content-Encoding') or 'identity').strip().lower()
					if not data_request and encoding not in ('', 'identity') and response.status < 400: return self._error(502)
					prefix, sniffed_hls, sniffed_xml = _sniff(response) if not data_request and method == 'GET' and response.status == 200 else (b'', False, False)
					if sniffed_xml: return self._error(502)
					playlist = not data_request and ('mpegurl' in content_type or urlsplit(effective_url).path.lower().endswith('.m3u8') or sniffed_hls)
					body = None
					if playlist:
						if response.status != 200: return self._error(502)
						if method == 'GET':
							data = prefix + response.read(_MAX_PLAYLIST + 1)
							if len(data) > _MAX_PLAYLIST: return self._error(502)
							body = owner._rewrite(data, effective_url)
					selection = None
					length = response.getheader('Content-Length')
					if method == 'GET' and response.status == 200 and not playlist and length and length.isdecimal() and not response.getheader('Transfer-Encoding') and not self.headers.get('If-Range'):
						selection = _range(self.headers.get('Range'), int(length))
					if selection:
						start, end = selection
						if start < len(prefix): prefix = prefix[start:]
						else:
							skip = start - len(prefix)
							prefix = b''
							while skip:
								chunk = response.read(min(65536, skip))
								if not chunk: raise OSError('Incomplete IPTV response')
								skip -= len(chunk)
					self.send_response(206 if selection else response.status)
					for name, value in response.getheaders():
						if selection and name.lower() in ('content-length', 'content-range', 'accept-ranges'): continue
						if name.lower() in _RESPONSE_HEADERS and not (playlist and name.lower() in ('content-length', 'content-encoding', 'content-range', 'etag', 'last-modified', 'cache-control', 'expires')): self.send_header(name, value)
					if selection:
						self.send_header('Content-Length', str(end - start + 1))
						self.send_header('Content-Range', 'bytes %d-%d/%s' % (start, end, length))
						self.send_header('Accept-Ranges', 'bytes')
					if body is not None: self.send_header('Content-Length', str(len(body)))
					if playlist: self.send_header('Cache-Control', 'no-store')
					self.send_header('Connection', 'close')
					self.end_headers()
					sent = True
					if method == 'GET':
						if body is not None: self.wfile.write(body)
						else:
							remaining = end - start + 1 if selection else None
							if prefix:
								chunk = prefix[:remaining] if remaining is not None else prefix
								self.wfile.write(chunk)
								if remaining is not None: remaining -= len(chunk)
							read_chunk = getattr(response, 'read1', response.read)
							while remaining is None or remaining > 0:
								chunk = read_chunk(min(65536, remaining) if remaining is not None else 65536)
								if not chunk: break
								self.wfile.write(chunk)
								if remaining is not None: remaining -= len(chunk)
				except Exception:
					if not sent:
						try: self._error(502)
						except OSError: pass
				finally:
					if connection: owner._release(connection)
					self.close_connection = True

		server = _Server(('127.0.0.1', 0), Handler)
		self.server = server
		try:
			self.thread = threading.Thread(target=server.serve_forever, name='BingieIPTVRelay', daemon=True)
			self.thread.start()
			directory = os.path.dirname(self.descriptor_path)
			os.makedirs(directory, mode=0o700, exist_ok=True)
			fd, path = tempfile.mkstemp(prefix='.livetv-relay-', dir=directory)
			try:
				with os.fdopen(fd, 'w', encoding='utf-8') as handle:
					json.dump({'port': server.server_address[1], 'secret': self.secret}, handle)
				os.replace(path, self.descriptor_path)
			except Exception:
				try: os.unlink(path)
				except OSError: pass
				raise
		except Exception:
			self.close()
			raise
		return self

	def close(self):
		self.stopping.set()
		try:
			with open(self.descriptor_path, encoding='utf-8') as handle: data = json.load(handle)
			if data.get('secret') == self.secret: os.unlink(self.descriptor_path)
		except (OSError, ValueError, TypeError, AttributeError): pass
		server, self.server = self.server, None
		if server:
			server.shutdown()
			server.server_close()
		with self.lock: connections = list(self.connections)
		for connection in connections:
			try:
				if connection.sock: connection.sock.shutdown(socket.SHUT_RDWR)
			except OSError: pass
			self._release(connection)
		if self.thread:
			self.thread.join(timeout=3)
			self.thread = None
		with self.lock:
			self.items.clear()
			self.tokens.clear()
