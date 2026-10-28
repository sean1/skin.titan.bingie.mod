import sys
import types
import unittest
from pathlib import Path

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'resources' / 'lib'
sys.path.insert(0, str(LIB))


def load_all_debrid():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.get_setting = lambda key, fallback=None: 'token' if key == 'ad.token' else fallback
	kodi_utils.logger = lambda *args: None
	kodi_utils.notification = lambda *args: None
	kodi_utils.sleep = lambda *args: None
	modules = types.ModuleType('modules')
	modules.__path__ = [str(LIB / 'modules')]
	modules.kodi_utils = kodi_utils
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils}
	return load_module('test_all_debrid_runtime', LIB / 'debrids' / 'all_debrid_api.py', stubs)


class AllDebridAPITests(unittest.TestCase):
	def setUp(self):
		self.module = load_all_debrid()
		self.api = self.module.AllDebridAPI()

	def test_cache_check_preserves_existing_transfers_and_cleans_only_new_ones(self):
		deleted = []
		self.api._existing_transfer_ids = lambda: {'9'}
		self.api._upload_magnets = lambda hashes: [
			{'hash': hashes[0], 'id': 9, 'ready': True},
			{'hash': hashes[1], 'id': 10, 'ready': False},
			{'hash': hashes[2], 'id': 11, 'ready': True}
		]
		self.api.delete_torrent = deleted.append

		result = self.api.check_cache(['A' * 40, 'B' * 40, 'C' * 40])

		self.assertEqual(result, {'A' * 40: True, 'B' * 40: False, 'C' * 40: True})
		self.assertEqual(deleted, [10, 11])

	def test_cache_check_is_bounded_and_does_not_turn_api_failure_into_misses(self):
		checked = []
		self.api._existing_transfer_ids = lambda: set()
		self.api._upload_magnets = lambda hashes: checked.extend(hashes)

		result = self.api.check_cache(['%040d' % value for value in range(40)])

		self.assertEqual(result, {})
		self.assertEqual(len(checked), self.module.cache_check_chunk_size)


if __name__ == '__main__':
	unittest.main()
