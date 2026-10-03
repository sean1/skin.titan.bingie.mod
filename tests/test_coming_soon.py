import types
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.module_isolation import load_module, temporary_modules
from tests.test_menu_completion import load_episode_module, load_menu_module
from tests.test_menu_media import load_media_module


ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 3)


class LocalDate(date):
	@classmethod
	def today(cls): return TODAY


def load_date_utils():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.local_string, kodi_utils.get_setting, kodi_utils.logger = str, Mock(), Mock()
	modules = types.ModuleType('modules')
	modules.__path__ = []
	return load_module('test_coming_soon_utils', ROOT / 'resources/lib/modules/utils.py', {'modules': modules, 'modules.kodi_utils': kodi_utils})


def load_episode_builders(media):
	episodes, kodi_utils = load_episode_module()
	episodes.card_coming_soon = media.card_coming_soon
	episodes.adjust_premiered_date = load_date_utils().adjust_premiered_date
	episodes.media_percentage_properties = lambda *args: {}
	episodes.settings.get_art_provider = Mock(return_value=())
	episodes.settings.show_specials = Mock(return_value=False)
	episodes.settings.thumb_fanart = Mock(return_value=False)
	episodes.settings.date_offset = Mock(return_value=0)
	metadata = types.ModuleType('indexers.metadata')
	for name in ('tvshow_meta', 'season_episodes_meta', 'episode_infodict', 'info_tagger', 'main_actors', 'resized_cast'):
		setattr(metadata, name, getattr(episodes, name))
	metadata.all_episodes_meta, metadata.season_infodict, metadata.tmdb_image_base = Mock(), Mock(), 'https://image/%s%s'
	cache = types.ModuleType('caches.watched_cache')
	for name in ('get_watched_info_tv', 'get_bookmarks', 'get_resumetime', 'set_resumetime', 'get_watched_status_episode'):
		setattr(cache, name, getattr(episodes, name))
	cache.get_watched_status_season = Mock()
	utils = types.ModuleType('modules.utils')
	utils.adjust_premiered_date, utils.get_datetime, utils.media_percentage_properties = episodes.adjust_premiered_date, Mock(), episodes.media_percentage_properties
	packages = {name: types.ModuleType(name) for name in ('modules', 'indexers', 'caches', 'menus')}
	for package in packages.values(): package.__path__ = []
	stubs = {
		**packages, 'modules.kodi_utils': kodi_utils, 'modules.settings': episodes.settings, 'modules.utils': utils,
		'indexers.metadata': metadata, 'caches.watched_cache': cache, 'menus.media': media,
	}
	seasons = load_module('test_coming_soon_seasons', ROOT / 'resources/lib/menus/seasons.py', stubs)
	return episodes, seasons, kodi_utils


class ComingSoonFixture:
	def setUp(self):
		self.media = load_media_module()
		clock = patch.object(self.media, 'date', LocalDate)
		clock.start()
		self.addCleanup(clock.stop)


class ComingSoonDateTests(ComingSoonFixture, unittest.TestCase):
	def test_only_dates_after_today_are_coming_soon(self):
		for mediatype, key in (('movie', 'release_date'), ('tvshow', 'first_air_date'), ('episode', 'premiered')):
			for value, expected in (('2026-10-02', False), ('2026-10-03', False), ('2026-10-04', True)):
				with self.subTest(mediatype=mediatype, value=value): self.assertEqual(self.media.card_coming_soon({key: value}, mediatype, TODAY), expected)

	def test_missing_malformed_partial_and_non_calendar_dates_remain_unknown(self):
		invalid = (None, '', '2027', '2027-02', '2027-2-03', '2027-02-30', '2027-02-29', '2027-00-01', '2027-13-01', '2027-01-00', '0000-01-01', '10000-01-01')
		invalid += ('2026-W40-7', '20271004', '2026-10-04T00:00:00Z', '2026-10-04/2026-10-06', '2026–2027', ' 2026-10-04', True, 20271004, b'2026-10-04', {}, date(2026, 10, 4))
		for value in invalid:
			with self.subTest(value=value): self.assertFalse(self.media.card_coming_soon({'release_date': value}, 'movie', TODAY))
		self.assertFalse(self.media.card_coming_soon({}, 'movie', TODAY))

	def test_raw_media_date_precedes_normalized_premiered_and_other_media_dates(self):
		for mediatype, key, unrelated in (('movie', 'release_date', 'first_air_date'), ('tvshow', 'first_air_date', 'release_date')):
			with self.subTest(mediatype=mediatype):
				data = {key: '2026-10-02', 'premiered': '2026-10-04', unrelated: '2026-10-04'}
				self.assertFalse(self.media.card_coming_soon(data, mediatype, TODAY))
				self.assertFalse(self.media.card_coming_soon({unrelated: '2026-10-04'}, mediatype, TODAY))

	def test_default_date_is_local_and_badge_disappears_on_the_release_day(self):
		class BeforeLocalMidnight(LocalDate):
			@classmethod
			def today(cls): return date(2026, 10, 2)
		data = {'release_date': '2026-10-03', 'original_language': 'en'}
		with patch.object(self.media, 'date', BeforeLocalMidnight):
			self.assertTrue(self.media.card_coming_soon(data, 'movie'))
			self.assertEqual(self.media.card_badge_properties(data, 'movie'), {'card_coming_soon': 'true', 'card_language': 'EN'})
		self.assertFalse(self.media.card_coming_soon(data, 'movie'))
		self.assertEqual(self.media.card_badge_properties(data, 'movie'), {'card_language': 'EN'})


