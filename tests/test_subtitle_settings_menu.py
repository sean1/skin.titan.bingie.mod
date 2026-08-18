import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, call

from tests.test_dialog_navigation import load_dialogs

ROOT = Path(__file__).resolve().parents[1]


class SubtitleSettingsMenuTests(unittest.TestCase):
	def setUp(self):
		self.dialogs = load_dialogs()
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

	def test_menu_contains_only_supported_actions(self):
		self.dialogs.kodi_utils.dialog.select.return_value = -1

		self.dialogs.subtitle_settings_menu()

		options = self.dialogs.kodi_utils.dialog.select.call_args.args[1]
		self.assertEqual(options, ['13397: 16041', '22006: 0.000s', '462: English (1/2)', '24134'])
		self.assertNotIn('13250', options)
		self.assertNotIn('12376', options)

	def test_offset_opens_native_subtitle_delay_slider(self):
		self.dialogs.kodi_utils.dialog.select.return_value = 1

		self.dialogs.subtitle_settings_menu()

		self.dialogs.execute_builtin.assert_called_once_with('Action(SubtitleDelay)')

	def test_enable_toggle_uses_player_api(self):
		self.dialogs.kodi_utils.dialog.select.return_value = 0

		self.dialogs.subtitle_settings_menu()

		self.dialogs._subtitle_rpc.assert_called_with('Player.SetSubtitle', {'playerid': 1, 'subtitle': 'off'})

	def test_subtitle_stream_selection_uses_player_api(self):
		self.dialogs.kodi_utils.dialog.select.side_effect = (2, 1)

		self.dialogs.subtitle_settings_menu()

		self.dialogs._subtitle_rpc.assert_has_calls([
			call('Player.GetActivePlayers'),
			call('Player.GetProperties', {'playerid': 1, 'properties': ['subtitleenabled', 'subtitles', 'currentsubtitle']}),
			call('Player.SetSubtitle', {'playerid': 1, 'subtitle': 1, 'enable': True}),
		])

	def test_download_opens_subtitle_search(self):
		self.dialogs.kodi_utils.dialog.select.return_value = 3

		self.dialogs.subtitle_settings_menu()

		self.dialogs.execute_builtin.assert_called_once_with('ActivateWindow(subtitlesearch)')

	def test_both_osd_buttons_open_the_focused_menu(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesOSD.xml').getroot()
		actions = [(node.text or '').strip() for node in root.iter('onclick')]

		self.assertEqual(actions.count('RunPlugin(plugin://skin.titan.bingie.lite/?mode=subtitle_settings)'), 2)
		self.assertNotIn('ActivateWindow(osdsubtitlesettings)', actions)


if __name__ == '__main__':
	unittest.main()
