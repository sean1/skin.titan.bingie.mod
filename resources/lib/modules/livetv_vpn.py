"""Service-owned Mullvad tunnel for Live TV; never fall back to direct access."""
import base64
import ctypes
import hashlib
import http.client
import json
import os
import platform
import secrets
import socket
import stat
import tempfile
import threading

from modules import kodi_utils as ku

_ANDROID_PRIVATE = '/data/user/0/org.xbmc.kodi/files'
_ACCOUNT_REL = ('resources', 'private', 'mullvad-account.txt')
_LIBRARY_REL = ('resources', 'lib', 'vpn', 'bin')
_LIBRARY_LIMIT = 100 * 1024 * 1024
_RETRY_SECONDS = 20
_CHECK_SECONDS = 5
_HEALTH_SECONDS = 60
_HEALTH_FAILURE_LIMIT = 3
_STATUS_PROPERTY = 'BingieLiveTVVPNStatus'
_FAILURE_MESSAGES = {
	'account': 'Mullvad account setup is invalid. Check the bundled account number.',
	'device_limit': 'Mullvad has no free device slots. Remove an unused device from your Mullvad account, then retry.',
	'rejected': 'Mullvad rejected registration. Check the account and available device slots.',
	'pending': 'Mullvad registration needs recovery. Check the device list and retry later.',
}
_library_lock = threading.Lock()
_loaded_library = None
_loaded_digest = None


class VPNUnavailable(RuntimeError):
	"""An IPTV VPN component is unavailable without exposing private details."""


def _android():
	return bool(ku.get_visibility('System.Platform.Android'))


def private_directory():
	"""Return the per-device private directory shared by Kodi's interpreters."""
	if _android():
		base = _ANDROID_PRIVATE
	else:
		base = ku.translate_path(ku.profile_path)
	if not isinstance(base, str) or not base or not os.path.isabs(base): raise VPNUnavailable('VPN private storage unavailable')
	return os.path.join(base, 'livetv-vpn')


def descriptor_path():
	return os.path.join(private_directory(), 'relay.json')


def failure_message():
	"""Return fixed user-facing guidance, never raw provisioning errors."""
	try: return _FAILURE_MESSAGES.get(ku.get_property(_STATUS_PROPERTY), '')
	except Exception: return ''


def _set_failure(code=''):
	try: ku.set_property(_STATUS_PROPERTY, code if code in _FAILURE_MESSAGES else '')
	except Exception: pass


def _secure_directory():
	path = private_directory()
	os.makedirs(path, mode=0o700, exist_ok=True)
	info = os.lstat(path)
	if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid(): raise VPNUnavailable('VPN private storage unavailable')
	os.chmod(path, 0o700)
	return path


def _sha256(path):
	digest = hashlib.sha256()
	with open(path, 'rb') as source:
		if not stat.S_ISREG(os.fstat(source.fileno()).st_mode): raise VPNUnavailable('VPN helper unavailable')
		size = 0
		while True:
			chunk = source.read(1024 * 1024)
			if not chunk: break
			size += len(chunk)
			if size > _LIBRARY_LIMIT: raise VPNUnavailable('VPN helper unavailable')
			digest.update(chunk)
	if not size: raise VPNUnavailable('VPN helper unavailable')
	return digest.hexdigest()


def _packaged_library():
	arch = platform.machine().lower()
	android = _android()
	word_size = ctypes.sizeof(ctypes.c_void_p)
	if android and word_size == 8 and arch in ('aarch64', 'arm64'):
		name = 'libbingie_vpn_android_arm64.so'
	elif android and word_size == 4 and (arch.startswith('arm') or arch == 'aarch64'):
		name = 'libbingie_vpn_android_armv7.so'
	elif not android and platform.system() == 'Linux' and arch in ('x86_64', 'amd64'):
		name = 'libbingie_vpn_linux_amd64.so'
	else:
		raise VPNUnavailable('VPN is not supported on this device')
	return os.path.join(ku.translate_path(ku.addon_path), *_LIBRARY_REL, name)


def _internal_library(source, digest):
	"""Copy the verified packaged library to Android app-private storage."""
	directory = _secure_directory()
	target = os.path.join(directory, 'libbingie-vpn-' + digest + '.so')
	if os.path.lexists(target):
		info = os.lstat(target)
		if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or _sha256(target) != digest: raise VPNUnavailable('VPN helper unavailable')
		return target
	fd, temporary = tempfile.mkstemp(prefix='.vpn-library-', dir=directory)
	try:
		os.fchmod(fd, 0o500)
		with os.fdopen(fd, 'wb') as dest, open(source, 'rb') as src:
			while True:
				chunk = src.read(1024 * 1024)
				if not chunk: break
				dest.write(chunk)
			dest.flush()
			os.fsync(dest.fileno())
		if _sha256(temporary) != digest: raise VPNUnavailable('VPN helper unavailable')
		os.replace(temporary, target)
		return target
	finally:
		try: os.unlink(temporary)
		except FileNotFoundError: pass


def _library():
	"""Keep the Go shared library loaded once for the lifetime of Kodi."""
	global _loaded_library, _loaded_digest
	with _library_lock:
		source = _packaged_library()
		digest = _sha256(source)
		if _loaded_library is not None:
			if digest != _loaded_digest: raise VPNUnavailable('Restart Kodi to update the VPN helper')
			return _loaded_library
		path = _internal_library(source, digest) if _android() else source
		library = ctypes.CDLL(path)
		library.Start.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
		library.Start.restype = ctypes.c_int
		library.Status.argtypes = []
		library.Status.restype = ctypes.c_int
		library.Stop.argtypes = []
		library.Stop.restype = ctypes.c_int
		_loaded_library, _loaded_digest = library, digest
		return library


