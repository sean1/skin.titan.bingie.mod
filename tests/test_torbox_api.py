import sys
import types
import unittest
from pathlib import Path

from tests.module_isolation import load_module, temporary_modules


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'resources' / 'lib'
sys.path.insert(0, str(LIB))


def load_torbox():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.get_setting = lambda key, fallback=None: 'token' if key == 'tb.token' else fallback
	kodi_utils.logger = lambda *args: None
	kodi_utils.notification = lambda *args: None
	kodi_utils.sleep = lambda *args: None
	modules = types.ModuleType('modules')
	modules.__path__ = [str(LIB / 'modules')]
	modules.kodi_utils = kodi_utils
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils}
	return load_module('test_torbox_runtime', LIB / 'debrids' / 'torbox_api.py', stubs)


class TorBoxAPITests(unittest.TestCase):
	def setUp(self):
		self.module = load_torbox()
		self.api = self.module.TorBoxAPI()

	def test_cache_check_returns_exact_hits_and_misses(self):
		first, second = 'A' * 40, 'B' * 40
		requests = []
		def post(path, params=None, json=None, **kwargs):
			requests.append((path, params, json))
			return {first.lower(): {'hash': first.lower()}}
		self.api._post = post

		result = self.api.check_cache([first, second])

		self.assertEqual(result, {first: True, second: False})
		self.assertEqual(requests, [('torrents/checkcached', {'format': 'object', 'list_files': 'false'}, {'hashes': [first.lower(), second.lower()]})])

	def test_cache_check_does_not_turn_api_failure_into_misses(self):
		self.api._post = lambda *args, **kwargs: None
		self.assertEqual(self.api.check_cache(['A' * 40]), {})

	def test_cached_transfer_creation_uses_multipart_cached_only_flag(self):
		request = {}
		def post(path, **kwargs):
			request.update(path=path, **kwargs)
			return {'torrent_id': 44}
		self.api._post = post

		result = self.api.create_transfer('magnet:?xt=urn:btih:' + 'A' * 40, cached_only=True)

		self.assertEqual(result, 44)
		self.assertEqual(request['path'], 'torrents/createtorrent')
		self.assertEqual(request['files']['add_only_if_cached'], (None, 'true'))
		self.assertEqual(request['files']['allow_zip'], (None, 'false'))

	def test_parse_magnet_pack_returns_video_files_from_new_transfer(self):
		self.api._existing_transfer = lambda info_hash: (None, True)
		self.api.create_transfer = lambda magnet, cached_only=False: 44 if cached_only else None
		self.api.torrent_info = lambda transfer_id: {
			'id': transfer_id, 'download_present': True,
			'files': [
				{'id': 7, 'short_name': 'Show.S01E01.mkv', 'size': 123},
				{'id': 8, 'short_name': 'Show.S01E01.nfo', 'size': 10}
			]
		}
		source_utils = types.ModuleType('modules.source_utils')
		source_utils.supported_video_extensions = lambda: ['.mkv']
		with temporary_modules({'modules.source_utils': source_utils}):
			result = self.api.parse_magnet_pack('magnet:?xt=urn:btih:' + 'A' * 40, 'A' * 40, errors=True)

		self.assertEqual(result, [{'link': '44,7', 'size': 123, 'torrent_id': 44, 'filename': 'Show.S01E01.mkv'}])

	def test_parse_magnet_pack_preserves_existing_transfer(self):
		transfer = {'id': 44, 'hash': 'A' * 40, 'download_present': True, 'files': [{'id': 7, 'name': 'Movie.mkv', 'size': 123}]}
		self.api._existing_transfer = lambda info_hash: (transfer, True)
		self.api.create_transfer = lambda *args, **kwargs: self.fail('existing transfer must be reused')
		deleted = []
		self.api.delete_torrent = deleted.append
		source_utils = types.ModuleType('modules.source_utils')
		source_utils.supported_video_extensions = lambda: ['.mkv']
		with temporary_modules({'modules.source_utils': source_utils}):
			result = self.api.parse_magnet_pack('magnet:?xt=urn:btih:' + 'A' * 40, 'A' * 40, errors=True)

		self.assertEqual(result[0]['torrent_id'], '')
		self.assertEqual(deleted, [])

	def test_request_download_uses_opaque_ids_and_returns_direct_link(self):
		request = {}
		def get(path, params):
			request.update(path=path, params=params)
			return 'https://cdn.example/Movie.mkv'
		self.api._get = get

		result = self.api.unrestrict_link('44,7')

		self.assertEqual(result, 'https://cdn.example/Movie.mkv')
		self.assertEqual(request, {'path': 'torrents/requestdl', 'params': {'token': 'token', 'torrent_id': '44', 'file_id': '7'}})

	def test_failed_download_request_does_not_log_token_bearing_url(self):
		logs = []
		self.api.token = 'secret-token-value'
		self.module.kodi_utils.logger = lambda *args: logs.append(args)
		class Response:
			ok = False
			reason = 'Unauthorized'
			url = 'https://api.torbox.app/v1/api/torrents/requestdl?token=secret-token-value'
			def json(self): return {'success': False, 'error': 'AUTH_ERROR', 'detail': 'Unauthorized'}
		self.module.session.request = lambda *args, **kwargs: Response()

		self.assertIsNone(self.api.unrestrict_link('44,7'))
		self.assertNotIn('secret-token-value', repr(logs))

	def test_delete_uses_control_endpoint(self):
		request = {}
		def post(path, **kwargs):
			request.update(path=path, **kwargs)
			return {'success': True}
		self.api._post = post

		self.assertTrue(self.api.delete_torrent('44'))
		self.assertEqual(request, {'path': 'torrents/controltorrent', 'json': {'torrent_id': 44, 'operation': 'delete'}, 'raw': True})


if __name__ == '__main__':
	unittest.main()
