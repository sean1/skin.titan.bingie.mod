import base64
import configparser
import ctypes
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]
PROVISION = ROOT / 'resources' / 'lib' / 'modules' / 'livetv_vpn_provision.py'
OLD_PRIVATE, OLD_PUBLIC, NEW_PRIVATE, NEW_PUBLIC, PEER_PUBLIC, OTHER_PUBLIC = [base64.b64encode(bytes([value]) * 32).decode('ascii') for value in range(1, 7)]
OLD_ADDRESS = '10.64.7.14/32'
NEW_ADDRESS = '10.64.7.15/32'
PEER_ENDPOINT = '192.0.2.15:10997'
OLD_DEVICE = {'id': 'old-device', 'pubkey': OLD_PUBLIC, 'ipv4_address': OLD_ADDRESS}
NEW_DEVICE = {'id': 'new-device', 'pubkey': NEW_PUBLIC, 'ipv4_address': NEW_ADDRESS}
OTHER_DEVICES = [
	{'id': f'other-device-{value}', 'pubkey': base64.b64encode(bytes([value]) * 32).decode('ascii'), 'ipv4_address': f'10.64.7.{value}/32'}
	for value in range(10, 15)
]
TOKEN_PATH = '/auth/v1/webtoken'
DEVICE_PATH = '/accounts/v1/devices'


def api_error(code):
	return urllib.error.HTTPError('https://example.invalid', 400, 'Synthetic Mullvad error', None, io.BytesIO(json.dumps({'code': code}).encode('utf-8')))


