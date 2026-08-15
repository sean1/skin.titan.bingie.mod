import sys
import types
import unittest
from pathlib import Path

from tests.module_isolation import load_module, temporary_modules


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

	def test_parse_magnet_pack_flattens_nested_video_files(self):
		self.api._existing_transfer_ids = lambda: set()
		self.api._upload_magnets = lambda magnets: [{'id': 44, 'hash': 'A' * 40, 'ready': True}]
		self.api.torrent_files = lambda transfer_id: [
			{'n': 'Show', 'e': [{'n': 'Season 01', 'e': [
				{'n': 'Show.S01E01.mkv', 's': 123, 'l': 'https://example/video'},
				{'n': 'Show.S01E01.nfo', 's': 10, 'l': 'https://example/nfo'}
			]}]}
		]
		source_utils = types.ModuleType('modules.source_utils')
		source_utils.supported_video_extensions = lambda: ['.mkv']
		with temporary_modules({'modules.source_utils': source_utils}):
			result = self.api.parse_magnet_pack('magnet:?xt=urn:btih:' + 'A' * 40, 'A' * 40, errors=True)

		self.assertEqual(result, [{
			'filename': 'Show/Season 01/Show.S01E01.mkv', 'size': 123,
			'link': 'https://example/video', 'torrent_id': 44
		}])

	def test_parse_magnet_pack_never_marks_existing_transfer_for_deletion(self):
		self.api._existing_transfer_ids = lambda: {'44'}
		self.api._upload_magnets = lambda magnets: [{'id': 44, 'hash': 'A' * 40, 'ready': True}]
		self.api.torrent_files = lambda transfer_id: [{'n': 'Movie.mkv', 's': 123, 'l': 'https://example/video'}]
		source_utils = types.ModuleType('modules.source_utils')
		source_utils.supported_video_extensions = lambda: ['.mkv']
		with temporary_modules({'modules.source_utils': source_utils}):
			result = self.api.parse_magnet_pack('magnet:?xt=urn:btih:' + 'A' * 40, 'A' * 40, errors=True)

		self.assertEqual(result[0]['torrent_id'], '')

	def test_unlock_returns_direct_link(self):
		self.api._post = lambda path, data: {'link': 'https://cdn.example/Movie.mkv'}
		self.assertEqual(self.api.unrestrict_link('https://alldebrid.com/f/id'), 'https://cdn.example/Movie.mkv')


if __name__ == '__main__':
	unittest.main()
