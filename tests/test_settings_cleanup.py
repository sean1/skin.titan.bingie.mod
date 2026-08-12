import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


class Addon:
	def getAddonInfo(self, key):
		return {'id': 'skin.titan.bingie.lite', 'path': '', 'profile': ''}.get(key, '')

	def getLocalizedString(self, value):
		return str(value)


class Window:
	def getProperty(self, key):
		return ''

	def setProperty(self, key, value):
		return None

	def clearProperty(self, key):
		return None


def load_kodi_utils():
	xbmc = types.ModuleType('xbmc')
	xbmc.Player = xbmc.Monitor = object
	xbmc.executeJSONRPC = xbmc.getInfoLabel = lambda *args: ''
	xbmc.getCondVisibility = lambda *args: False
	xbmc.sleep = xbmc.executebuiltin = xbmc.log = lambda *args: None
	xbmcgui = types.ModuleType('xbmcgui')
	xbmcgui.Window = lambda *args: Window()
	xbmcgui.Dialog = xbmcgui.DialogProgress = xbmcgui.DialogProgressBG = xbmcgui.WindowXMLDialog = object
	xbmcgui.ACTION_SHOW_INFO = xbmcgui.ACTION_PARENT_DIR = xbmcgui.ACTION_PREVIOUS_MENU = xbmcgui.ACTION_STOP = 0
	xbmcgui.ACTION_NAV_BACK = xbmcgui.ACTION_SELECT_ITEM = xbmcgui.ACTION_MOUSE_START = xbmcgui.ACTION_CONTEXT_MENU = 0
	xbmcgui.ACTION_MOUSE_RIGHT_CLICK = xbmcgui.ACTION_MOUSE_LONG_CLICK = xbmcgui.ACTION_MOVE_LEFT = xbmcgui.ACTION_MOVE_RIGHT = 0
	xbmcgui.ACTION_MOVE_UP = xbmcgui.ACTION_MOVE_DOWN = 0
	xbmcplugin = types.ModuleType('xbmcplugin')
	xbmcvfs = types.ModuleType('xbmcvfs')
	xbmcvfs.translatePath = lambda path: path
	xbmcvfs.exists = lambda path: Path(path).exists()
	xbmcvfs.mkdir = xbmcvfs.mkdirs = lambda path: Path(path).mkdir(parents=True, exist_ok=True)
	xbmcaddon = types.ModuleType('xbmcaddon')
	xbmcaddon.Addon = Addon
	source_search = types.ModuleType('modules.source_search')
	source_search.EXTERNAL_PROVIDERS = ()
	stubs = {
		'xbmc': xbmc, 'xbmcgui': xbmcgui, 'xbmcplugin': xbmcplugin, 'xbmcvfs': xbmcvfs, 'xbmcaddon': xbmcaddon,
		'modules.source_search': source_search
	}
	path = ROOT / 'resources' / 'lib' / 'modules' / 'kodi_utils.py'
	return load_module('test_settings_cleanup_kodi_utils', path, stubs)


class SettingsCleanupTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.kodi_utils = load_kodi_utils()

	def setUp(self):
		self.temp_dir = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp_dir.cleanup)
		self.profile = Path(self.temp_dir.name)
		self.settings_file = self.profile / 'settings.xml'
		self.open_modes = []
		self.kodi_utils.profile_path = '%s/' % self.profile
		self.kodi_utils.path_exists = lambda path: Path(path).exists()
		self.kodi_utils.make_directorys = lambda path: Path(path).mkdir(parents=True, exist_ok=True)
		def open_file(path, mode='r'):
			self.open_modes.append(mode)
			return open(path, mode, encoding='utf-8')
		self.kodi_utils.open_file = open_file
		self.kodi_utils.FIXED_SETTINGS = {'fixed.setting': 'default'}
		self.kodi_utils.make_settings_dict = Mock()
		self.kodi_utils.notification = Mock()

	def test_unchanged_file_is_not_rewritten(self):
		contents = '<settings><setting id="persisted.setting">value</setting></settings>'
		self.settings_file.write_text(contents, encoding='utf-8')

		self.kodi_utils.clean_settings(silent=True)

		self.assertEqual(self.settings_file.read_text(encoding='utf-8'), contents)
		self.assertNotIn('w', self.open_modes)
		self.kodi_utils.make_settings_dict.assert_called_once_with()

	def test_missing_file_is_created_and_refreshed(self):
		self.kodi_utils.clean_settings(silent=True)

		self.assertEqual(self.settings_file.read_text(encoding='utf-8'), '<settings version="2" />')
		self.assertIn('w', self.open_modes)
		self.kodi_utils.make_settings_dict.assert_called_once_with()

	def test_fixed_setting_is_removed_and_file_is_rewritten(self):
		self.settings_file.write_text(
			'<settings><setting id="fixed.setting">custom</setting><setting id="persisted.setting">value</setting></settings>', encoding='utf-8'
		)

		self.kodi_utils.clean_settings(silent=True)

		contents = self.settings_file.read_text(encoding='utf-8')
		self.assertNotIn('fixed.setting', contents)
		self.assertIn('persisted.setting', contents)
		self.assertIn('w', self.open_modes)
		self.kodi_utils.make_settings_dict.assert_called_once_with()


if __name__ == '__main__':
	unittest.main()