class ComingSoonBuilderTests(ComingSoonFixture, unittest.TestCase):

	def build_episode(self, in_season_browser, premiered, show_unaired=True, adjust_hours=0):
		episodes, seasons, kodi_utils = load_episode_builders(self.media)
		local_today = date(2026, 10, 2)
		meta = {'tmdb_id': 202, 'tvdb_id': 303, 'imdb_id': 'tt0202', 'title': 'Series', 'year': 2020, 'total_seasons': 1, 'duration': 3600, 'cast': [], 'premiered': '2099-01-01'}
		item = {'season': 1, 'episode': 1, 'title': 'Episode', 'premiered': premiered}
		episodes.tvshow_meta.return_value, episodes.season_episodes_meta.return_value = meta, [item]
		episodes.get_watched_status_episode.return_value, episodes.get_resumetime.return_value, episodes.set_resumetime.return_value = (0, 4), ('0', '0'), (0, 0)
		episodes.info_tagger.return_value.getDuration.return_value = 3600
		episodes.settings.date_offset.return_value = adjust_hours
		listitem = Mock()
		kodi_utils.make_listitem = Mock(return_value=listitem)
		module = seasons if in_season_browser else episodes
		menu = module.Episodes.__new__(module.Episodes)
		menu.params, menu.current_date, menu.meta_user_info, menu.watched_info = {'tmdb_id': 202, 'season': 1}, local_today, {}, {}
		menu.watched_indicators, menu.is_widget, menu.show_unaired = 0, False, show_unaired
		menu.items = []
		menu.append = menu.items.append
		if in_season_browser:
			show = SimpleNamespace(meta=meta, tmdb_id=202, tvdb_id=303, imdb_id='tt0202', title='Series', cast=[], duration=3600, poster='', fanart='', clearlogo='', banner='', clearart='', landscape='')
			seasons.MetaParser = Mock(return_value=show)
			menu.poster_main, menu.poster_backup, menu.fanart_main, menu.fanart_backup = '', '', '', ''
			menu.build_season_list(menu.params)
		else:
			menu.list_type, menu.bookmarks, menu.adjust_hours, menu.thumb_fanart, menu.all_episodes = 'in_progress', {}, adjust_hours, False, 0
			menu.cm_sort, menu.art_provider, menu.container_update = {'options': 1, 'extras': 2, 'mark': 3}, (), 'Container.Update(%s)'
			menu._format_title, menu._episode_label_has_context = Mock(return_value='Episode'), Mock(return_value=False)
			menu.build_episode_content(0, {'season': 1, 'episode': 1, 'media_ids': {'tmdb': 202}})
		return menu.items, listitem

	def test_both_episode_builders_use_own_adjusted_air_date_and_listing_local_today(self):
		for in_season_browser in (False, True):
			for premiered, offset, expected in (('2026-10-03', 0, True), ('2026-10-02', 0, False), ('2026-10-02', 4, True), (None, 0, False)):
				with self.subTest(in_season_browser=in_season_browser, premiered=premiered, offset=offset):
					items, listitem = self.build_episode(in_season_browser, premiered, adjust_hours=offset)
					self.assertEqual(len(items), 1)
					properties = listitem.setProperties.call_args.args[0]
					self.assertEqual(properties.get('card_coming_soon') == 'true', expected)
					if offset: self.assertEqual(properties['pov_lite_first_aired'], '2026-10-03')


if __name__ == '__main__': unittest.main()
