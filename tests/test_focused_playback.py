import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module, temporary_modules


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'resources' / 'lib' / 'modules' / 'focused_playback.py'


def load_focused(labels):
	modules = types.ModuleType('modules')
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.get_infolabel = Mock(side_effect=lambda label: labels.get(label, ''))
	kodi_utils.get_visibility = Mock(return_value=False)
	kodi_utils.current_window_id = Mock(return_value=10000)
	kodi_utils.get_property = Mock(return_value='')
	modules.kodi_utils = kodi_utils
	focused = load_module('test_focused_playback_module', MODULE_PATH, {'modules': modules, 'modules.kodi_utils': kodi_utils})
	return focused


class FocusedPlaybackTests(unittest.TestCase):
	def test_movie_opens_manual_source_selection(self):
		focused = load_focused({'Container.ListItem.Property(PovLiteItem)': 'true', 'Container.ListItem.DBType': 'movie', 'Container.ListItem.UniqueID(tmdb)': '42'})
		sources = types.ModuleType('modules.sources')
		sources.Sources = types.SimpleNamespace(factory=Mock())
		with temporary_modules({'modules.sources': sources}): self.assertTrue(focused.source_select_focused())
		sources.Sources.factory.assert_called_once_with({'mode': 'play_media', 'mediatype': 'movie', 'tmdb_id': '42', 'autoplay': 'false'})

	def test_episode_passes_numbers_to_manual_selection(self):
		focused = load_focused({
			'ListItem.Property(PovLiteItem)': 'true', 'ListItem.Property(DBTYPE)': 'episode', 'ListItem.Property(tmdb_id)': '42',
			'ListItem.Season': '2', 'ListItem.Episode': '3'
		})
		sources = types.ModuleType('modules.sources')
		sources.Sources = types.SimpleNamespace(factory=Mock())
		with temporary_modules({'modules.sources': sources}): self.assertTrue(focused.source_select_focused())
		sources.Sources.factory.assert_called_once_with({'mode': 'play_media', 'mediatype': 'episode', 'tmdb_id': '42', 'autoplay': 'false', 'season': '2', 'episode': '3'})

	def test_real_episode_marker_is_accepted_without_generic_item_marker(self):
		focused = load_focused({
			'Container.ListItem.Property(PovLiteSourceSelect)': 'plugin://skin.titan.bingie.lite/?mode=play_media&mediatype=episode&tmdb_id=42&season=2&episode=3&autoplay=false',
			'Container.ListItem.DBType': 'episode', 'Container.ListItem.UniqueID(tmdb)': '42', 'Container.ListItem.Season': '2', 'Container.ListItem.Episode': '3'
		})
		sources = types.ModuleType('modules.sources')
		sources.Sources = types.SimpleNamespace(factory=Mock())
		with temporary_modules({'modules.sources': sources}): self.assertTrue(focused.source_select_focused())
		sources.Sources.factory.assert_called_once_with({'mode': 'play_media', 'mediatype': 'episode', 'tmdb_id': '42', 'autoplay': 'false', 'season': '2', 'episode': '3'})

	def test_tvshow_uses_smart_play_with_manual_selection(self):
		focused = load_focused({'Container.ListItem.Property(PovLiteItem)': 'true', 'Container.ListItem.DBType': 'tvshow', 'Container.ListItem.UniqueID(tmdb)': '42'})
		episode_tools = types.ModuleType('modules.episode_tools')
		episode_tools.SmartPlay = Mock()
		with temporary_modules({'modules.episode_tools': episode_tools}): self.assertTrue(focused.source_select_focused())
		episode_tools.SmartPlay.assert_called_once_with({'mode': 'play_media', 'mediatype': 'tvshow', 'tmdb_id': '42', 'autoplay': 'false'})

	def test_rejects_local_and_navigation_items(self):
		for labels in (
			{'Container.ListItem.DBType': 'movie', 'Container.ListItem.UniqueID(tmdb)': '42'},
			{'Container.ListItem.Property(PovLiteItem)': 'true', 'Container.ListItem.DBType': 'category', 'Container.ListItem.UniqueID(tmdb)': '42'},
			{'Container.ListItem.Property(PovLiteItem)': 'true', 'Container.ListItem.DBType': 'episode', 'Container.ListItem.UniqueID(tmdb)': '42'}
		): self.assertFalse(load_focused(labels).source_select_focused())

	def test_allows_pov_info_runtime_window_and_listing_visibility(self):
		labels = {'Container.ListItem.Property(PovLiteItem)': 'true', 'Container.ListItem.DBType': 'movie', 'Container.ListItem.UniqueID(tmdb)': '42'}
		for window_id, listing_visible in ((11123, False), (99999, True)):
			focused = load_focused(labels)
			focused.kodi_utils.current_window_id.return_value = window_id
			focused.kodi_utils.get_visibility.side_effect = lambda condition, visible=listing_visible: visible if condition == focused.ALLOWED_WINDOW_VISIBILITY else False
			sources = types.ModuleType('modules.sources')
			sources.Sources = types.SimpleNamespace(factory=Mock())
			with temporary_modules({'modules.sources': sources}): self.assertTrue(focused.source_select_focused())

	def test_rejects_playback_window_before_reading_focused_properties(self):
		focused = load_focused({'Container.ListItem.Property(PovLiteItem)': 'true', 'Container.ListItem.DBType': 'movie', 'Container.ListItem.UniqueID(tmdb)': '42'})
		focused.kodi_utils.get_visibility.side_effect = lambda condition: condition == focused.PLAYBACK_WINDOW_VISIBILITY
		self.assertFalse(focused.source_select_focused())
		focused.kodi_utils.get_infolabel.assert_not_called()

	def test_pov_info_uses_window_properties_when_play_button_has_no_item(self):
		for mediatype in ('movie', 'tvshow'):
			focused = load_focused({})
			focused.kodi_utils.current_window_id.return_value = 11123
			focused.kodi_utils.get_property.side_effect = lambda name, media=mediatype: media if name == 'PovInfoType' else '42'
			if mediatype == 'movie':
				sources = types.ModuleType('modules.sources')
				sources.Sources = types.SimpleNamespace(factory=Mock())
				with temporary_modules({'modules.sources': sources}): self.assertTrue(focused.source_select_focused())
				sources.Sources.factory.assert_called_once_with({'mode': 'play_media', 'mediatype': 'movie', 'tmdb_id': '42', 'autoplay': 'false'})
			else:
				episode_tools = types.ModuleType('modules.episode_tools')
				episode_tools.SmartPlay = Mock()
				with temporary_modules({'modules.episode_tools': episode_tools}): self.assertTrue(focused.source_select_focused())
				episode_tools.SmartPlay.assert_called_once_with({'mode': 'play_media', 'mediatype': 'tvshow', 'tmdb_id': '42', 'autoplay': 'false'})


if __name__ == '__main__': unittest.main()