class MullvadProvisionTests(unittest.TestCase):
	def setUp(self):
		self.provision = load_module('test_mullvad_provision_runtime', PROVISION)
		scratch = ROOT / 'tmp'
		scratch.mkdir(exist_ok=True)
		directory = tempfile.TemporaryDirectory(prefix='mullvad-provision-test-', dir=scratch)
		self.addCleanup(directory.cleanup)
		self.profile = Path(directory.name)
		self.account_path = self.profile / 'account.txt'
		self.account_path.write_text('9990000000000000', encoding='ascii')
		self.config = self.profile / 'mullvad-wireguard.conf'
		self.pending = self.profile / 'mullvad-pending.json'
		self.original = (
			f'[Interface]\nPrivateKey = {OLD_PRIVATE}\nAddress = {OLD_ADDRESS}\nDNS = 10.64.0.1\n\n'
			f'[Peer]\nPublicKey = {PEER_PUBLIC}\nAllowedIPs = 0.0.0.0/0\nEndpoint = {PEER_ENDPOINT}\n'
		)
		self.config.write_text(self.original, encoding='ascii')
		self.config.chmod(0o600)
		self.devices = [OLD_DEVICE]
		self.auth_error = None
		self.list_error = None
		self.create = Mock(return_value=NEW_DEVICE)
		self.requests = []
		self.native = Mock()
		self.native.PublicKeyFromPrivate.side_effect = self.derive_public_key
		self.native.GenerateKeyPair.side_effect = self.generate_keys
		network = patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('Network is disabled in provisioning tests'))
		api = patch.object(self.provision, '_request', side_effect=self.request)
		self.addCleanup(network.stop)
		self.addCleanup(api.stop)
		network.start()
		api.start()

	def derive_public_key(self, private_buffer, private_length, output, output_length):
		private_key = ctypes.string_at(private_buffer, private_length).rstrip(b'\0').decode('ascii')
		self.assertGreaterEqual(output_length, 45)
		public_key = {OLD_PRIVATE: OLD_PUBLIC, NEW_PRIVATE: NEW_PUBLIC}[private_key]
		ctypes.memmove(output, public_key.encode('ascii') + b'\0', 45)
		return 0

	def generate_keys(self, private_buffer, private_length, public_buffer, public_length):
		self.assertGreaterEqual(private_length, 45)
		self.assertGreaterEqual(public_length, 45)
		ctypes.memmove(private_buffer, NEW_PRIVATE.encode('ascii') + b'\0', 45)
		ctypes.memmove(public_buffer, NEW_PUBLIC.encode('ascii') + b'\0', 45)
		return 0

	def request(self, opener, path, method='GET', body=None, token=None, **kwargs):
		self.requests.append((method, path))
		if (method, path) == ('POST', TOKEN_PATH):
			if self.auth_error:
				raise self.auth_error
			return {'access_token': 'synthetic-test-token'}
		self.assertEqual(token, 'synthetic-test-token')
		if (method, path) == ('GET', DEVICE_PATH):
			if self.list_error:
				raise self.list_error
			return self.devices
		if (method, path) == ('POST', DEVICE_PATH):
			return self.create(body)
		self.fail('Unexpected Mullvad request')

	def signature(self):
		info = self.config.stat()
		return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size

	def bind(self):
		return self.provision.record_config_binding(str(self.profile), str(self.account_path), self.native, self.signature())

	def recover(self, signature=None):
		return self.provision.recover_revoked_config(str(self.profile), str(self.account_path), self.native, signature or self.signature())

	def assert_original_config(self):
		self.assertEqual(self.config.read_text(encoding='ascii'), self.original)

	def assert_replacement_config(self, address=NEW_ADDRESS):
		config = configparser.ConfigParser(interpolation=None)
		config.read(self.config, encoding='ascii')
		self.assertEqual(config['Interface']['PrivateKey'], NEW_PRIVATE)
		self.assertEqual(config['Interface']['Address'], address)
		self.assertEqual(config['Interface']['DNS'], '10.64.0.1')
		self.assertEqual(dict(config['Peer']), {'publickey': PEER_PUBLIC, 'allowedips': '0.0.0.0/0', 'endpoint': PEER_ENDPOINT})
		self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)

	def test_healthy_config_is_loaded_without_contacting_mullvad(self):
		result = self.provision.ensure_config(str(self.profile), str(self.account_path), self.native)
		self.assertEqual(result, str(self.config))
		self.assertEqual(self.requests, [])
		self.native.GenerateKeyPair.assert_not_called()
		self.assert_original_config()

	def test_background_startup_never_registers_without_config(self):
		self.config.unlink()
		result = self.provision.ensure_config(str(self.profile), str(self.account_path), self.native, allow_registration=False)
		self.assertIsNone(result)
		self.assertEqual(self.requests, [])
		self.native.GenerateKeyPair.assert_not_called()
		self.assertFalse(self.config.exists())
		self.assertFalse(self.pending.exists())

	def test_legacy_binding_requires_the_exact_device_key(self):
		self.devices = [dict(OLD_DEVICE, pubkey=OTHER_PUBLIC)]
		self.assertFalse(self.bind())
		self.devices = [OLD_DEVICE]
		self.assertTrue(self.bind())
		self.assertFalse(self.recover())
		self.create.assert_not_called()
		self.native.GenerateKeyPair.assert_not_called()
		self.assert_original_config()

	def test_unbound_config_or_switched_account_cannot_recover(self):
		self.devices = []
		self.assertFalse(self.recover())
		self.devices = [OLD_DEVICE]
		self.assertTrue(self.bind())
		self.account_path.write_text('9991000000000000', encoding='ascii')
		self.devices = []
		self.assertFalse(self.recover())
		self.create.assert_not_called()
		self.native.GenerateKeyPair.assert_not_called()
		self.assert_original_config()

	def test_stale_config_signature_cannot_overwrite_a_new_config(self):
		self.assertTrue(self.bind())
		signature = self.signature()
		new_content = self.original.replace('192.0.2.15', '192.0.2.16')
		self.config.write_text(new_content, encoding='ascii')
		self.devices = []
		self.assertFalse(self.recover(signature))
		self.create.assert_not_called()
		self.assertEqual(self.config.read_text(encoding='ascii'), new_content)

	def test_failed_device_lookup_never_proves_revocation(self):
		self.assertTrue(self.bind())
		for devices, list_error in (({'unexpected': []}, None), ([OLD_DEVICE, OLD_DEVICE], None), ([], api_error('INVALID_ACCOUNT')), ([], OSError('Unreachable'))):
			with self.subTest(devices_type=type(devices).__name__, error_type=type(list_error).__name__):
				self.devices = devices
				self.list_error = list_error
				with self.assertRaises(self.provision.ProvisionError):
					self.recover()
				self.create.assert_not_called()
				self.native.GenerateKeyPair.assert_not_called()
				self.assert_original_config()
				self.assertFalse(self.pending.exists())

	def test_rejected_account_never_causes_replacement(self):
		self.assertTrue(self.bind())
		self.requests.clear()
		self.auth_error = api_error('INVALID_ACCOUNT')
		with self.assertRaises(self.provision.AccountError):
			self.recover()
		self.assertEqual(self.requests, [('POST', TOKEN_PATH)])
		self.create.assert_not_called()
		self.native.GenerateKeyPair.assert_not_called()
		self.assert_original_config()

	def test_revoked_registration_is_replaced_once_with_same_peer(self):
		self.assertTrue(self.bind())
		self.devices = []
		self.assertTrue(self.recover())
		self.assert_replacement_config()
		self.assertFalse(self.pending.exists())
		self.devices = [NEW_DEVICE]
		self.assertFalse(self.recover())
		self.create.assert_called_once_with({'pubkey': NEW_PUBLIC, 'hijack_dns': False})
		self.native.GenerateKeyPair.assert_called_once()

	def test_full_account_preserves_config_and_retries_the_same_key(self):
		self.assertTrue(self.bind())
		self.devices = []
		self.create.side_effect = api_error('MAX_DEVICES_REACHED')
		with self.assertRaises(self.provision.DeviceLimitError):
			self.recover()
		self.assert_original_config()
		self.assertTrue(self.pending.exists())
		self.create.side_effect = None
		self.assertTrue(self.recover())
		self.assertEqual(len(self.create.call_args_list), 2)
		self.assertEqual(self.create.call_args_list[0], self.create.call_args_list[1])
		self.native.GenerateKeyPair.assert_called_once()
		self.assertFalse(self.pending.exists())
		self.assert_replacement_config()

	def test_full_device_list_blocks_replacement_before_creation(self):
		self.assertTrue(self.bind())
		self.devices = OTHER_DEVICES
		with self.assertRaises(self.provision.DeviceLimitError):
			self.recover()
		self.create.assert_not_called()
		self.native.GenerateKeyPair.assert_not_called()
		self.assertFalse(self.pending.exists())
		self.assert_original_config()

	def test_lost_create_reply_recovers_existing_replacement_after_restart(self):
		self.assertTrue(self.bind())
		self.devices = []
		self.create.side_effect = OSError('Response was lost')
		with self.assertRaises(self.provision.RegistrationPendingError):
			self.recover()
		self.assert_original_config()
		self.assertTrue(self.pending.exists())
		self.provision = load_module('test_mullvad_provision_restarted', PROVISION)
		self.devices = [NEW_DEVICE, *OTHER_DEVICES[:4]]
		with patch.object(self.provision, '_request', side_effect=self.request):
			self.requests.clear()
			result = self.provision.ensure_config(str(self.profile), str(self.account_path), self.native)
			self.assertEqual(result, str(self.config))
			self.assertEqual(self.requests, [])
			self.assert_original_config()
			self.assertTrue(self.recover())
		self.create.assert_called_once_with({'pubkey': NEW_PUBLIC, 'hijack_dns': False})
		self.native.GenerateKeyPair.assert_called_once()
		self.assertFalse(self.pending.exists())
		self.assert_replacement_config()

	def test_restart_finishes_local_bookkeeping_after_config_swap(self):
		self.assertTrue(self.bind())
		self.devices = []
		with patch.object(self.provision, '_save_binding', side_effect=OSError('Interrupted after config write')):
			with self.assertRaises(self.provision.ProvisionError):
				self.recover()
		self.assert_replacement_config()
		self.assertTrue(self.pending.exists())
		self.requests.clear()
		result = self.provision.ensure_config(str(self.profile), str(self.account_path), self.native, allow_registration=False)
		self.assertEqual(result, str(self.config))
		self.assertEqual(self.requests, [])
		self.assertFalse(self.pending.exists())
		self.devices = [NEW_DEVICE]
		self.assertFalse(self.recover())
		self.create.assert_called_once()
		self.native.GenerateKeyPair.assert_called_once()

	def test_revoked_staged_key_is_reconfirmed_before_replacing_config(self):
		self.assertTrue(self.bind())
		self.devices = []
		write = self.provision._private_write

		def interrupt_config_write(path, content):
			if Path(path) == self.config:
				raise OSError('Interrupted before config swap')
			write(path, content)

		with patch.object(self.provision, '_private_write', side_effect=interrupt_config_write):
			with self.assertRaises(self.provision.ProvisionError):
				self.recover()
		self.assertEqual(json.loads(self.pending.read_text(encoding='ascii'))['stage'], 'registered')
		self.assert_original_config()

		new_address = '10.64.7.16/32'
		self.create.return_value = dict(NEW_DEVICE, ipv4_address=new_address)
		self.assertTrue(self.recover())
		self.assertEqual(len(self.create.call_args_list), 2)
		self.assertEqual(self.create.call_args_list[0], self.create.call_args_list[1])
		self.native.GenerateKeyPair.assert_called_once()
		self.assertFalse(self.pending.exists())
		self.assert_replacement_config(address=new_address)


if __name__ == '__main__':
	unittest.main()
