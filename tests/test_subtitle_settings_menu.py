import types
import unittest
from pathlib import Path
from unittest.mock import Mock, call

from tests.module_isolation import temporary_modules
from tests.test_dialog_navigation import load_dialogs

ROOT = Path(__file__).resolve().parents[1]


class SubtitleSettingsMenuTests(unittest.TestCase):
	def setUp(self):
		self.dialogs = load_dialogs()
		self.mark_manual_selection = Mock(return_value=True)
		indexers = types.ModuleType('indexers')
		indexers.__path__ = []
		subtitles = types.ModuleType('indexers.subtitles')
		subtitles.mark_manual_selection = self.mark_manual_selection
		isolated = temporary_modules({'indexers': indexers, 'indexers.subtitles': subtitles})
		isolated.__enter__()
		self.addCleanup(isolated.__exit__, None, None, None)
		self.dialogs.kodi_utils.xbmc = Mock()
		self.dialogs.kodi_utils.xbmc.getLocalizedString.side_effect = lambda value: str(value)
		self.dialogs.kodi_utils.get_infolabel = Mock(return_value='0.000s')
		self.dialogs.kodi_utils.dialog = Mock()
		self.dialogs.execute_builtin = Mock()
		self.state = {
			'subtitleenabled': True,
			'subtitles': [{'index': 0, 'language': 'eng', 'name': 'English'}, {'index': 1, 'language': 'spa', 'name': 'Spanish'}],
			'currentsubtitle': {'index': 0, 'language': 'eng', 'name': 'English'},
		}
		self.dialogs._subtitle_rpc = Mock(side_effect=lambda method, params=None: [{'playerid': 1, 'type': 'video'}] if method == 'Player.GetActivePlayers' else self.state if method == 'Player.GetProperties' else 'OK')

	def test_enable_toggle_uses_player_api(self):
		self.dialogs.kodi_utils.dialog.select.return_value = 0

		self.dialogs.subtitle_settings_menu()

		self.dialogs._subtitle_rpc.assert_called_with('Player.SetSubtitle', {'playerid': 1, 'subtitle': 'off'})
		self.mark_manual_selection.assert_called_once_with()

	def test_manual_off_is_recorded_before_the_player_is_changed(self):
		events = []
		self.mark_manual_selection.side_effect = lambda: events.append('manual')
		rpc = self.dialogs._subtitle_rpc.side_effect
		self.dialogs._subtitle_rpc.side_effect = lambda method, params=None: events.append('off') if method == 'Player.SetSubtitle' else rpc(method, params)
		self.dialogs.kodi_utils.dialog.select.return_value = 0
		self.dialogs.subtitle_settings_menu()
		self.assertEqual(events, ['manual', 'off'])

	def test_manual_enable_is_preserved_even_from_initial_disabled_state(self):
		self.state['subtitleenabled'] = False
		self.dialogs.kodi_utils.dialog.select.return_value = 0
		self.dialogs.subtitle_settings_menu()
		self.mark_manual_selection.assert_called_once_with()
		self.dialogs._subtitle_rpc.assert_called_with('Player.SetSubtitle', {'playerid': 1, 'subtitle': 'on'})

	def test_subtitle_stream_selection_uses_player_api(self):
		self.dialogs.kodi_utils.dialog.select.side_effect = (2, 1)

		self.dialogs.subtitle_settings_menu()

		self.dialogs._subtitle_rpc.assert_has_calls([
			call('Player.GetActivePlayers'),
			call('Player.GetProperties', {'playerid': 1, 'properties': ['subtitleenabled', 'subtitles', 'currentsubtitle']}),
			call('Player.SetSubtitle', {'playerid': 1, 'subtitle': 1, 'enable': True}),
		])
		self.mark_manual_selection.assert_called_once_with()

	def test_cancelled_stream_selection_does_not_block_automatic_subtitles(self):
		self.dialogs.kodi_utils.dialog.select.side_effect = (2, -1)
		self.dialogs.subtitle_settings_menu()
		self.mark_manual_selection.assert_not_called()
		self.assertFalse(any(invocation.args[0] == 'Player.SetSubtitle' for invocation in self.dialogs._subtitle_rpc.call_args_list))


if __name__ == '__main__':
	unittest.main()
