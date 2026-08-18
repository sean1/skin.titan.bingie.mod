import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_media_module():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.add_dir = Mock()
	kodi_utils.set_category = Mock()
	kodi_utils.set_sort_method = Mock()
	kodi_utils.set_content = Mock()
	kodi_utils.end_directory = Mock()
	kodi_utils.set_view_mode = Mock()
	kodi_utils.build_url = Mock(side_effect=lambda params: params)
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	utils = types.ModuleType('modules.utils')
	utils.media_percentage_properties = Mock()
	metadata = types.ModuleType('indexers.metadata')
	metadata.tmdb_image_base = 'https://image/%s%s'
	indexers = types.ModuleType('indexers')
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils, 'modules.utils': utils, 'indexers': indexers, 'indexers.metadata': metadata}
	path = ROOT / 'resources' / 'lib' / 'menus' / 'media.py'
	return load_module('test_menu_completion_media', path, stubs)


def load_menu_module(mediatype):
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.argv1 = lambda: '7'
	kodi_utils.get_kodi_version = lambda: 21
	kodi_utils.make_cast_list = lambda value: value
	kodi_utils.local_string = str
	kodi_utils.build_url = lambda params: params
	kodi_utils.get_infolabel = lambda label: ''
	kodi_utils.get_addoninfo = lambda key: ''
	kodi_utils.media_path = lambda name: name
	kodi_utils.external_browse = lambda: False
	kodi_utils.end_directory = Mock()
	settings = types.ModuleType('modules.settings')
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	modules.settings = settings
	utils = types.ModuleType('modules.utils')
	utils.LIST_WORKERS = 5
	utils.get_datetime = Mock()
	utils.media_percentage_properties = Mock(return_value={})
	utils.valid_tmdb_id = lambda value: bool(value)
	utils.TaskPool = Mock
	utils.manual_function_import = lambda *args: (_ for _ in ()).throw(RuntimeError('provider failed'))
	metadata = types.ModuleType('indexers.metadata')
	for name in ('movie_meta', 'tvshow_meta', 'art_infodict', 'movie_show_infodict', 'main_actors', 'resized_cast'):
		setattr(metadata, name, Mock())
	metadata.tmdb_image_base = 'https://image/%s%s'
	indexers = types.ModuleType('indexers')
	indexers.__path__ = []
	cache = types.ModuleType('caches.watched_cache')
	for name in ('get_watched_info_movie', 'get_watched_status_movie', 'get_bookmarks', 'get_resumetime', 'set_resumetime', 'get_watched_info_tv', 'get_watched_status_tvshow'):
		setattr(cache, name, Mock())
	caches = types.ModuleType('caches')
	caches.__path__ = []
	meta_lists = types.ModuleType('modules.meta_lists')
	meta_lists.movie_genres = {'Drama': ('1',)}
	meta_lists.tvshow_genres = {'Drama': ('1',)}
	media = types.ModuleType('menus.media')
	media.build_tmdb_detail_shelf_item = Mock()
	media.card_flag = lambda data: ''
	media.card_language = lambda data: ''
	media.complete_media_directory = Mock(side_effect=lambda handle, *args: kodi_utils.end_directory(handle, None))
	menus = types.ModuleType('menus')
	menus.__path__ = []
	stubs = {
		'caches': caches, 'caches.watched_cache': cache, 'indexers': indexers, 'indexers.metadata': metadata, 'menus': menus, 'menus.media': media,
		'modules': modules, 'modules.kodi_utils': kodi_utils, 'modules.settings': settings, 'modules.meta_lists': meta_lists, 'modules.utils': utils
	}
	filename = 'movies.py' if mediatype == 'movie' else 'tvshows.py'
	module = load_module('test_menu_completion_%s' % mediatype, ROOT / 'resources' / 'lib' / 'menus' / filename, stubs)
	return module, media.complete_media_directory, kodi_utils


