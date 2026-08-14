import types
import unittest
from datetime import date, timedelta
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

	def test_recent_release_feeds_use_correct_release_type_and_rolling_window(self):
		today = date.today()
		start_date = today - timedelta(days=90)
		for function, release_type in ((self.tmdb.tmdb_movies_digital_releases, 4), (self.tmdb.tmdb_movies_physical_releases, 5)):
			with self.subTest(release_type=release_type):
				result = function(2)
				url = result['url']
				self.assertIn('with_release_type=%s' % release_type, url)
				self.assertIn('release_date.gte=%s' % start_date.isoformat(), url)
				self.assertIn('release_date.lte=%s' % today.isoformat(), url)
				self.assertIn('region=US', url)
				self.assertIn('page=2', url)
				self.assertIn('sort_by=primary_release_date.desc', url)

	def test_decade_feed_uses_inclusive_decade_bounds(self):
		url = self.tmdb.tmdb_movies_decade('2010', 3)['url']
		self.assertIn('primary_release_date.gte=2010-01-01', url)
		self.assertIn('primary_release_date.lte=2019-12-31', url)
		self.assertIn('page=3', url)

	def test_language_feed_filters_original_language(self):
		url = self.tmdb.tmdb_movies_language('ko', 1)['url']
		self.assertIn('with_original_language=ko', url)
		self.assertIn('sort_by=popularity.desc', url)

	def test_new_series_feed_uses_first_air_date_rolling_window(self):
		today = date.today()
		start_date = today - timedelta(days=90)
		url = self.tmdb.tmdb_tv_new_series(2)['url']
		self.assertIn('first_air_date.gte=%s' % start_date.isoformat(), url)
		self.assertIn('first_air_date.lte=%s' % today.isoformat(), url)
		self.assertIn('include_null_first_air_dates=false', url)
		self.assertIn('sort_by=first_air_date.desc', url)
		self.assertIn('page=2', url)

	def test_tv_decade_and_language_feeds_use_tv_filters(self):
		decade_url = self.tmdb.tmdb_tv_decade('2000', 1)['url']
		self.assertIn('first_air_date.gte=2000-01-01', decade_url)
		self.assertIn('first_air_date.lte=2009-12-31', decade_url)
		language_url = self.tmdb.tmdb_tv_language('ja', 1)['url']
		self.assertIn('with_original_language=ja', language_url)

	def test_year_decade_parent_offers_both_browsing_modes(self):
		self.menu.movie_years_decades()

		self.assertEqual([call.args[0]['name'] for call in self.menu._add_item.call_args_list], ['By Decade', 'By Year'])
		self.assertEqual([call.args[0]['mode'] for call in self.menu._add_item.call_args_list], ['navigator.decades', 'navigator.years'])
		self.menu._end_directory.assert_called_once_with()

	def test_tv_year_decade_parent_uses_tv_menu_type(self):
		self.menu.tv_years_decades()

		params = [call.args[0] for call in self.menu._add_item.call_args_list]
		self.assertEqual([item['menu_type'] for item in params], ['tvshow', 'tvshow'])
		self.assertEqual([item['mode'] for item in params], ['navigator.decades', 'navigator.years'])

	def test_language_directory_uses_unique_two_letter_tmdb_codes(self):
		meta_lists = types.ModuleType('modules.meta_lists')
		meta_lists.meta_languages = {
			'Korean': {'iso': 'ko'}, 'Portuguese': {'iso': 'pt'}, 'Portuguese (Brazil)': {'iso': 'pt-BR'}, 'Duplicate Korean': {'iso': 'ko'}
		}
		with temporary_modules({'modules.meta_lists': meta_lists}):
			self.menu.movie_languages()

		params = [call.args[0] for call in self.menu._add_item.call_args_list]
		self.assertEqual({item['language'] for item in params}, {'ko', 'pt'})
		self.assertTrue(all(item['action'] == 'tmdb_movies_language' for item in params))

	def test_tv_language_directory_uses_tv_list_action(self):
		meta_lists = types.ModuleType('modules.meta_lists')
		meta_lists.meta_languages = {'Japanese': {'iso': 'ja'}, 'Spanish': {'iso': 'es'}}
		with temporary_modules({'modules.meta_lists': meta_lists}):
			self.menu.tv_languages()

		params = [call.args[0] for call in self.menu._add_item.call_args_list]
		self.assertTrue(all(item['mode'] == 'build_tvshow_list' and item['action'] == 'tmdb_tv_language' for item in params))

	def test_studio_search_opens_movies_for_selected_company(self):
		tmdb_api = types.ModuleType('indexers.tmdb_api')
		tmdb_api.tmdb_company_id = Mock(return_value={'results': [
			{'id': 1, 'name': 'Other Studio', 'origin_country': 'US'}, {'id': 2, 'name': 'Chosen Studio', 'origin_country': 'CA'}
		]})
		indexers = types.ModuleType('indexers')
		indexers.tmdb_api = tmdb_api
		self.kodi_utils.dialog.input.return_value = 'studio'
		self.kodi_utils.select_dialog.return_value = '2'
		with temporary_modules({'indexers': indexers, 'indexers.tmdb_api': tmdb_api}):
			result = self.menu.movie_studios()

		self.assertIn('action=tmdb_movies_networks', result)
		self.assertIn('company=2', result)
		self.assertIn('name=Chosen Studio', result)


if __name__ == '__main__':
	unittest.main()
