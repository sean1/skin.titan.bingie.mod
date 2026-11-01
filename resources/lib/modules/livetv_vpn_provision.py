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
import tempfile
import urllib.error
import urllib.request


_API = 'https://api.mullvad.net'
_RELAY = 'ca-tor-wg-207'
_RELAY_PORT = 10997
_CONFIG = 'mullvad-wireguard.conf'
_PENDING = 'mullvad-pending.json'
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


def _config_key(path):
	"""Return the key only for a structurally valid existing configuration."""
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
		return private_key
	except (OSError, UnicodeError, ValueError, KeyError, configparser.Error):
		return None


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


def _relays(opener):
	relays = _request(opener, '/www/relays/all')
	if not isinstance(relays, list):
		raise ProvisionError('Mullvad relay list is unavailable')
	candidates = [item for item in relays if isinstance(item, dict) and item.get('type') == 'wireguard' and item.get('active') and item.get('country_code') == 'ca']
	candidates.sort(key=lambda item: (item.get('hostname') != _RELAY, str(item.get('hostname') or '')))
	result = []
	for item in candidates:
		try:
			address = str(ipaddress.IPv4Address(item['ipv4_addr_in']))
			if _key(item.get('pubkey')):
				result.append({'host': address, 'public_key': item['pubkey'], 'port': _RELAY_PORT})
		except (ValueError, KeyError, TypeError):
			continue
	if not result:
		raise ProvisionError('No Canadian Mullvad relay is available')
	return result


def _relay(opener):
	return _relays(opener)[0]


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


def ensure_config(private_directory, account_path, native_library):
	"""Return a private config path, registering at most one key per install.

	A posting-stage journal preserves the key across ambiguous responses. Later
	calls look up that same key and retry it, never generating a replacement key.
	"""
	try:
		os.makedirs(private_directory, mode=0o700, exist_ok=True)
		config_path = os.path.join(private_directory, _CONFIG)
		pending_path = os.path.join(private_directory, _PENDING)
		lock_path = os.path.join(private_directory, _LOCK)
		lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
		with os.fdopen(lock_fd, 'r+') as lock:
			fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
			if os.path.exists(config_path):
				existing_key = _config_key(config_path)
				if not existing_key:
					raise ProvisionError('Existing Mullvad configuration needs manual recovery')
				# If a crash occurred after the config was saved, remove only a
				# pending record for exactly the same private key.
				if os.path.exists(pending_path):
					try:
						with open(pending_path, 'r', encoding='utf-8') as handle:
							pending = json.load(handle)
						if pending.get('private_key') == existing_key:
							_remove_pending(pending_path)
					except (OSError, ValueError, AttributeError):
						pass
				return config_path
			account = _account(account_path)
			opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
			try:
				token = _request(opener, '/auth/v1/webtoken', method='POST', body={'account_number': account})['access_token']
			except urllib.error.HTTPError as error:
				if _api_error_code(error) == 'INVALID_ACCOUNT':
					raise AccountError('Mullvad account was rejected') from None
				raise ProvisionError('Mullvad account could not be checked; retry later') from None
			if os.path.exists(pending_path):
				state = _load_pending(pending_path, account)
			else:
				relay = _relay(opener)
				private_key, public_key = _generate_keys(native_library)
				salt = os.urandom(16).hex()
				state = {'version': 1, 'stage': 'prepared', 'private_key': private_key, 'public_key': public_key, 'salt': salt, 'account_fingerprint': _fingerprint(account, salt), 'relay': relay}
				_save_pending(pending_path, state)
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
			config = ('[Interface]\nPrivateKey = {private}\nAddress = {address}\nDNS = 10.64.0.1\n\n[Peer]\nPublicKey = {public}\nAllowedIPs = 0.0.0.0/0\nEndpoint = {host}:{port}\n').format(private=state['private_key'], address=address, public=relay['public_key'], host=relay['host'], port=relay['port'])
			_private_write(config_path, config)
			_remove_pending(pending_path)
			return config_path
	except ProvisionError:
		raise
	except (OSError, ValueError, TypeError, KeyError, AttributeError, urllib.error.URLError):
		raise ProvisionError('Mullvad setup could not be completed; retry later') from None


def rotate_relay(private_directory):
	"""Select another active Canadian relay without registering a new device."""
	try:
		config_path = os.path.join(private_directory, _CONFIG)
		lock_path = os.path.join(private_directory, _LOCK)
		lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
		with os.fdopen(lock_fd, 'r+') as lock:
			fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
			if not _config_key(config_path):
				raise ProvisionError('Existing Mullvad configuration needs manual recovery')
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
