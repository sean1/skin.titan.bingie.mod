"""Service-owned Mullvad tunnel for Live TV; never fall back to direct access."""
import base64
import ctypes
import hashlib
import http.client
import json
import os
import platform
import re
import secrets
import socket
import stat
import tempfile
import threading
from time import monotonic

from modules import kodi_utils as ku

_ANDROID_PRIVATE = '/data/user/0/org.xbmc.kodi/files'
_ACCOUNT_REL = ('resources', 'private', 'mullvad-account.txt')
_LIBRARY_REL = ('resources', 'lib', 'vpn', 'bin')
_LIBRARY_LIMIT = 100 * 1024 * 1024
_RETRY_SECONDS = 20
_CHECK_SECONDS = 5
_HEALTH_SECONDS = 60
_HEALTH_FAILURE_LIMIT = 3
_REQUEST_SECONDS = 60
_ACCOUNT_CHECK_INTERVAL = 15
_BINDING_RETRY_SECONDS = 5 * 60
_STATUS_PROPERTY = 'BingieLiveTVVPNStatus'
_REQUEST_PROPERTY = 'BingieLiveTVVPNRequest'
_SPEED_REQUEST_PROPERTY = 'BingieLiveTVVPNSpeedRequest'
_SPEED_STATUS_PROPERTY = 'BingieLiveTVVPNSpeedStatus'
_SPEED_PAUSE_PROPERTY = 'BingieLiveTVVPNSpeedPaused'
_SPEED_CATALOG_PROBLEMS = 'BingieLiveTVVPNCatalogProblems'
_CATALOG_ACCESS_PROBLEMS = 'BingieLiveTVVPNCatalogAccessProblems'
_SPEED_QUEUED = 'VPN server test queued until Kodi is idle.'
_SPEED_TESTING = 'Comparing VPN servers'
_SPEED_AUTO_TESTING = 'Automatic VPN test running'
_SPEED_AUTO_INTERVAL = 7 * 24 * 60 * 60
_SPEED_AUTO_IDLE = 15 * 60
_SPEED_DEGRADED_INTERVAL = 6 * 60 * 60
_SPEED_DEGRADED_IDLE = 2 * 60
_CATALOG_RECOVERY_INTERVAL = 15 * 60
_CATALOG_ACCESS_LIMIT = 12
_SPEED_PROBLEM_WINDOW = 30 * 60
_SPEED_CANDIDATES = 3
_SPEED_CATALOG_SECONDS = 45
_FAILURE_MESSAGES = {
	'account': 'Mullvad account setup is invalid. Check the bundled account number.',
	'device_limit': 'Mullvad has no free device slots. Remove an unused device from your Mullvad account, then retry.',
	'rejected': 'Mullvad rejected registration. Check the account and available device slots.',
	'pending': 'Mullvad registration needs recovery. Check the device list and retry later.',
	'connecting': 'Connecting to the Live TV VPN. Try again in a moment.',
}
_library_lock = threading.Lock()
_catalog_problem_lock = threading.Lock()
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


def request_connection():
	"""Allow one registration attempt in response to a Live TV action."""
	ku.set_property(_REQUEST_PROPERTY, str(monotonic()))


def _requested_at():
	try:
		requested = float(ku.get_property(_REQUEST_PROPERTY))
		return requested if 0 <= monotonic() - requested < _REQUEST_SECONDS else 0.0
	except (ValueError, TypeError): return 0.0


def speed_test_status():
	return ku.get_property(_SPEED_STATUS_PROPERTY)


def request_speed_test():
	status = speed_test_status()
	if _speed_requested_at() or status == _SPEED_QUEUED or status.startswith(_SPEED_TESTING): return False
	ku.set_property(_SPEED_STATUS_PROPERTY, _SPEED_QUEUED)
	ku.set_property(_SPEED_REQUEST_PROPERTY, str(monotonic()))
	return True


def _speed_requested_at():
	try: return float(ku.get_property(_SPEED_REQUEST_PROPERTY))
	except (ValueError, TypeError): return 0.0


def _speed_result(message, *, automatic=False):
	if automatic:
		ku.logger('Live TV', 'Automatic VPN test: ' + message)
		if _speed_requested_at(): return
	ku.set_property(_SPEED_STATUS_PROPERTY, message)
	if not automatic: ku.dialog.notification('Live TV', message, time=7000)


