import types
import unittest
from pathlib import Path
from unittest.mock import Mock, call

from tests.module_isolation import load_module


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
	path = ROOT / 'resources' / 'lib' / 'menus' / 'media.py'
	return load_module('test_menu_media_module', path, stubs)


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
			'poster_path': '/poster.jpg', 'backdrop_path': '/backdrop.jpg', 'overview': 'Plot', 'original_language': 'en', 'country_codes': ['US']
		}

		result = self.media.build_tmdb_detail_shelf_item(3, item, '55', 'movie', {1: 'Drama'}, 'poster-empty', 'fanart-empty', 19)

		self.assertEqual(result, ({'mode': 'show_media_info', 'mediatype': 'movie', 'tmdb_id': 101}, self.listitem, False))
		self.listitem.setLabel.assert_called_once_with('Movie')
		self.listitem.setProperties.assert_called_once_with({
			'PovLiteItem': 'true', 'PovLiteSummary': 'true', 'PovFocusIdentity': 'listing|movie|101', 'pov_lite_sort_order': '3',
			'tmdb_id': '101', 'PovInfoSourceTmdb': '55',
			'PovLiteSourceSelect': {'mode': 'play_media', 'mediatype': 'movie', 'tmdb_id': 101, 'autoplay': 'false'},
			'card_language': 'EN', 'rating_percent': '7.5'
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
		item = {
			'id': 202, 'original_name': 'Show', 'first_air_date': '', 'vote_average': 0, 'genre_ids': [], 'poster_path': None, 'backdrop_path': None,
			'overview': '', 'origin_country': ['CA', 'US'], 'original_language': 'en'
		}

		result = self.media.build_tmdb_detail_shelf_item(4, item, None, 'tvshow', {}, 'poster-empty', 'fanart-empty', 21)

		self.assertEqual(result, ({'mode': 'show_media_info', 'mediatype': 'tvshow', 'tmdb_id': 202}, self.listitem, False))
		self.listitem.setArt.assert_called_once_with({
			'poster': 'poster-empty', 'icon': 'poster-empty', 'fanart': 'fanart-empty', 'thumb': 'fanart-empty', 'landscape': 'fanart-empty',
			'tvshow.poster': 'poster-empty', 'tvshow.landscape': 'fanart-empty'
		})
		self.listitem.setProperties.assert_called_once_with({
			'PovLiteItem': 'true', 'PovLiteSummary': 'true', 'PovFocusIdentity': 'listing|tvshow|202', 'pov_lite_sort_order': '4', 'tmdb_id': '202',
			'PovInfoSourceTmdb': '', 'PovLiteSourceSelect': {'mode': 'smart_play_media', 'tmdb_id': 202, 'autoplay': 'false'},
			'card_flag': 'flags/country/ca.png', 'rating_percent': '0'
		})
		self.video.setTitle.assert_called_once_with('Show')
		self.video.setTvShowTitle.assert_called_once_with('Show')
		self.video.setUniqueIDs.assert_called_once_with({'tmdb': '202'})
		self.video.setMediaType.assert_called_once_with('tvshow')
		self.video.setPlot.assert_called_once_with('')
		self.assertEqual(self.video.method_calls, [
			call.setTitle('Show'), call.setTvShowTitle('Show'), call.setUniqueIDs({'tmdb': '202'}), call.setMediaType('tvshow'), call.setPlot('')
		])

	def test_country_code_uses_only_explicit_query_country_fields(self):
		cases = (
			({'country_codes': ['GB', 'US']}, 'gb'),
			({'origin_country': ['JP']}, 'jp'),
			({'production_countries': [{'iso_3166_1': 'DE'}]}, 'de'),
			({'origin_country': ['', 'CA']}, 'ca'),
			({'original_language': 'fr'}, ''),
			({}, '')
		)
		for data, expected in cases:
			with self.subTest(data=data): self.assertEqual(self.media.first_country_code(data), expected)

	def test_card_badges_follow_media_type_when_country_and_language_are_both_available(self):
		data = {'country_codes': ['GB'], 'original_language': 'en'}
		self.assertEqual(self.media.card_badge_properties(data, 'movie'), {'card_language': 'EN'})
		self.assertEqual(self.media.card_badge_properties(data, 'tvshow'), {'card_flag': 'flags/country/gb.png'})

	def test_card_country_and_language_values_are_extracted_independently(self):
		cases = (
			({'country_codes': ['GB'], 'original_language': 'en'}, 'flags/country/gb.png', 'EN'),
			({'original_language': ' JA '}, '', 'JA'),
			({'original_language': 'bn'}, '', 'BN'),
			({'original_language': 'xx'}, '', ''),
			({'original_language': 'e1'}, '', ''),
			({'original_language': 'pt-BR'}, '', ''),
			({'original_language': 'x'}, '', ''),
			({'original_language': '123'}, '', ''),
			({}, '', '')
		)
		for data, expected_flag, expected_language in cases:
			with self.subTest(data=data):
				self.assertEqual(self.media.card_flag(data), expected_flag)
				self.assertEqual(self.media.card_language(data), expected_language)

	def test_card_badges_require_the_expected_metadata_for_each_media_type(self):
		self.assertEqual(self.media.card_badge_properties({'country_codes': ['US']}, 'movie'), {})
		self.assertEqual(self.media.card_badge_properties({'original_language': 'en'}, 'tvshow'), {})
		self.assertEqual(self.media.card_badge_properties({'country_codes': ['US'], 'original_language': 'en'}, 'episode'), {})

	def test_invalid_summary_item_is_ignored_before_listitem_creation(self):
		for item in ({'id': 1}, {'title': 'Movie'}):
			with self.subTest(item=item):
				self.media.kodi_utils.make_listitem.reset_mock()
				self.assertIsNone(self.media.build_tmdb_detail_shelf_item(0, item, None, 'movie', {}, 'poster-empty', 'fanart-empty', 21))
				self.media.kodi_utils.make_listitem.assert_not_called()


if __name__ == '__main__':
	unittest.main()
