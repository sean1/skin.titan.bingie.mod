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
_INITIAL_PLAYBACK_SECONDS = 15
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


def _response_socket(response):
	return getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)


def _playback_timing(started, outcome):
	try:
		import xbmc
		xbmc.log('BINGIE IPTV startup phase=initial_response outcome=%s elapsed_ms=%d' % (outcome, round((time.monotonic() - started) * 1000)), xbmc.LOGINFO)
	except Exception: pass


def _trace_relay(message):
	try:
		import xbmc
		xbmc.log('BINGIE RELAY TRACE ' + message, xbmc.LOGINFO)
	except Exception: pass


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


def register(url, descriptor_path=None, data_request=False):
	"""Register a remote IPTV URL and return a local opaque capability URL."""
	parsed = _url(url)
	port, secret = _descriptor(descriptor_path)
	if parsed.scheme.lower() == 'http' and parsed.hostname == '127.0.0.1' and parsed.port == port and not parsed.query and not parsed.fragment and re.fullmatch(r'/r/[A-Za-z0-9_-]{32,}/resource(?:\.[A-Za-z0-9]{1,8})?', parsed.path): return url
	conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
	try:
		body = json.dumps({'url': url, 'data_request': bool(data_request)}, separators=(',', ':')).encode('utf-8')
		conn.request('POST', '/register', body, {'Authorization': 'Bearer ' + secret, 'Content-Type': 'application/json'})
		response = conn.getresponse()
		data = response.read(_MAX_CONTROL + 1)
		if response.status != 200 or len(data) > _MAX_CONTROL: raise OSError('IPTV VPN relay is unavailable')
		path = json.loads(data.decode('utf-8')).get('path')
		if not isinstance(path, str) or not re.fullmatch(r'/r/[A-Za-z0-9_-]{32,}/resource(?:\.[A-Za-z0-9]{1,8})?', path): raise OSError('Invalid IPTV VPN relay response')
		return 'http://127.0.0.1:%d%s' % (port, path)
	finally: conn.close()


def cancel(url, descriptor_path=None):
	"""Cancel one playback capability and its active relay requests."""
	parsed = _url(url)
	port, secret = _descriptor(descriptor_path)
	if parsed.scheme.lower() != 'http' or parsed.hostname != '127.0.0.1' or parsed.port != port or parsed.query or parsed.fragment or not re.fullmatch(r'/r/[A-Za-z0-9_-]{32,}/resource(?:\.[A-Za-z0-9]{1,8})?', parsed.path): raise ValueError('Invalid IPTV relay capability')
	conn = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
	try:
		body = json.dumps({'path': parsed.path}, separators=(',', ':')).encode('utf-8')
		conn.request('POST', '/cancel', body, {'Authorization': 'Bearer ' + secret, 'Content-Type': 'application/json'})
		response = conn.getresponse()
		response.read(_MAX_CONTROL + 1)
		if response.status != 200: raise OSError('IPTV relay cancellation failed')
	finally: conn.close()


