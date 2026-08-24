import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module, temporary_modules


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'resources' / 'lib' / 'modules' / 'focused_playback.py'
PLAY_CONTROL_VISIBILITY = 'Control.HasFocus(51) | Control.HasFocus(80)'


def load_focused(labels=None, properties=None, window_id=10000, play_focused=False):
	labels, properties = labels or {}, properties or {}
	modules = types.ModuleType('modules')
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.get_infolabel = Mock(side_effect=lambda label: labels.get(label, ''))
	kodi_utils.get_visibility = Mock(side_effect=lambda condition: play_focused if condition == PLAY_CONTROL_VISIBILITY else False)
	kodi_utils.current_window_id = Mock(return_value=window_id)
	kodi_utils.get_property = Mock(side_effect=lambda name: properties.get(name, ''))
	kodi_utils.execute_builtin = Mock()
	modules.kodi_utils = kodi_utils
	return load_module('test_focused_playback_module', MODULE_PATH, {'modules': modules, 'modules.kodi_utils': kodi_utils})


class FocusedPlaybackTests(unittest.TestCase):
	def test_movie_info_play_button_opens_manual_source_selection(self):
		focused = load_focused(properties={'PovInfoType': 'movie', 'PovInfoTmdb': '42'}, window_id=11123, play_focused=True)
		sources = types.ModuleType('modules.sources')
		sources.Sources = types.SimpleNamespace(factory=Mock())
		with temporary_modules({'modules.sources': sources}): self.assertTrue(focused.source_select_focused())
		sources.Sources.factory.assert_called_once_with({'mode': 'play_media', 'mediatype': 'movie', 'tmdb_id': '42', 'autoplay': 'false'})

	def test_tv_info_play_button_uses_smart_play_with_manual_selection(self):
		focused = load_focused(properties={'PovInfoType': 'tvshow', 'PovInfoTmdb': '42'}, window_id=11123, play_focused=True)
		episode_tools = types.ModuleType('modules.episode_tools')
		episode_tools.SmartPlay = Mock()
		with temporary_modules({'modules.episode_tools': episode_tools}): self.assertTrue(focused.source_select_focused())
		episode_tools.SmartPlay.assert_called_once_with({'mode': 'play_media', 'mediatype': 'tvshow', 'tmdb_id': '42', 'autoplay': 'false'})

	def test_listing_item_does_not_open_manual_sources(self):
		focused = load_focused({
			'Container.ListItem.Property(PovLiteItem)': 'true', 'Container.ListItem.DBType': 'movie', 'Container.ListItem.UniqueID(tmdb)': '42'
		})
		self.assertFalse(focused.source_select_focused())
		focused.kodi_utils.execute_builtin.assert_called_once_with('Action(ContextMenu)')

	def test_unrelated_info_control_restores_context_menu_without_reusing_background_item(self):
		focused = load_focused(
			{'Container.ListItem.Property(PovLiteItem)': 'true', 'Container.ListItem.DBType': 'movie', 'Container.ListItem.UniqueID(tmdb)': '99'},
			{'PovInfoType': 'movie', 'PovInfoTmdb': '42'}, window_id=11123
		)
		self.assertFalse(focused.source_select_focused())
		focused.kodi_utils.get_property.assert_not_called()
		focused.kodi_utils.execute_builtin.assert_called_once_with('Action(ContextMenu)')

	def test_play_control_id_outside_pov_info_does_not_open_manual_sources(self):
		focused = load_focused(properties={'PovInfoType': 'movie', 'PovInfoTmdb': '42'}, window_id=10000, play_focused=True)
		self.assertFalse(focused.source_select_focused())
		focused.kodi_utils.execute_builtin.assert_called_once_with('Action(ContextMenu)')

	def test_stale_global_mapping_restores_context_menu_outside_allowed_windows(self):
		focused = load_focused(window_id=10101)
		self.assertFalse(focused.source_select_focused())
		focused.kodi_utils.execute_builtin.assert_called_once_with('Action(ContextMenu)')

	def test_info_play_button_rejects_missing_or_invalid_media(self):
		for properties in ({'PovInfoType': 'movie'}, {'PovInfoType': 'episode', 'PovInfoTmdb': '42'}, {'PovInfoType': 'movie', 'PovInfoTmdb': '0'}):
			focused = load_focused(properties=properties, window_id=11123, play_focused=True)
			self.assertFalse(focused.source_select_focused())
			focused.kodi_utils.execute_builtin.assert_called_once_with('Action(ContextMenu)')

	def test_rejects_playback_window_before_checking_play_focus(self):
		focused = load_focused(properties={'PovInfoType': 'movie', 'PovInfoTmdb': '42'}, window_id=11123, play_focused=True)
		focused.kodi_utils.get_visibility.side_effect = lambda condition: condition == focused.PLAYBACK_WINDOW_VISIBILITY
		self.assertFalse(focused.source_select_focused())
		focused.kodi_utils.get_property.assert_not_called()
		focused.kodi_utils.execute_builtin.assert_not_called()


if __name__ == '__main__': unittest.main()