def _automatic_speed_idle(seconds):
	return not (_requested_at() or _speed_requested_at()) and ku.get_visibility('System.IdleTime(%d)' % seconds) and not ku.get_visibility(
		'Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)')


def _recent_catalog_problems(since=0):
	try: observed = [float(value) for value in ku.get_property(_SPEED_CATALOG_PROBLEMS).split()]
	except (ValueError, AttributeError): return []
	now = monotonic()
	return [when for when in observed[-2:] if when > since and 0 <= now - when <= _SPEED_PROBLEM_WINDOW]


def report_catalog_problem():
	"""Share slow or unusable production catalog results with the Kodi service."""
	try:
		if ku.get_property(_SPEED_PAUSE_PROPERTY) or speed_test_status().startswith((_SPEED_TESTING, _SPEED_AUTO_TESTING)): return
		with _catalog_problem_lock:
			observed = [*_recent_catalog_problems()[-1:], monotonic()]
			ku.set_property(_SPEED_CATALOG_PROBLEMS, ' '.join(map(str, observed)))
	except Exception: pass


def _recent_catalog_access_problems(since=0):
	try: records = json.loads(ku.get_property(_CATALOG_ACCESS_PROBLEMS))
	except (ValueError, TypeError): return []
	if not isinstance(records, list): return []
	now = monotonic()
	recent = []
	for record in records[-_CATALOG_ACCESS_LIMIT:]:
		if not isinstance(record, list) or len(record) != 3: continue
		started, provider, action = record
		if type(started) in (int, float) and started > since and 0 <= now - started <= _SPEED_PROBLEM_WINDOW and isinstance(provider, str) and isinstance(action, str):
			recent.append((started, provider, action))
	return recent