def _unused_port():
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
		reserved.bind(('127.0.0.1', 0))
		return reserved.getsockname()[1]


def _verify_exit(port, password):
	"""Verify Mullvad over the CONNECT-only proxy without any direct fallback."""
	credentials = base64.b64encode(('bingie:' + password).encode('ascii')).decode('ascii')
	connection = http.client.HTTPSConnection('127.0.0.1', port, timeout=8)
	connection.set_tunnel('am.i.mullvad.net', 443, headers={'Proxy-Authorization': 'Basic ' + credentials})
	try:
		connection.request('GET', '/json', headers={'Accept': 'application/json'})
		response = connection.getresponse()
		if response.status != 200: return False
		payload = response.read(16385)
		if len(payload) > 16384: return False
		return json.loads(payload).get('mullvad_exit_ip') is True
	finally:
		connection.close()


def _signature(path):
	info = os.lstat(path)
	if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077: raise VPNUnavailable('VPN configuration unavailable')
	return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size


class VPNManager:
	"""Start and stop the device tunnel and IPTV relay with the Kodi service."""
	def __init__(self):
		self._stop = threading.Event()
		self._lock = threading.Lock()
		self._thread = None
		self._state = 'VPN not started'

	def state(self):
		with self._lock: return self._state

	def _set_state(self, state):
		with self._lock: self._state = state

	def start(self):
		with self._lock:
			if self._thread is not None: return
			_set_failure()
			self._stop.clear()
			self._state = 'Connecting IPTV VPN'
			self._thread = threading.Thread(target=self._run, name='BINGIE IPTV VPN', daemon=True)
			self._thread.start()

	def close(self):
		self._stop.set()
		with self._lock: thread = self._thread
		if thread is not None and thread is not threading.current_thread(): thread.join(60)
		with self._lock:
			if thread is not None and thread.is_alive(): self._state = 'IPTV VPN is stopping'
			else:
				self._thread = None
				self._state = 'VPN stopped'

	def _run(self):
		connection_failures = 0
		while not self._stop.is_set():
			library = None
			relay = None
			started = False
			connection_failed = False
			stopped = True
			try:
				from modules import livetv_vpn_provision, livetv_vpn_relay
				private = _secure_directory()
				library = _library()
				account_path = os.path.join(ku.translate_path(ku.addon_path), *_ACCOUNT_REL)
				try: config = livetv_vpn_provision.ensure_config(private, account_path, library)
				except livetv_vpn_provision.ProvisionError as error:
					if isinstance(error, livetv_vpn_provision.AccountError): _set_failure('account')
					elif isinstance(error, livetv_vpn_provision.DeviceLimitError): _set_failure('device_limit')
					elif isinstance(error, livetv_vpn_provision.RegistrationPendingError): _set_failure('pending')
					else: _set_failure()
					raise
				_set_failure()
				if self._stop.is_set(): break
				private_real = os.path.realpath(private)
				if os.path.commonpath((private_real, os.path.realpath(config))) != private_real: raise VPNUnavailable('VPN configuration unavailable')
				signature = _signature(config)
				port = _unused_port()
				password = secrets.token_urlsafe(32)
				result = library.Start(os.fsencode(config), password.encode('ascii'), port)
				if result != 0:
					connection_failed = result == 4
					raise VPNUnavailable('IPTV VPN could not start')
				started = True
				self._set_state('Connecting IPTV VPN')
				verified = False
				for _ in range(3):
					if self._stop.is_set(): break
					try: verified = _verify_exit(port, password)
					except Exception: verified = False
					if verified: break
					if self._stop.wait(_CHECK_SECONDS): break
				if not verified or self._stop.is_set():
					connection_failed = not self._stop.is_set()
					raise VPNUnavailable('IPTV VPN is unavailable')
				relay = livetv_vpn_relay.Relay(port, password, descriptor_path())
				relay.start()
				self._set_state('IPTV VPN connected')
				failures = 0
				while not self._stop.wait(_HEALTH_SECONDS):
					if _signature(config) != signature: break
					try: healthy = relay.thread is not None and relay.thread.is_alive() and library.Status() in (1, 2) and _verify_exit(port, password)
					except Exception: healthy = False
					if healthy: connection_failures = 0
					failures = 0 if healthy else failures + 1
					if failures >= _HEALTH_FAILURE_LIMIT:
						connection_failed = True
						raise VPNUnavailable('IPTV VPN is unavailable')
			except Exception:
				self._set_state('IPTV VPN unavailable')
			finally:
				if relay is not None:
					try: relay.close()
					except Exception: self._set_state('IPTV VPN unavailable')
				if started and library is not None:
					try:
						if library.Stop() != 0: stopped = False; self._set_state('IPTV VPN unavailable')
					except Exception: stopped = False; self._set_state('IPTV VPN unavailable')
			if connection_failed and not self._stop.is_set():
				connection_failures += 1
				if connection_failures >= 3 and stopped:
					try:
						livetv_vpn_provision.rotate_relay(private)
						connection_failures = 0
					except Exception: self._set_state('IPTV VPN unavailable')
			if not self._stop.is_set(): self._stop.wait(_RETRY_SECONDS)