class MenuCompletionTests(unittest.TestCase):
	def setUp(self):
		self.media = load_media_module()
		self.media._schedule_next_page_prefetch = Mock()

	def complete(self, **overrides):
		values = {
			'handle': 7, 'mode': 'build_movie_list', 'action': 'tmdb_movies_popular', 'exit_list_params': 'plugin://origin', 'category': 'Popular',
			'content_type': 'movies', 'view_type': 'view.movies', 'is_widget': False, 'new_page': {'new_page': '2'}, 'limited_tmdb': False,
			'origin_params': {'mode': 'build_movie_list', 'action': 'tmdb_movies_popular'}, 'nextpage_label': 'Next', 'nextpage_icon': 'next.png'
		}
		values.update(overrides)
		self.media.complete_media_directory(**values)

	def test_standard_page_adds_next_page_finishes_and_schedules_prefetch(self):
		self.complete()

		expected_page = {
			'new_page': '2', 'mode': 'build_movie_list', 'action': 'tmdb_movies_popular', 'exit_list_params': 'plugin://origin', 'name': 'Popular'
		}
		self.media.kodi_utils.add_dir.assert_called_once_with(7, expected_page, 'Next', 'next.png')
		self.media.kodi_utils.set_category.assert_called_once_with(7, 'Popular')
		self.media.kodi_utils.set_sort_method.assert_called_once_with(7, 'movies')
		self.media.kodi_utils.set_content.assert_called_once_with(7, 'movies')
		self.media.kodi_utils.end_directory.assert_called_once_with(7, None)
		self.media.kodi_utils.build_url.assert_called_once_with({**expected_page, 'prefetch': 'true'})
		self.media.kodi_utils.set_view_mode.assert_called_once_with('view.movies', 'movies', False)
		self.media._schedule_next_page_prefetch.assert_called_once_with(
			{**expected_page, 'prefetch': 'true'}, {'mode': 'build_movie_list', 'action': 'tmdb_movies_popular'}
		)

	def test_limited_page_uses_browse_link_without_background_prefetch(self):
		self.complete(limited_tmdb=True)

		self.media.kodi_utils.add_dir.assert_called_once_with(7, {
			'mode': 'build_movie_list', 'action': 'tmdb_movies_popular', 'exit_list_params': 'plugin://origin', 'name': 'Popular'
		}, 'Next', 'next.png')
		self.media._schedule_next_page_prefetch.assert_not_called()

	def test_widget_finishes_without_navigation_or_prefetch(self):
		self.complete(is_widget=True)

		self.media.kodi_utils.add_dir.assert_not_called()
		self.media.kodi_utils.end_directory.assert_called_once_with(7, False)
		self.media._schedule_next_page_prefetch.assert_not_called()
		self.media.kodi_utils.set_view_mode.assert_called_once_with('view.movies', 'movies', True)

	def test_pagination_error_with_empty_category_still_finishes_directory(self):
		self.media.kodi_utils.add_dir.side_effect = RuntimeError('pagination failed')

		self.complete(category='')

		self.media.kodi_utils.set_category.assert_called_once_with(7, '')
		self.media.kodi_utils.end_directory.assert_called_once_with(7, None)
		self.media.kodi_utils.set_view_mode.assert_called_once_with('view.movies', 'movies', False)

	def test_movie_and_tv_callers_complete_after_provider_import_failure(self):
		cases = (
			('movie', 'build_movie_list', 'tmdb_movies_popular', 'view.movies', 'movies'),
			('tvshow', 'build_tvshow_list', 'tmdb_tv_popular', 'view.tvshows', 'tvshows')
		)
		for mediatype, mode, action, view_type, content_type in cases:
			with self.subTest(mediatype=mediatype):
				module, complete, kodi_utils = load_menu_module(mediatype)
				menu = module.Menu.__new__(module.Menu)
				menu.params = {'mode': mode, 'name': 'Popular'}
				menu.action = action
				menu.exit_list_params = 'plugin://origin'
				menu.is_widget = False
				menu.new_page = {}
				menu.total_pages = None

				menu.run()

				complete.assert_called_once_with(
					7, mode, action, 'plugin://origin', 'Popular', content_type, view_type, False, {}, False, menu.params,
					module.nextpage_str, module.item_next
				)
				kodi_utils.end_directory.assert_called_once_with(7, None)


if __name__ == '__main__':
	unittest.main()
