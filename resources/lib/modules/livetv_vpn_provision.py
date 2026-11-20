"""Provision a private per-installation Mullvad WireGuard configuration.

The account file is read only when provisioning is needed. Device registration
uses Mullvad's published WireGuard configuration API; credentials and keys are
never written to Kodi logs or returned in error messages.
"""
import base64
import configparser
import ctypes
import fcntl
import hashlib
import io
import ipaddress
import json
import os
import re
import stat
import tempfile
import urllib.error
import urllib.request


_API = 'https://api.mullvad.net'
_DEVICE_LIMIT = 5
_RELAY = 'ca-tor-wg-207'
_RELAY_PORT = 10997
_CONFIG = 'mullvad-wireguard.conf'
_PENDING = 'mullvad-pending.json'
_BINDING = 'mullvad-device-binding.json'
_LOCK = 'mullvad-provision.lock'


class ProvisionError(RuntimeError):
	"""Provisioning could not complete without risking duplicate devices."""


class AccountError(ProvisionError):
	"""The Mullvad account file is missing, malformed, or rejected."""


class DeviceLimitError(ProvisionError):
	"""Mullvad reports that the account has no free device slots."""


class RegistrationPendingError(ProvisionError):
	"""The registration result remains unknown; the same key will be retried."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
	def redirect_request(self, req, fp, code, msg, headers, newurl):
		return None


def _request(opener, path, method='GET', body=None, token=None, timeout=15):
	headers = {'Accept': 'application/json'}
	if token:
		headers['Authorization'] = 'Bearer ' + token
	data = None
	if body is not None:
		data = json.dumps(body).encode('utf-8')
		headers['Content-Type'] = 'application/json'
	request = urllib.request.Request(_API + path, data=data, headers=headers, method=method)
	with opener.open(request, timeout=timeout) as response:
		payload = response.read(4 * 1024 * 1024 + 1)
		if len(payload) > 4 * 1024 * 1024:
			raise ProvisionError('Mullvad returned an unexpectedly large response')
		return json.loads(payload.decode('utf-8'))


def _account(path):
	try:
		with open(path, 'r', encoding='utf-8') as handle:
			value = handle.read(128).strip()
	except (OSError, UnicodeError):
		raise AccountError('Mullvad account file is unavailable') from None
	if not re.fullmatch(r'[0-9]{16}', value):
		raise AccountError('Mullvad account file must contain a 16-digit account number')
	return value


def _api_error_code(error):
	"""Read only the structured error code; do not expose the response body."""
	try:
		body = json.loads(error.read(4096).decode('utf-8'))
		return body.get('code') if isinstance(body, dict) else None
	except (OSError, ValueError, UnicodeError, AttributeError):
		return None


def _key(value):
	if not isinstance(value, str):
		return False
	try:
		return len(base64.b64decode(value, validate=True)) == 32
	except (ValueError, TypeError):
		return False


def _fingerprint(account, salt):
	return hashlib.sha256((salt + account).encode('ascii')).hexdigest()


def _key_digest(key):
	return hashlib.sha256(base64.b64decode(key, validate=True)).hexdigest()


def _private_write(path, content):
	"""Replace a private file atomically and persist the directory entry."""
	directory = os.path.dirname(path)
	fd, temp_path = tempfile.mkstemp(prefix='.mullvad-', dir=directory)
	try:
		os.fchmod(fd, 0o600)
		with os.fdopen(fd, 'w', encoding='utf-8') as handle:
			fd = None
			handle.write(content)
			handle.flush()
			os.fsync(handle.fileno())
			info = os.fstat(handle.fileno())
		os.replace(temp_path, path)
		directory_fd = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
		try:
			os.fsync(directory_fd)
		finally:
			os.close(directory_fd)
	finally:
		if fd is not None:
			os.close(fd)
		try:
			os.unlink(temp_path)
		except FileNotFoundError:
			pass
	return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size


def _save_pending(path, state):
	_private_write(path, json.dumps(state, sort_keys=True))


def _load_pending(path, account):
	try:
		with open(path, 'r', encoding='utf-8') as handle:
			state = json.load(handle)
		if not isinstance(state, dict) or state.get('version') != 1 or state.get('stage') not in ('prepared', 'posting', 'registered'):
			raise ValueError
		if not isinstance(state.get('salt'), str) or state.get('account_fingerprint') != _fingerprint(account, state['salt']):
			raise ValueError
		if not _key(state.get('private_key')) or not _key(state.get('public_key')):
			raise ValueError
		return state
	except (OSError, ValueError, TypeError, KeyError):
		raise ProvisionError('Pending Mullvad registration needs manual recovery') from None


def _read_config(path):
	"""Read a structurally valid existing configuration."""
	try:
		parser = configparser.ConfigParser(interpolation=None, strict=True)
		with open(path, 'r', encoding='utf-8') as handle:
			parser.read_file(handle)
		private_key = parser['Interface']['PrivateKey'].strip()
		address = parser['Interface']['Address'].split(',')[0].strip()
		dns = parser['Interface']['DNS'].split(',')[0].strip()
		public_key = parser['Peer']['PublicKey'].strip()
		allowed = [v.strip() for v in parser['Peer']['AllowedIPs'].split(',')]
		host, port = parser['Peer']['Endpoint'].rsplit(':', 1)
		ipaddress.ip_interface(address)
		ipaddress.ip_address(dns)
		ipaddress.ip_address(host)
		if not _key(private_key) or not _key(public_key) or '0.0.0.0/0' not in allowed or not 1 <= int(port) <= 65535:
			raise ValueError
		return parser
	except (OSError, UnicodeError, ValueError, KeyError, configparser.Error):
		return None


def _config_key(path):
	config = _read_config(path)
	return config['Interface']['PrivateKey'].strip() if config is not None else None


def _generate_keys(native_library):
	private_buffer = ctypes.create_string_buffer(45)
	public_buffer = ctypes.create_string_buffer(45)
	try:
		function = native_library.GenerateKeyPair
		function.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
		function.restype = ctypes.c_int
		status = function(ctypes.addressof(private_buffer), len(private_buffer), ctypes.addressof(public_buffer), len(public_buffer))
		if status != 0:
			raise ProvisionError('Unable to generate a Mullvad device key')
		private_key = private_buffer.value.decode('ascii')
		public_key = public_buffer.value.decode('ascii')
		if not _key(private_key) or not _key(public_key):
			raise ProvisionError('Generated Mullvad device key is invalid')
		return private_key, public_key
	except (AttributeError, UnicodeError, TypeError, ValueError):
		raise ProvisionError('Unable to generate a Mullvad device key') from None
	finally:
		ctypes.memset(ctypes.addressof(private_buffer), 0, len(private_buffer))


def _public_key(native_library, private_key):
	private_buffer = ctypes.create_string_buffer(private_key.encode('ascii'))
	public_buffer = ctypes.create_string_buffer(45)
	try:
		function = native_library.PublicKeyFromPrivate
		function.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
		function.restype = ctypes.c_int
		status = function(ctypes.addressof(private_buffer), ctypes.sizeof(private_buffer), ctypes.addressof(public_buffer), ctypes.sizeof(public_buffer))
		key = public_buffer.value.decode('ascii')
		if status != 0 or not _key(key):
			raise ValueError
		return key
	except (AttributeError, UnicodeError, TypeError, ValueError):
		raise ProvisionError('Unable to identify the current Mullvad device') from None
	finally:
		ctypes.memset(ctypes.addressof(private_buffer), 0, len(private_buffer))


def _relays(opener):
	relays = _request(opener, '/www/relays/all')
	if not isinstance(relays, list):
		raise ProvisionError('Mullvad relay list is unavailable')
	candidates = [item for item in relays if isinstance(item, dict) and item.get('type') == 'wireguard' and item.get('active') is True and item.get('country_code') == 'ca']
	candidates.sort(key=lambda item: (item.get('hostname') != _RELAY, str(item.get('hostname') or '')))
	result = []
	for item in candidates:
		try:
			address = str(ipaddress.IPv4Address(item['ipv4_addr_in']))
			hostname = item.get('hostname')
			if _key(item.get('pubkey')) and isinstance(hostname, str) and re.fullmatch(r'ca-[a-z]{3}-wg-[0-9]+', hostname):
				result.append({'hostname': hostname, 'host': address, 'public_key': item['pubkey'], 'port': _RELAY_PORT})
		except (ValueError, KeyError, TypeError):
			continue
	if not result:
		raise ProvisionError('No Canadian Mullvad relay is available')
	return result


def _relay(opener):
	return _relays(opener)[0]


def relay_candidates():
	"""Return the active Canadian WireGuard peers without accessing an account."""
	try:
		opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
		return _relays(opener)
	except (OSError, ValueError, TypeError):
		raise ProvisionError('Mullvad relay list is unavailable') from None


def _registered_device(opener, token, public_key):
	devices = _request(opener, '/accounts/v1/devices', token=token)
	if not isinstance(devices, list):
		raise ProvisionError('Mullvad device list is unavailable')
	matches = [item for item in devices if isinstance(item, dict) and item.get('pubkey') == public_key]
	if len(matches) > 1:
		raise ProvisionError('Mullvad device registration needs manual recovery')
	return matches[0] if matches else None


def _device_address(device):
	try:
		if not device.get('id'):
			raise ValueError
		address = ipaddress.ip_interface(device['ipv4_address'])
		if address.version != 4:
			raise ValueError
		return str(address)
	except (ValueError, KeyError, TypeError, AttributeError):
		raise ProvisionError('Mullvad device registration needs manual recovery') from None


def _remove_pending(path):
	try:
		os.unlink(path)
	except FileNotFoundError:
		pass


def _authenticated(account):
	opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
	try:
		response = _request(opener, '/auth/v1/webtoken', method='POST', body={'account_number': account})
	except urllib.error.HTTPError as error:
		if _api_error_code(error) == 'INVALID_ACCOUNT':
			raise AccountError('Mullvad account was rejected') from None
		raise ProvisionError('Mullvad account could not be checked; retry later') from None
	token = response.get('access_token') if isinstance(response, dict) else None
	if not isinstance(token, str) or not token.strip():
		raise ProvisionError('Mullvad account could not be checked; retry later')
	return opener, token


def _lock_file(directory):
	path = os.path.join(directory, _LOCK)
	fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
	return os.fdopen(fd, 'r+')


def _read_json_file(path):
	try:
		with open(path, 'r', encoding='utf-8') as handle:
			value = json.load(handle)
		return value if isinstance(value, dict) else {}
	except (OSError, ValueError):
		return {}


def _config_matches(path, expected_signature):
	try:
		info = os.lstat(path)
		return (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o077
			and expected_signature == (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size))
	except OSError:
		return False


def _save_binding(path, account, public_key):
	salt = os.urandom(16).hex()
	binding = {'version': 1, 'salt': salt, 'account_fingerprint': _fingerprint(account, salt), 'device_key': _key_digest(public_key)}
	_private_write(path, json.dumps(binding, sort_keys=True))


def _binding_matches(path, account, public_key):
	binding = _read_json_file(path)
	salt = binding.get('salt')
	return (binding.get('version') == 1 and isinstance(salt, str) and re.fullmatch('[0-9a-f]{32}', salt) is not None
		and binding.get('account_fingerprint') == _fingerprint(account, salt) and binding.get('device_key') == _key_digest(public_key))


def _confirmed_devices(opener, token):
	devices = _request(opener, '/accounts/v1/devices', token=token)
	if not isinstance(devices, list) or any(not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'] or not _key(item.get('pubkey')) for item in devices):
		raise ProvisionError('Mullvad device list is unavailable')
	keys = {_key_digest(item['pubkey']) for item in devices}
	if len(keys) != len(devices) or len({item['id'] for item in devices}) != len(devices):
		raise ProvisionError('Mullvad device list is unavailable')
	return keys


def record_config_binding(private_directory, account_path, native_library, expected_signature):
	"""Bind a working legacy configuration only after Mullvad identifies its key."""
	try:
		config_path = os.path.join(private_directory, _CONFIG)
		binding_path = os.path.join(private_directory, _BINDING)
		with _lock_file(private_directory) as lock:
			fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
			if not _config_matches(config_path, expected_signature):
				return False
			private_key = _config_key(config_path)
			if not private_key:
				return False
			account = _account(account_path)
			public_key = _public_key(native_library, private_key)
			if _binding_matches(binding_path, account, public_key):
				return True
			opener, token = _authenticated(account)
			if _key_digest(public_key) not in _confirmed_devices(opener, token) or not _config_matches(config_path, expected_signature):
				return False
			_save_binding(binding_path, account, public_key)
			return True
	except ProvisionError:
		raise
	except (OSError, ValueError, TypeError, KeyError, AttributeError):
		raise ProvisionError('Mullvad device could not be checked; retry later') from None


def _prepare_registration(account, native_library, relay, replaces=None):
	private_key, public_key = _generate_keys(native_library)
	salt = os.urandom(16).hex()
	state = {'version': 1, 'stage': 'prepared', 'private_key': private_key, 'public_key': public_key, 'salt': salt, 'account_fingerprint': _fingerprint(account, salt), 'relay': relay}
	if replaces:
		state['replaces'] = replaces
	return state


def _finish_registration(config_path, pending_path, binding_path, account, opener, token, state, config=None, expected_signature=None):
	if state['stage'] == 'prepared':
		# Persist before POST. Future attempts first query this same key.
		state['stage'] = 'posting'
		_save_pending(pending_path, state)
	if state['stage'] == 'posting':
		device = _registered_device(opener, token, state['public_key'])
		if not device:
			# Mullvad's own wg-tools reuses the saved key, looking it up
			# before creating it. Keep our journal on every error: a prior
			# request may have committed despite a lost response.
			try:
				device = _request(opener, '/accounts/v1/devices', method='POST', body={'pubkey': state['public_key'], 'hijack_dns': False}, token=token)
			except urllib.error.HTTPError as error:
				code = _api_error_code(error)
				if code in ('PUBKEY_IN_USE', 'MAX_DEVICES_REACHED'):
					device = _registered_device(opener, token, state['public_key'])
					if not device and code == 'MAX_DEVICES_REACHED':
						raise DeviceLimitError('Mullvad account has no free device slots') from None
					if not device:
						raise RegistrationPendingError('Mullvad device registration is pending; retry later') from None
				elif code == 'INVALID_ACCOUNT':
					raise AccountError('Mullvad account was rejected') from None
				else:
					raise RegistrationPendingError('Mullvad device registration is pending; retry later') from None
			except (OSError, urllib.error.URLError):
				raise RegistrationPendingError('Mullvad device registration is pending; retry later') from None
		if not isinstance(device, dict) or device.get('pubkey') != state['public_key']:
			raise RegistrationPendingError('Mullvad device registration is pending; retry later')
		state['address'] = _device_address(device)
		state['stage'] = 'registered'
		_save_pending(pending_path, state)
	relay = state['relay']
	if not isinstance(relay, dict) or not _key(relay.get('public_key')) or str(ipaddress.IPv4Address(relay['host'])) != relay['host'] or not 1 <= int(relay['port']) <= 65535:
		raise ProvisionError('Pending Mullvad configuration needs manual recovery')
	address = str(ipaddress.IPv4Interface(state['address']))
	if config is None:
		text = ('[Interface]\nPrivateKey = {private}\nAddress = {address}\nDNS = 10.64.0.1\n\n[Peer]\nPublicKey = {public}\nAllowedIPs = 0.0.0.0/0\nEndpoint = {host}:{port}\n').format(
			private=state['private_key'], address=address, public=relay['public_key'], host=relay['host'], port=relay['port'])
	else:
		config['Interface']['PrivateKey'] = state['private_key']
		config['Interface']['Address'] = address
		buffer = io.StringIO()
		config.write(buffer)
		text = buffer.getvalue()
		if not _config_matches(config_path, expected_signature):
			raise ProvisionError('Mullvad configuration changed during recovery')
	_private_write(config_path, text)
	_save_binding(binding_path, account, state['public_key'])
	_remove_pending(pending_path)
	return config_path


def ensure_config(private_directory, account_path, native_library, *, allow_registration=True):
	"""Reuse an existing configuration or provision a new installation when allowed."""
	try:
		os.makedirs(private_directory, mode=0o700, exist_ok=True)
		config_path = os.path.join(private_directory, _CONFIG)
		pending_path = os.path.join(private_directory, _PENDING)
		binding_path = os.path.join(private_directory, _BINDING)
		with _lock_file(private_directory) as lock:
			fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
			config = None
			if os.path.exists(config_path):
				config = _read_config(config_path)
				if config is None:
					raise ProvisionError('Existing Mullvad configuration needs manual recovery')
				private_key = config['Interface']['PrivateKey'].strip()
				pending = _read_json_file(pending_path)
				if pending.get('private_key') == private_key and pending.get('stage') == 'registered':
					try:
						account = _account(account_path)
						state = _load_pending(pending_path, account)
						_save_binding(binding_path, account, state['public_key'])
						_remove_pending(pending_path)
					except (OSError, ProvisionError):
						pass
				return config_path
			if not allow_registration:
				return None
			account = _account(account_path)
			if os.path.exists(pending_path):
				state = _load_pending(pending_path, account)
				if state.get('replaces'):
					raise ProvisionError('Pending Mullvad replacement needs its original configuration')
			else:
				state = None
			opener, token = _authenticated(account)
			if state is None:
				state = _prepare_registration(account, native_library, _relay(opener))
				_save_pending(pending_path, state)
			return _finish_registration(config_path, pending_path, binding_path, account, opener, token, state)
	except ProvisionError:
		raise
	except (OSError, ValueError, TypeError, KeyError, AttributeError):
		raise ProvisionError('Mullvad setup could not be completed; retry later') from None


def recover_revoked_config(private_directory, account_path, native_library, expected_signature):
	"""Replace a registration only after its known Mullvad account omits the current key."""
	try:
		config_path = os.path.join(private_directory, _CONFIG)
		pending_path = os.path.join(private_directory, _PENDING)
		binding_path = os.path.join(private_directory, _BINDING)
		with _lock_file(private_directory) as lock:
			fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
			if not _config_matches(config_path, expected_signature):
				return False
			config = _read_config(config_path)
			if config is None:
				return False
			private_key = config['Interface']['PrivateKey'].strip()
			account = _account(account_path)
			public_key = _public_key(native_library, private_key)
			if not _binding_matches(binding_path, account, public_key):
				return False
			opener, token = _authenticated(account)
			devices = _confirmed_devices(opener, token)
			if _key_digest(public_key) in devices or not _config_matches(config_path, expected_signature):
				return False
			replaces = _key_digest(private_key)
			if os.path.exists(pending_path):
				state = _load_pending(pending_path, account)
				if state.get('replaces') != replaces:
					raise ProvisionError('Pending Mullvad registration needs manual recovery')
				if _key_digest(state['public_key']) not in devices:
					if len(devices) >= _DEVICE_LIMIT:
						raise DeviceLimitError('Mullvad account has no free device slots')
					if state['stage'] == 'registered':
						state['stage'] = 'posting'
						_save_pending(pending_path, state)
			else:
				if len(devices) >= _DEVICE_LIMIT:
					raise DeviceLimitError('Mullvad account has no free device slots')
				peer = config['Peer']
				host, port = peer['Endpoint'].rsplit(':', 1)
				relay = {'host': host, 'port': int(port), 'public_key': peer['PublicKey'].strip()}
				state = _prepare_registration(account, native_library, relay, replaces=replaces)
				_save_pending(pending_path, state)
			_finish_registration(config_path, pending_path, binding_path, account, opener, token, state, config, expected_signature)
			return True
	except ProvisionError:
		raise
	except (OSError, ValueError, TypeError, KeyError, AttributeError):
		raise ProvisionError('Mullvad device could not be checked; retry later') from None


def current_relay(private_directory, expected_signature):
	"""Return the configured peer if the expected private configuration still matches."""
	try:
		config_path = os.path.join(private_directory, _CONFIG)
		with _lock_file(private_directory) as lock:
			fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
			if not _config_matches(config_path, expected_signature):
				return None
			config = _read_config(config_path)
			if config is None or not _config_matches(config_path, expected_signature):
				return None
			peer = config['Peer']
			host, port = peer['Endpoint'].rsplit(':', 1)
			return {'host': host, 'port': int(port), 'public_key': peer['PublicKey'].strip()}
	except OSError:
		return None


def _write_relay(private_directory, relay, expected_signature, target_path=None):
	try:
		host, port, key = relay['host'], relay['port'], relay['public_key']
		if str(ipaddress.IPv4Address(host)) != host or type(port) is not int or not 1 <= port <= 65535 or not _key(key):
			raise ValueError
		config_path = os.path.join(private_directory, _CONFIG)
		with _lock_file(private_directory) as lock:
			fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
			if not _config_matches(config_path, expected_signature):
				return False
			config = _read_config(config_path)
			if config is None:
				return False
			private_key = config['Interface']['PrivateKey'].strip()
			if _read_json_file(os.path.join(private_directory, _PENDING)).get('replaces') == _key_digest(private_key):
				return False
			peer = config['Peer']
			if host == peer['Endpoint'].rsplit(':', 1)[0] or _key_digest(key) == _key_digest(peer['PublicKey'].strip()):
				return False
			if target_path is not None:
				target_path = os.path.abspath(target_path)
				info = os.lstat(os.path.dirname(target_path))
				if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077 or os.path.lexists(target_path):
					raise ProvisionError('Private Mullvad test configuration path is unavailable')
			peer['PublicKey'] = key
			peer['Endpoint'] = '%s:%d' % (host, port)
			buffer = io.StringIO()
			config.write(buffer)
			if not _config_matches(config_path, expected_signature):
				return False
			signature = _private_write(config_path if target_path is None else target_path, buffer.getvalue())
			return signature if target_path is None else True
	except ProvisionError:
		raise
	except (OSError, ValueError, TypeError, KeyError, configparser.Error):
		raise ProvisionError('Mullvad relay could not be changed; retry later') from None


def stage_relay(private_directory, relay, expected_signature, target_path):
	"""Stage a different peer for a separate tunnel using the current device identity."""
	try:
		target_path = os.fspath(target_path)
	except TypeError:
		raise ProvisionError('Private Mullvad test configuration path is unavailable') from None
	if not isinstance(target_path, str) or not target_path:
		raise ProvisionError('Private Mullvad test configuration path is unavailable')
	return _write_relay(private_directory, relay, expected_signature, target_path)


def select_relay(private_directory, relay, expected_signature):
	"""Replace only the peer and return the new config signature, or False."""
	return _write_relay(private_directory, relay, expected_signature)


def rotate_relay(private_directory):
	"""Select another active Canadian relay without registering a new device."""
	try:
		config_path = os.path.join(private_directory, _CONFIG)
		lock_path = os.path.join(private_directory, _LOCK)
		lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
		with os.fdopen(lock_fd, 'r+') as lock:
			fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
			private_key = _config_key(config_path)
			if not private_key:
				raise ProvisionError('Existing Mullvad configuration needs manual recovery')
			if _read_json_file(os.path.join(private_directory, _PENDING)).get('replaces') == _key_digest(private_key):
				return config_path
			parser = configparser.ConfigParser(interpolation=None, strict=True)
			with open(config_path, 'r', encoding='utf-8') as handle:
				parser.read_file(handle)
			old_host, _ = parser['Peer']['Endpoint'].rsplit(':', 1)
			old_key = parser['Peer']['PublicKey'].strip()
			opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
			candidates = _relays(opener)
			current = next((index for index, relay in enumerate(candidates) if relay['host'] == old_host and relay['public_key'] == old_key), -1)
			relay = next((candidates[(current + offset) % len(candidates)] for offset in range(1, len(candidates) + 1) if candidates[(current + offset) % len(candidates)]['host'] != old_host or candidates[(current + offset) % len(candidates)]['public_key'] != old_key), None)
			if not relay:
				raise ProvisionError('No alternate Canadian Mullvad relay is available')
			parser['Peer']['PublicKey'] = relay['public_key']
			parser['Peer']['Endpoint'] = '%s:%s' % (relay['host'], relay['port'])
			config = io.StringIO()
			parser.write(config)
			_private_write(config_path, config.getvalue())
			return config_path
	except ProvisionError:
		raise
	except (OSError, ValueError, TypeError, KeyError, AttributeError, configparser.Error, urllib.error.URLError):
		raise ProvisionError('Mullvad relay could not be changed; retry later') from None
