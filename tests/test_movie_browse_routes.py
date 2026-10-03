import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module, temporary_modules


ROOT = Path(__file__).resolve().parents[1]


def load_tmdb_module():
	main_cache = types.ModuleType('caches.main_cache')
	main_cache.cache_object = Mock(side_effect=lambda function, key, url, **kwargs: {'key': key, 'url': url, **kwargs})
	meta_cache = types.ModuleType('caches.meta_cache')
	meta_cache.cache_function = lambda *args, **kwargs: (lambda function: function)
	caches = types.ModuleType('caches')
	caches.main_cache = main_cache
	caches.meta_cache = meta_cache
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.get_setting = lambda key: ''
	kodi_utils.local_string = str
	kodi_utils.logger = Mock()
	settings = types.ModuleType('modules.settings')
	settings.get_language = lambda: 'en-US'
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	modules.settings = settings
	stubs = {
		'caches': caches, 'caches.main_cache': main_cache, 'caches.meta_cache': meta_cache,
		'modules': modules, 'modules.kodi_utils': kodi_utils, 'modules.settings': settings
	}
	return load_module('test_movie_browse_tmdb', ROOT / 'resources' / 'lib' / 'indexers' / 'tmdb_api.py', stubs)


def load_navigator_module():
	navigator_cache = types.ModuleType('caches.navigator_cache')
	navigator_cache.navigator_cache = Mock()
	caches = types.ModuleType('caches')
	caches.navigator_cache = navigator_cache
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.local_string = str
	kodi_utils.build_url = lambda params: 'plugin://test?%s' % '&'.join('%s=%s' % item for item in params.items())
	kodi_utils.make_listitem = Mock()
	kodi_utils.media_path = lambda path: path
	kodi_utils.add_item = Mock()
	kodi_utils.add_items = Mock()
	kodi_utils.add_dir = Mock()
	kodi_utils.dialog = Mock()
	kodi_utils.notification = Mock()
	kodi_utils.select_dialog = Mock()
	kodi_utils.execute_builtin = Mock(side_effect=lambda command: command)
	settings = types.ModuleType('modules.settings')
	settings.addon_fanart = lambda: ''
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	modules.settings = settings
	stubs = {
		'caches': caches, 'caches.navigator_cache': navigator_cache,
		'modules': modules, 'modules.kodi_utils': kodi_utils, 'modules.settings': settings
	}
	module = load_module('test_movie_browse_navigator', ROOT / 'resources' / 'lib' / 'menus' / 'navigator.py', stubs)
	return module, kodi_utils


class MovieBrowseRouteTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.tmdb = load_tmdb_module()
		cls.navigator, cls.kodi_utils = load_navigator_module()

	def setUp(self):
		self.menu = object.__new__(self.navigator.Navigator)
		self.menu.params_get = lambda key, default=None: default
		self.menu._add_item = Mock()
		self.menu._end_directory = Mock()
		self.kodi_utils.dialog.reset_mock()
		self.kodi_utils.select_dialog.reset_mock()
		self.kodi_utils.execute_builtin.reset_mock()
		self.kodi_utils.notification.reset_mock()


	def test_other_network_search_lets_user_choose_between_matches(self):
		meta_lists = types.ModuleType('modules.meta_lists')
		meta_lists.networks = (
			{'id': 4, 'name': 'BBC One', 'logo': 'bbc1.png'}, {'id': 332, 'name': 'BBC Two', 'logo': 'bbc2.png'}
		)
		self.kodi_utils.dialog.input.return_value = 'bbc'
		self.kodi_utils.select_dialog.return_value = '332'
		with temporary_modules({'modules.meta_lists': meta_lists}):
			result = self.menu.search_tv_network()

		self.assertIn('network_id=332', result)
		self.assertIn('name=BBC Two', result)

	def test_other_network_search_cancel_or_no_results_does_not_navigate(self):
		meta_lists = types.ModuleType('modules.meta_lists')
		meta_lists.networks = ({'id': 213, 'name': 'Netflix', 'logo': 'netflix.png'},)
		with temporary_modules({'modules.meta_lists': meta_lists}):
			self.kodi_utils.dialog.input.return_value = ''
			self.assertIsNone(self.menu.search_tv_network())
			self.kodi_utils.dialog.input.return_value = 'missing'
			self.menu.search_tv_network()

		self.kodi_utils.notification.assert_called_once_with(32760)
		self.kodi_utils.execute_builtin.assert_not_called()



if __name__ == '__main__':
	unittest.main()
