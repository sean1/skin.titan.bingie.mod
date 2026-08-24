import sys
import types
import unittest
from pathlib import Path

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'resources' / 'lib'
sys.path.insert(0, str(LIB))


def load_real_debrid():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.local_string = str
	kodi_utils.get_setting = lambda key, fallback=None: 'token' if key == 'rd.token' else fallback
	kodi_utils.logger = lambda *args: None
	kodi_utils.notification = lambda *args: None
	kodi_utils.set_settings = lambda *args: True
	modules = types.ModuleType('modules')
	modules.__path__ = [str(LIB / 'modules')]
	modules.kodi_utils = kodi_utils
	main_cache = types.ModuleType('caches.main_cache')
	main_cache.cache_object = lambda function, _key, params, _expiry: function(params)
	main_cache.clear_main_cache_property = lambda *args: None
	caches = types.ModuleType('caches')
	caches.__path__ = [str(LIB / 'caches')]
	caches.main_cache = main_cache
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils, 'caches': caches, 'caches.main_cache': main_cache}
	return load_module('test_real_debrid_runtime', LIB / 'debrids' / 'real_debrid_api.py', stubs)


class Response:
	def __init__(self, status_code, content=b'', payload=None):
		self.status_code = status_code
		self.content = content
		self.payload = payload
		self.ok = 200 <= status_code < 400
		self.reason = 'reason'
		self.url = 'https://app.real-debrid.com/rest/1.0/test'
		self.request = types.SimpleNamespace(headers={})

	def json(self):
		if isinstance(self.payload, Exception): raise self.payload
		return self.payload


class RealDebridAPITests(unittest.TestCase):
	def setUp(self):
		self.module = load_real_debrid()
		self.api = self.module.RealDebridAPI()

	def test_delete_endpoints_accept_empty_success_response(self):
		requests = []
		def request(method, path, data=None, timeout=None):
			requests.append((method, path))
			return Response(204)
		self.module.session.request = request

		self.assertTrue(self.api.delete_torrent('44'))
		self.assertTrue(self.api.delete_download('55'))
		self.assertEqual(requests, [('delete', self.module.base_url + 'torrents/delete/44'), ('delete', self.module.base_url + 'downloads/delete/55')])

	def test_delete_endpoints_reject_json_error_response(self):
		self.module.session.request = lambda *args, **kwargs: Response(404, b'json', {'error': 'unknown_resource', 'error_code': 7})

		self.assertFalse(self.api.delete_torrent('44'))
		self.assertFalse(self.api.delete_download('55'))

	def test_delete_rejects_malformed_response_body(self):
		self.module.session.request = lambda *args, **kwargs: Response(502, b'html', ValueError('invalid JSON'))

		self.assertFalse(self.api.delete_torrent('44'))

	def test_delete_rejects_timeout_during_authenticated_retry(self):
		self.module.session.request = lambda *args, **kwargs: Response(401, b'json', {'error': 'bad_token', 'error_code': 8})
		self.module.session.send = lambda *args, **kwargs: (_ for _ in ()).throw(self.module.requests.Timeout('retry timeout'))
		self.api.refresh_token = lambda: True

		self.assertFalse(self.api.delete_torrent('44'))


if __name__ == '__main__':
	unittest.main()