def report_catalog_access_problem(provider, action, started):
	"""Share a failed upstream catalog request with the Kodi service."""
	if not isinstance(provider, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', provider) or action not in ('get_live_categories', 'get_live_streams'): return
	try:
		if ku.get_property(_SPEED_PAUSE_PROPERTY) or speed_test_status().startswith((_SPEED_TESTING, _SPEED_AUTO_TESTING)): return
		with _catalog_problem_lock:
			records = sorted(set([*_recent_catalog_access_problems(), (started, provider, action)]))
			ku.set_property(_CATALOG_ACCESS_PROBLEMS, json.dumps(records[-_CATALOG_ACCESS_LIMIT:]))
	except Exception: pass


def _catalog_recovery(since, handled):
	seen = set()
	for started, provider, action in reversed(_recent_catalog_access_problems(since)):
		check = (provider, action)
		if started <= handled.get(check, 0): continue
		if check in seen: return check
		seen.add(check)
	return None


def wait_for_connection(seconds=4):
	"""Give an explicit Live TV action a short, abortable wait for the relay."""
	from modules.livetv_vpn_relay import _descriptor
	deadline = monotonic() + seconds
	while True:
		try:
			if not ku.get_property(_SPEED_PAUSE_PROPERTY):
				port, _ = _descriptor()
				with socket.create_connection(('127.0.0.1', port), timeout=0.1): return not ku.get_property(_SPEED_PAUSE_PROPERTY)
		except (OSError, ValueError, TypeError, AttributeError): pass
		remaining = deadline - monotonic()
		if remaining <= 0 or ku.monitor.waitForAbort(min(0.1, remaining)): return False


def _set_failure(code=''):
	try: ku.set_property(_STATUS_PROPERTY, code if code in _FAILURE_MESSAGES else '')
	except Exception: pass


def _set_provision_failure(provision, error):
	if isinstance(error, provision.AccountError): _set_failure('account')
	elif isinstance(error, provision.DeviceLimitError): _set_failure('device_limit')
	elif isinstance(error, provision.RegistrationPendingError): _set_failure('pending')
	else: _set_failure()


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


def _verify_exit(port, password, hostname):
	"""Verify the expected Mullvad peer over the CONNECT-only proxy."""
	credentials = base64.b64encode(('bingie:' + password).encode('ascii')).decode('ascii')
	connection = http.client.HTTPSConnection('127.0.0.1', port, timeout=8)
	connection.set_tunnel('am.i.mullvad.net', 443, headers={'Proxy-Authorization': 'Basic ' + credentials})
	try:
		connection.request('GET', '/json', headers={'Accept': 'application/json'})
		response = connection.getresponse()
		if response.status != 200: return False
		payload = response.read(16385)
		if len(payload) > 16384: return False
		data = json.loads(payload)
		return data.get('mullvad_exit_ip') is True and data.get('mullvad_exit_ip_hostname') == hostname
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

	def _wait_for_check(self, seconds, seen_request, seen_speed=None):
		deadline = monotonic() + seconds
		while not self._stop.is_set() and _requested_at() <= seen_request:
			if seen_speed is not None and _speed_requested_at() > seen_speed and not ku.get_visibility('Player.HasMedia'): break
			remaining = deadline - monotonic()
			if remaining <= 0 or self._stop.wait(min(1, remaining)): break

	def _probe_speed(self, library, probe_config, probe_descriptor, candidate, accounts, cancel, repair=None):
		from modules import livetv_vpn_relay, livetv_vpn_speed
		port, password = _unused_port(), secrets.token_urlsafe(32)
		if cancel(): return {}
		started = library.Start(os.fsencode(probe_config), password.encode('ascii'), port)
		if started == 2: raise VPNUnavailable('IPTV VPN was still running before testing')
		if started != 0: return {}
		probe = None
		try:
			if cancel() or not _verify_exit(port, password, candidate['hostname']) or cancel(): return {}
			probe = livetv_vpn_relay.Relay(port, password, probe_descriptor)
			probe.start()
			return livetv_vpn_speed.measure_exit(accounts, probe_descriptor, timeout=_SPEED_CATALOG_SECONDS, cancel=cancel, repair=repair)
		except (OSError, http.client.HTTPException, ValueError, TypeError, AttributeError): return {}
		finally:
			try:
				if probe is not None: probe.close()
			finally:
				if library.Stop() != 0: raise VPNUnavailable('IPTV VPN test could not stop')

	def _compare_speed(self, private, library, config, signature, port, password, *, automatic_idle=0, repair=None):
		from menus import livetv
		from modules import livetv_vpn_provision, livetv_vpn_speed
		live_request = _requested_at()
		automatic = bool(automatic_idle)
		status = _SPEED_AUTO_TESTING if automatic else _SPEED_TESTING

		def finish(message):
			_speed_result(message, automatic=automatic)

		def progress(message):
			if not automatic or not _speed_requested_at(): ku.set_property(_SPEED_STATUS_PROPERTY, status + '. ' + message)

		def cancelled():
			if self._stop.is_set() or _requested_at() > live_request or ku.get_visibility('Player.HasMedia'): return True
			if automatic and not _automatic_speed_idle(automatic_idle): return True
			try: return _signature(config) != signature
			except (OSError, VPNUnavailable): return True

		if automatic:
			if cancelled():
				finish('VPN server test cancelled.')
				return None
			reason = 'after repeated provider errors' if repair else 'after slow or failed Live TV catalogs' if automatic_idle == _SPEED_DEGRADED_IDLE else 'on the weekly schedule'
			ku.logger('Live TV', 'Starting automatic VPN speed test %s.' % reason)
		progress('Finding alternatives.')
		current = livetv_vpn_provision.current_relay(private, signature)
		accounts = livetv._sports_accounts(livetv._load())
		if not current or not accounts:
			finish('No VPN server comparison is available.')
			return None
		if repair:
			provider, action = repair
			identity = next((identity for identity, account in accounts if (account.get('id') or 'primary') == provider), None)
			if identity is None:
				finish('The affected Live TV account is no longer available.')
				return None
			repair = (identity, action)
		relays = livetv_vpn_provision.relay_candidates()
		current = livetv_vpn_provision._listed_relay(relays, current)
		if not current:
			finish('No VPN server comparison is available.')
			return None
		current_name = current['hostname']
		alternates = [peer for peer in relays if peer['public_key'] != current['public_key']]
		if not alternates:
			finish('No alternate VPN server is available.')
			return None

		def log_measurement(hostname, timings):
			seconds = ', '.join('%s %.2f' % ('/'.join(check) if repair else check, value) for check, value in sorted(timings.items()))
			checks = len(accounts) + bool(repair and repair[1] == 'get_live_categories')
			ku.logger('Live TV', 'VPN server test: %s %d/%d | seconds %s' % (hostname, len(timings), checks, seconds))

		progress('Testing the current server.')
		baseline = livetv_vpn_speed.measure_exit(accounts, descriptor_path(), timeout=_SPEED_CATALOG_SECONDS, cancel=cancelled, repair=repair)
		log_measurement(current_name, baseline)
		if repair and repair in baseline:
			finish('The provider catalog recovered on %s.' % current_name)
			return None
		preferred = livetv_vpn_provision._PREFERRED_RELAY_HOSTNAME if repair else None
		candidates = livetv_vpn_speed.shortlist_relays(alternates, current['host'], limit=_SPEED_CANDIDATES, cancel=cancelled, preferred_hostname=preferred)
		if not candidates:
			finish('VPN server test cancelled.' if cancelled() else 'No alternate VPN server is available.')
			return None
		results = []
		switched = None
		with tempfile.TemporaryDirectory(prefix='.relay-probe-', dir=private) as directory:
			staged = []
			for index, candidate in enumerate(candidates, 1):
				if cancelled(): break
				probe_base = os.path.join(directory, 'relay-%d' % index)
				if livetv_vpn_provision.stage_relay(private, candidate, signature, probe_base + '.conf'):
					staged.append((candidate, probe_base))
			if staged and not cancelled():
				ku.set_property(_SPEED_PAUSE_PROPERTY, '1')
				if library.Stop() != 0: raise VPNUnavailable('IPTV VPN could not pause for testing')
				can_resume = True
				try:
					for index, (candidate, probe_base) in enumerate(staged, 1):
						if cancelled(): break
						progress('Alternate %d of %d. Live TV is paused.' % (index, len(staged)))
						ping = candidate['ping_ms']
						latency = '%.1f ms median %s ping' % (ping, candidate['ping_method']) if ping is not None else 'no latency replies'
						ku.logger('Live TV', 'VPN candidate %d of %d: %s (%s)' % (index, len(staged), candidate['hostname'], latency))
						result = self._probe_speed(library, probe_base + '.conf', probe_base + '.json', candidate, accounts, cancelled, repair=repair)
						results.append((candidate, result))
						log_measurement(candidate['hostname'], result)
					eligible = results if not repair else [(candidate, timings) for candidate, timings in results if repair in timings]
					winner = livetv_vpn_speed.best_relay(eligible, baseline, preferred_hostname=preferred)
					if winner and not cancelled():
						new_signature = livetv_vpn_provision.select_relay(private, winner, signature)
						if new_signature: switched = (current, winner, new_signature, automatic_idle)
				except VPNUnavailable:
					can_resume = False
					raise
				finally:
					if can_resume and not switched and not self._stop.is_set():
						try: unchanged = _signature(config) == signature
						except (OSError, VPNUnavailable): unchanged = False
						if not unchanged or library.Start(os.fsencode(config), password.encode('ascii'), port) != 0:
							raise VPNUnavailable('IPTV VPN could not resume after testing')
						try: verified = _verify_exit(port, password, current_name) and _signature(config) == signature
						except Exception: verified = False
						if not verified: raise VPNUnavailable('IPTV VPN could not resume after testing')
						ku.clear_property(_SPEED_PAUSE_PROPERTY)
		if switched: return switched
		if cancelled(): finish('VPN server test cancelled.')
		elif not results: finish('No alternate VPN server could be tested.')
		elif baseline or any(result for _, result in results): finish('Kept %s. No tested server improved Live TV.' % current_name)
		else: finish('No tested server completed a Live TV catalog. Kept %s.' % current_name)
		return None

	def _run(self):
		from modules import livetv_vpn_provision, livetv_vpn_relay
		connection_failures = 0
		handled_request = seen_request = checked_request = account_check_after = 0.0
		binding_signature, binding_retry = None, 0.0
		revoked_signature = None
		seen_speed = 0.0
		seen_catalog_problems = 0.0
		speed_switch = None
		approved_relay = None
		if speed_test_status().startswith((_SPEED_TESTING, _SPEED_AUTO_TESTING)): ku.clear_property(_SPEED_STATUS_PROPERTY)
		while not self._stop.is_set():
			applying_speed = speed_switch is not None
			reconnect = False
			library = None
			relay = None
			started = False
			connection_failed = False
			stopped = True
			replaced = False
			signature = None
			try:
				private = _secure_directory()
				library = _library()
				account_path = os.path.join(ku.translate_path(ku.addon_path), *_ACCOUNT_REL)
				requested = _requested_at()
				seen_request = max(seen_request, requested)
				try:
					config = livetv_vpn_provision.ensure_config(private, account_path, library, allow_registration=False)
					if not config:
						if requested <= handled_request or self._stop.is_set():
							if _speed_requested_at() > seen_speed:
								seen_speed = _speed_requested_at()
								ku.clear_property(_SPEED_REQUEST_PROPERTY)
								_speed_result('Connect to Live TV before testing VPN servers.')
							self._wait_for_check(_RETRY_SECONDS, seen_request)
							continue
						handled_request = requested
						_set_failure('connecting')
						config = livetv_vpn_provision.ensure_config(private, account_path, library)
				except livetv_vpn_provision.ProvisionError as error:
					_set_provision_failure(livetv_vpn_provision, error)
					raise
				if self._stop.is_set(): break
				private_real = os.path.realpath(private)
				if os.path.commonpath((private_real, os.path.realpath(config))) != private_real: raise VPNUnavailable('VPN configuration unavailable')
				configured_signature = _signature(config)
				if not approved_relay or approved_relay[0] != configured_signature:
					approved_relay = livetv_vpn_provision.ensure_amsterdam_relay(private, configured_signature)
				signature, exit_hostname = approved_relay
				if speed_switch and signature != speed_switch[2]:
					_speed_result('VPN configuration changed. Test result was not applied.', automatic=bool(speed_switch[3]))
					speed_switch = None
				if signature == revoked_signature: raise VPNUnavailable('Live TV Mullvad registration was removed')
				activation_idle = speed_switch[3] if speed_switch else 0
				if activation_idle and not _automatic_speed_idle(activation_idle): raise VPNUnavailable('Automatic VPN test cancelled')
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
					if activation_idle and not _automatic_speed_idle(activation_idle): break
					try: verified = _verify_exit(port, password, exit_hostname)
					except Exception: verified = False
					if verified: break
					if not applying_speed and _requested_at() > handled_request: break
					if self._stop.wait(_CHECK_SECONDS): break
				if activation_idle and not _automatic_speed_idle(activation_idle): raise VPNUnavailable('Automatic VPN test cancelled')
				if not verified or self._stop.is_set():
					connection_failed = not self._stop.is_set()
					raise VPNUnavailable('IPTV VPN is unavailable')
				if _signature(config) != signature: raise VPNUnavailable('IPTV VPN configuration changed')
				relay = livetv_vpn_relay.Relay(port, password, descriptor_path())
				relay_started_at = monotonic()
				handled_catalog_access = {}
				relay.start()
				ku.clear_property(_SPEED_PAUSE_PROPERTY)
				_set_failure()
				self._set_state('IPTV VPN connected')
				if speed_switch:
					_speed_result('Live TV VPN switched to %s.' % speed_switch[1]['hostname'], automatic=bool(activation_idle))
					speed_switch = None
				connection_failures = failures = 0
				while not self._stop.is_set():
					if replaced:
						if not ku.get_visibility('Player.HasMedia') or self._stop.wait(_CHECK_SECONDS): break
						continue
					if signature != binding_signature and monotonic() >= binding_retry:
						binding_retry = monotonic() + _BINDING_RETRY_SECONDS
						try:
							if livetv_vpn_provision.record_config_binding(private, account_path, library, signature): binding_signature = signature
						except livetv_vpn_provision.ProvisionError: pass
					requested = _requested_at()
					pending_check = requested > max(handled_request, checked_request)
					if not replaced and not failures and pending_check and signature == binding_signature and monotonic() >= account_check_after and not ku.get_visibility('Player.HasMedia'):
						checked_request = requested
						account_check_after = monotonic() + _ACCOUNT_CHECK_INTERVAL
						try:
							replaced = livetv_vpn_provision.recover_revoked_config(private, account_path, library, signature)
							revoked_signature = None
							_set_failure('connecting' if replaced else '')
							if replaced:
								handled_request = requested
								continue
						except livetv_vpn_provision.ProvisionError as error:
							_set_provision_failure(livetv_vpn_provision, error)
							if isinstance(error, (livetv_vpn_provision.DeviceLimitError, livetv_vpn_provision.RegistrationPendingError)):
								handled_request = requested
								revoked_signature = signature
					speed_requested = _speed_requested_at()
					if signature == revoked_signature and speed_requested > seen_speed:
						seen_speed = speed_requested
						ku.clear_property(_SPEED_REQUEST_PROPERTY)
						_speed_result('Connect to Live TV before testing VPN servers.')
					speed_ready = not failures and not replaced and signature != revoked_signature
					manual_speed = speed_ready and speed_requested > seen_speed and not ku.get_visibility('Player.HasMedia')
					automatic_idle = 0
					repair = None
					if speed_ready and speed_requested <= seen_speed and not requested:
						repair = _catalog_recovery(relay_started_at, handled_catalog_access)
						degraded = repair or len(_recent_catalog_problems(seen_catalog_problems)) >= 2
						idle = _SPEED_DEGRADED_IDLE if degraded else _SPEED_AUTO_IDLE
						interval = _CATALOG_RECOVERY_INTERVAL if repair else _SPEED_DEGRADED_INTERVAL if degraded else _SPEED_AUTO_INTERVAL
						if _automatic_speed_idle(idle) and livetv_vpn_provision.record_speed_attempt(private, interval): automatic_idle = idle
					if manual_speed or automatic_idle:
						seen_catalog_problems = monotonic()
						if manual_speed:
							seen_speed = speed_requested
							ku.clear_property(_SPEED_REQUEST_PROPERTY)
							livetv_vpn_provision.record_speed_attempt(private)
						try: speed_switch = self._compare_speed(private, library, config, signature, port, password, automatic_idle=automatic_idle, repair=repair)
						except VPNUnavailable:
							_speed_result('VPN server test ended. Reconnecting Live TV.', automatic=bool(automatic_idle))
							reconnect = True
							break
						except Exception: _speed_result('Unable to complete the VPN server test.', automatic=bool(automatic_idle))
						else:
							if repair and not speed_switch and _automatic_speed_idle(automatic_idle): handled_catalog_access[repair] = seen_catalog_problems
						if speed_switch:
							started = False
							break
					retry_seconds = _CHECK_SECONDS if pending_check or (failures and requested > handled_request) else _HEALTH_SECONDS
					self._wait_for_check(retry_seconds, seen_request, seen_speed if not failures else None)
					if self._stop.is_set(): break
					if _signature(config) != signature: break
					requested = _requested_at()
					seen_request = max(seen_request, requested)
					try: healthy = relay.thread is not None and relay.thread.is_alive() and library.Status() in (1, 2) and _verify_exit(port, password, exit_hostname)
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
			if speed_switch and stopped:
				if not applying_speed and not self._stop.is_set(): continue
				old, _, tested_signature, activation_idle = speed_switch
				speed_switch = None
				try: restored = livetv_vpn_provision.select_relay(private, old, tested_signature)
				except livetv_vpn_provision.ProvisionError: restored = False
				_speed_result('Could not activate the tested VPN server. Restoring the previous server.' if restored else 'Could not activate the tested VPN server.', automatic=bool(activation_idle))
				if restored: continue
			if _speed_requested_at() > seen_speed:
				seen_speed = _speed_requested_at()
				ku.clear_property(_SPEED_REQUEST_PROPERTY)
				_speed_result('Connect to Live TV before testing VPN servers.')
			if (replaced or reconnect) and stopped:
				continue
			if connection_failed or (signature is not None and signature == revoked_signature):
				connection_failures += 1
				requested = _requested_at()
				seen_request = max(seen_request, requested)
				if stopped and not self._stop.is_set() and requested > handled_request:
					handled_request = requested
					_set_failure('connecting')
					try:
						if livetv_vpn_provision.recover_revoked_config(private, account_path, library, signature) or signature == revoked_signature:
							revoked_signature = None
							connection_failures = 0
							continue
						_set_failure()
					except livetv_vpn_provision.ProvisionError as error:
						_set_provision_failure(livetv_vpn_provision, error)
						if isinstance(error, (livetv_vpn_provision.DeviceLimitError, livetv_vpn_provision.RegistrationPendingError)): revoked_signature = signature
				if connection_failures >= 3 and stopped and not self._stop.is_set() and signature != revoked_signature:
					try:
						livetv_vpn_provision.rotate_relay(private)
						connection_failures = 0
					except Exception: self._set_state('IPTV VPN unavailable')
			self._wait_for_check(_RETRY_SECONDS, seen_request)