def open_url(url, headers=None, timeout=20, descriptor_path=None):
	"""Open an IPTV URL through the VPN; never use environment proxy settings."""
	local = register(url, descriptor_path, data_request=True)
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
		self.roots = {}
		self.namespaces = {}
		self.active = {}
		self.trace_roots = {}
		self.trace_sequence = 0
		self.started_roots = set()
		self.lock = threading.RLock()
		self.stopping = threading.Event()
		self.connections = set()
		self.server = None
		self.thread = None

	def _register(self, url, root=None, data_request=False):
		parsed = _url(url)
		ext = os.path.splitext(parsed.path)[1]
		if not re.fullmatch(r'\.[A-Za-z0-9]{1,8}', ext or ''): ext = ''
		token = secrets.token_urlsafe(32)
		now = time.monotonic()
		with self.lock:
			if root and root not in self.items: raise OSError('Playback capability expired')
			namespace = 'data' if data_request else 'playback'
			key = (url, root or '', namespace)
			old = self.tokens.get(key)
			item = self.items.pop(old, None) if old else None
			if item and now - item[1] <= _TOKEN_TTL:
				self.items[old] = (url, now)
				return '/r/%s/resource%s' % (old, ext)
			if old:
				self.started_roots.discard(old)
				self.tokens.pop(key, None)
				self.roots.pop(old, None)
				self.namespaces.pop(old, None)
			while self.items and (len(self.items) >= _MAX_ITEMS or now - next(iter(self.items.values()))[1] > _TOKEN_TTL):
				removed_token, (removed_url, _) = self.items.popitem(last=False)
				self.started_roots.discard(removed_token)
				removed_root = self.roots.pop(removed_token, removed_token)
				removed_namespace = self.namespaces.pop(removed_token, 'playback')
				removed_key = (removed_url, '' if removed_root == removed_token else removed_root, removed_namespace)
				if self.tokens.get(removed_key) == removed_token: self.tokens.pop(removed_key, None)
			self.items[token] = (url, now)
			self.roots[token] = root or token
			self.namespaces[token] = namespace
			self.tokens[key] = token
		return '/r/%s/resource%s' % (token, ext)

	def _resolve(self, token):
		now = time.monotonic()
		with self.lock:
			item = self.items.pop(token, None)
			if not item: return None
			if now - item[1] > _TOKEN_TTL:
				self.started_roots.discard(token)
				root = self.roots.pop(token, token)
				namespace = self.namespaces.pop(token, 'playback')
				key = (item[0], '' if root == token else root, namespace)
				if self.tokens.get(key) == token: self.tokens.pop(key, None)
				return None
			self.items[token] = (item[0], now)
			root = self.roots.get(token)
			if root and root != token and root in self.items:
				root_item = self.items.pop(root)
				self.items[root] = (root_item[0], now)
			return item[0]

	def _cancel(self, path):
		match = re.fullmatch(r'/r/([A-Za-z0-9_-]{32,})/resource(?:\.[A-Za-z0-9]{1,8})?', path or '')
		if not match: raise ValueError('Invalid IPTV relay capability')
		with self.lock:
			root = self.roots.get(match.group(1))
			if not root or self.namespaces.get(root) != 'playback': return
			self.started_roots.discard(root)
			for token, item_root in list(self.roots.items()):
				if item_root != root: continue
				item = self.items.pop(token, None)
				self.roots.pop(token, None)
				namespace = self.namespaces.pop(token, 'playback')
				if item:
					key = (item[0], '' if token == root else root, namespace)
					if self.tokens.get(key) == token: self.tokens.pop(key, None)
			handlers = list(self.active.pop(root, ()))
			for handler in handlers: handler.cancelled = True
		for handler in handlers:
			for sock in (handler.connection, handler.response_socket, getattr(getattr(handler, 'upstream', None), 'sock', None)):
				try:
					if sock: sock.shutdown(socket.SHUT_RDWR)
				except OSError: pass

	def _open(self, url, method, headers, timeout=30, data_request=False, handler=None):
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
			connect_timeout = timeout
			if handler and handler.initial_pending:
				connect_timeout = min(timeout, max(0.001, handler.initial_deadline - time.monotonic()))
			connection = (http.client.HTTPSConnection if parsed.scheme.lower() == 'https' else http.client.HTTPConnection)('127.0.0.1', self.proxy_port, timeout=connect_timeout)
			with self.lock:
				if self.stopping.is_set(): raise OSError('IPTV VPN relay is unavailable')
				if handler and handler.cancelled: raise OSError('Playback cancelled')
				if handler and handler.initial_expired: raise TimeoutError('IPTV initial response timed out')
				if handler:
					handler.upstream = connection
					handler.response_socket = None
				self.connections.add(connection)
			auth = base64.b64encode(('bingie:' + self.proxy_password).encode('utf-8')).decode('ascii')
			connection.set_tunnel(parsed.hostname, target_port, headers={'Proxy-Authorization': 'Basic ' + auth})
			path = parsed.path or '/'
			if parsed.query: path += '?' + parsed.query
			keep_open = False
			try:
				connection.request(method, path, headers=hop_headers)
				if handler and handler.cancelled: raise OSError('Playback cancelled')
				response = connection.getresponse()
				if handler:
					with self.lock: handler.response_socket = _response_socket(response)
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

	def _rewrite(self, data, base, root=None):
		text = data.decode('utf-8-sig').lstrip('\ufeff \t\r\n')
		if not text.startswith('#EXTM3U'): raise ValueError('Invalid HLS playlist')
		def local(value):
			if '{$' in value: raise ValueError('HLS variables are unsupported')
			return self._register(urljoin(base, value), root)
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
					if self.path not in ('/register', '/cancel') or not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + owner.secret): return self._error(403)
					length = int(self.headers.get('Content-Length', '0'))
					if not 0 < length <= _MAX_CONTROL: return self._error(400)
					data = json.loads(self.rfile.read(length).decode('utf-8'))
					if self.path == '/register': body = json.dumps({'path': owner._register(data.get('url'), data_request=data.get('data_request') is True)}).encode('utf-8')
					else:
						owner._cancel(data.get('path'))
						body = b'{}'
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
				root = None
				timer = None
				initial_started = None
				initial_outcome = 'failed'
				initial_logged = False
				self.initial_pending = False
				self.initial_expired = False
				self.cancelled = False
				self.upstream = None
				self.response_socket = None
				trace_started = None
				trace_last = None
				trace_bytes = 0
				trace_phase = 'setup'
				trace_outcome = 'complete'
				trace_root = trace_request = trace_active = 0
				try:
					match = re.fullmatch(r'/r/([A-Za-z0-9_-]{32,})/resource(?:\.[A-Za-z0-9]{1,8})?', self.path)
					data_request = hmac.compare_digest(self.headers.get('X-Bingie-Data-Request', ''), owner.secret)
					with owner.lock:
						url = owner._resolve(match.group(1)) if match else None
						if url and not data_request:
							root = owner.roots.get(match.group(1))
							owner.active.setdefault(root, set()).add(self)
							self.initial_pending = method == 'GET' and root == match.group(1) and root not in owner.started_roots
							owner.trace_sequence += 1
							trace_request = owner.trace_sequence
							if root not in owner.trace_roots and len(owner.trace_roots) < _MAX_ITEMS: owner.trace_roots[root] = trace_request
							trace_root = owner.trace_roots.get(root, 0)
							trace_active = sum(len(handlers) for handlers in owner.active.values())
					if trace_request: _trace_relay('start request=%d root=%d method=%s range=%s active=%d' % (trace_request, trace_root, method, bool(self.headers.get('Range')), trace_active))
					if not url: return self._error(404)
					if self.initial_pending:
						initial_started = time.monotonic()
						self.initial_deadline = initial_started + _INITIAL_PLAYBACK_SECONDS
						def expire():
							with owner.lock:
								if not self.initial_pending: return
								self.initial_expired = True
								sock = self.response_socket or getattr(self.upstream, 'sock', None)
								try:
									if sock: sock.shutdown(socket.SHUT_RDWR)
								except OSError: pass
						timer = threading.Timer(_INITIAL_PLAYBACK_SECONDS, expire)
						timer.daemon = True
						timer.start()
					try: timeout = max(1, min(120, float(self.headers.get('X-Bingie-Timeout', '30'))))
					except ValueError: timeout = 30
					connection, response, effective_url = owner._open(url, method, dict(self.headers), timeout=timeout, data_request=data_request, handler=self if not data_request else None)
					content_type = (response.getheader('Content-Type') or '').lower()
					if trace_request:
						trace_length = response.getheader('Content-Length') or ''
						trace_length = int(trace_length) if trace_length.isdecimal() and len(trace_length) <= 20 else -1
						trace_type = 'transport' if 'mp2t' in content_type else 'playlist' if 'mpegurl' in content_type else 'other'
						_trace_relay('response request=%d root=%d status=%d length=%d transfer=%s type=%s' % (trace_request, trace_root, response.status, trace_length, bool(response.getheader('Transfer-Encoding')), trace_type))
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
							body = owner._rewrite(data, effective_url, root)
					if initial_started is not None:
						with owner.lock:
							if self.initial_expired: raise TimeoutError('IPTV initial response timed out')
							if self.cancelled or root not in owner.items: raise OSError('Playback cancelled')
							if self.response_socket and not playlist and response.fp is not None: self.response_socket.settimeout(timeout)
							self.initial_pending = False
							if prefix:
								owner.started_roots.add(root)
								initial_outcome = 'ok'
						timer.cancel()
						_playback_timing(initial_started, initial_outcome)
						initial_logged = True
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
							if not data_request:
								trace_started = trace_last = time.monotonic()
							remaining = end - start + 1 if selection else None
							if prefix:
								chunk = prefix[:remaining] if remaining is not None else prefix
								trace_bytes += len(chunk)
								trace_phase = 'write'
								self.wfile.write(chunk)
								if remaining is not None: remaining -= len(chunk)
							read_chunk = getattr(response, 'read1', response.read)
							while remaining is None or remaining > 0:
								trace_phase = 'read'
								chunk = read_chunk(min(65536, remaining) if remaining is not None else 65536)
								if not chunk:
									trace_outcome = 'eof'
									break
								trace_last = time.monotonic()
								trace_bytes += len(chunk)
								trace_phase = 'write'
								self.wfile.write(chunk)
								if remaining is not None: remaining -= len(chunk)
				except Exception as exc:
					trace_outcome = 'timeout' if isinstance(exc, TimeoutError) else 'error'
					if not sent:
						try: self._error(502)
						except OSError: pass
				finally:
					if trace_started is not None:
						try:
							now = time.monotonic()
							trace_remaining = response.length if isinstance(response.length, int) else -1
							with owner.lock: trace_active = sum(len(handlers) for handlers in owner.active.values())
							_trace_relay('end request=%d root=%d phase=%s outcome=%s elapsed_ms=%d idle_ms=%d bytes=%d remaining=%d active=%d' % (trace_request, trace_root, trace_phase, trace_outcome, round((now - trace_started) * 1000), round((now - trace_last) * 1000), trace_bytes, trace_remaining, trace_active))
						except Exception: pass
					if timer:
						with owner.lock:
							self.initial_pending = False
							if self.initial_expired: initial_outcome = 'timeout'
						timer.cancel()
						if not initial_logged: _playback_timing(initial_started, initial_outcome)
					if connection: owner._release(connection)
					if root:
						with owner.lock:
							handlers = owner.active.get(root)
							if handlers:
								handlers.discard(self)
								if not handlers: owner.active.pop(root, None)
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
			self.roots.clear()
			self.namespaces.clear()
			self.active.clear()
