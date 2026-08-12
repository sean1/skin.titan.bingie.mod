import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, call


ROOT = Path(__file__).resolve().parents[1]


def load_media_module():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.build_url = Mock(side_effect=lambda params: params)
	kodi_utils.make_listitem = Mock()
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	utils = types.ModuleType('modules.utils')
	utils.media_percentage_properties = Mock(side_effect=lambda rating: {'rating_percent': str(rating)})
	metadata = types.ModuleType('indexers.metadata')
	metadata.tmdb_image_base = 'https://image/%s%s'
	indexers = types.ModuleType('indexers')
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils, 'modules.utils': utils, 'indexers': indexers, 'indexers.metadata': metadata}
	previous = {name: sys.modules.get(name) for name in stubs}
	sys.modules.update(stubs)
	try:
		path = ROOT / 'resources' / 'lib' / 'menus' / 'media.py'
		spec = importlib.util.spec_from_file_location('test_menu_media_module', path)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
	finally:
		for name, old_module in previous.items():
			if old_module is None: sys.modules.pop(name, None)
			else: sys.modules[name] = old_module
	return module


class MenuMediaTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.media = load_media_module()

	def setUp(self):
		self.listitem = Mock()
		self.video = Mock()
		self.listitem.getVideoInfoTag.return_value = self.video
		self.media.kodi_utils.make_listitem.return_value = self.listitem
		self.media.kodi_utils.build_url.reset_mock()
		self.media.media_percentage_properties.reset_mock()

	def test_movie_summary_preserves_legacy_info_and_art(self):
		item = {
			'id': 101, 'title': 'Movie', 'release_date': '2024-06-07', 'vote_average': 7.5, 'genre_ids': [1, 99],
			'poster_path': '/poster.jpg', 'backdrop_path': '/backdrop.jpg', 'overview': 'Plot'
		}

		result = self.media.build_tmdb_detail_shelf_item(3, item, '55', 'movie', {1: 'Drama'}, 'poster-empty', 'fanart-empty', 19)

		self.assertEqual(result, ({'mode': 'show_media_info', 'mediatype': 'movie', 'tmdb_id': 101}, self.listitem, False))
		self.listitem.setLabel.assert_called_once_with('Movie')
		self.listitem.setProperties.assert_called_once_with({
			'PovLiteItem': 'true', 'PovLiteSummary': 'true', 'PovFocusIdentity': 'listing|movie|101', 'pov_lite_sort_order': '3',
			'tmdb_id': '101', 'PovInfoSourceTmdb': '55', 'rating_percent': '7.5'
		})
		self.listitem.setArt.assert_called_once_with({
			'poster': 'https://image/w342/poster.jpg', 'icon': 'https://image/w342/poster.jpg', 'fanart': 'https://image/w1280/backdrop.jpg',
			'thumb': 'https://image/w780/backdrop.jpg', 'landscape': 'https://image/w780/backdrop.jpg'
		})
		self.listitem.setUniqueIDs.assert_called_once_with({'tmdb': '101'})
		self.listitem.setInfo.assert_called_once_with('video', {
			'title': 'Movie', 'plot': 'Plot', 'premiered': '2024-06-07', 'year': '2024', 'rating': 7.5, 'genre': ['Drama'], 'mediatype': 'movie'
		})

	def test_tvshow_summary_preserves_modern_video_tag_and_fallback_art(self):
		item = {'id': 202, 'original_name': 'Show', 'first_air_date': '', 'vote_average': 0, 'genre_ids': [], 'poster_path': None, 'backdrop_path': None, 'overview': ''}

		result = self.media.build_tmdb_detail_shelf_item(4, item, None, 'tvshow', {}, 'poster-empty', 'fanart-empty', 21)

		self.assertEqual(result, ({'mode': 'show_media_info', 'mediatype': 'tvshow', 'tmdb_id': 202}, self.listitem, False))
		self.listitem.setArt.assert_called_once_with({
			'poster': 'poster-empty', 'icon': 'poster-empty', 'fanart': 'fanart-empty', 'thumb': 'fanart-empty', 'landscape': 'fanart-empty',
			'tvshow.poster': 'poster-empty', 'tvshow.landscape': 'fanart-empty'
		})
		self.video.setTitle.assert_called_once_with('Show')
		self.video.setTvShowTitle.assert_called_once_with('Show')
		self.video.setUniqueIDs.assert_called_once_with({'tmdb': '202'})
		self.video.setMediaType.assert_called_once_with('tvshow')
		self.video.setPlot.assert_called_once_with('')
		self.assertEqual(self.video.method_calls, [
			call.setTitle('Show'), call.setTvShowTitle('Show'), call.setUniqueIDs({'tmdb': '202'}), call.setMediaType('tvshow'), call.setPlot('')
		])

	def test_invalid_summary_item_is_ignored_before_listitem_creation(self):
		for item in ({'id': 1}, {'title': 'Movie'}):
			with self.subTest(item=item):
				self.media.kodi_utils.make_listitem.reset_mock()
				self.assertIsNone(self.media.build_tmdb_detail_shelf_item(0, item, None, 'movie', {}, 'poster-empty', 'fanart-empty', 21))
				self.media.kodi_utils.make_listitem.assert_not_called()


if __name__ == '__main__':
	unittest.main()
