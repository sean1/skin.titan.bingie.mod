import types
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from tests.module_isolation import load_module
from tests.test_menu_completion import load_episode_module
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

	def test_missing_malformed_partial_and_non_calendar_dates_remain_unknown(self):
		invalid = (None, '', '2027', '2027-02', '2027-2-03', '2027-02-30', '2027-02-29', '2027-00-01', '2027-13-01', '2027-01-00', '0000-01-01', '10000-01-01')
		invalid += ('2026-W40-7', '20271004', '2026-10-04T00:00:00Z', '2026-10-04/2026-10-06', '2026–2027', ' 2026-10-04', True, 20271004, b'2026-10-04', {}, date(2026, 10, 4))
		for value in invalid:
			with self.subTest(value=value): self.assertFalse(self.media.card_coming_soon({'release_date': value}, 'movie', TODAY))
		self.assertFalse(self.media.card_coming_soon({}, 'movie', TODAY))


if __name__ == '__main__': unittest.main()
