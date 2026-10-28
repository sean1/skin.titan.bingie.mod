import sys
import types
import unittest
from pathlib import Path

from tests.module_isolation import load_module


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


	def test_cache_check_does_not_turn_api_failure_into_misses(self):
		self.api._post = lambda *args, **kwargs: None
		self.assertEqual(self.api.check_cache(['A' * 40]), {})


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


if __name__ == '__main__':
	unittest.main()
