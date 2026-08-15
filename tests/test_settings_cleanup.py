import json
import sqlite3
import tempfile
import threading
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
	def __init__(self):
		self.properties = {}

	def getProperty(self, key):
		return self.properties.get(key, '')

	def setProperty(self, key, value):
		self.properties[key] = value

	def clearProperty(self, key):
		self.properties.pop(key, None)


def load_kodi_utils(module_name='test_settings_cleanup_kodi_utils', window=None):
	window = window or Window()
	xbmc = types.ModuleType('xbmc')
	xbmc.Player = xbmc.Monitor = object
	xbmc.executeJSONRPC = xbmc.getInfoLabel = lambda *args: ''
	xbmc.getCondVisibility = lambda *args: False
	xbmc.sleep = xbmc.executebuiltin = xbmc.log = lambda *args: None
	xbmcgui = types.ModuleType('xbmcgui')
	xbmcgui.Window = lambda *args: window
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
	return load_module(module_name, path, stubs)


class SettingsCleanupTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.kodi_utils = load_kodi_utils()
		cls.original_make_settings_dict = staticmethod(cls.kodi_utils.make_settings_dict)

	def configure_module(self, module):
		module.profile_path = '%s/' % self.profile
		module.persisted_settings_db = str(self.persisted_settings_db)
		module.path_exists = lambda path: Path(path).exists()
		module.make_directorys = lambda path: Path(path).mkdir(parents=True, exist_ok=True)
		module.open_file = self.open_file
		module.FIXED_SETTINGS = {'fixed.setting': 'default'}
		module.notification = Mock()
		return module

	def setUp(self):
		self.temp_dir = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp_dir.cleanup)
		self.profile = Path(self.temp_dir.name)
		self.settings_file = self.profile / 'settings.xml'
		self.persisted_settings_db = self.profile / 'persisted_settings.db'
		self.open_modes = []
		def open_file(path, mode='r'):
			self.open_modes.append(mode)
			return open(path, mode, encoding='utf-8')
		self.open_file = open_file
		self.configure_module(self.kodi_utils)
		self.kodi_utils.window.properties.clear()
		self.kodi_utils.make_settings_dict = Mock(wraps=self.original_make_settings_dict)

	def database_settings(self):
		with sqlite3.connect(self.persisted_settings_db) as dbcon:
			return dict(dbcon.execute('SELECT id, value FROM settings'))

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

	def test_all_debrid_credentials_are_persisted(self):
		self.assertIn('ad.account_id', self.kodi_utils.PERSISTED_SETTING_IDS)
		self.assertIn('ad.token', self.kodi_utils.PERSISTED_SETTING_IDS)

		self.assertTrue(self.kodi_utils.set_setting('ad.token', 'test-token'))

		self.assertEqual(self.database_settings()['ad.token'], 'test-token')
		self.assertFalse(self.settings_file.exists())

	def test_torbox_credentials_are_persisted(self):
		self.assertIn('tb.account_id', self.kodi_utils.PERSISTED_SETTING_IDS)
		self.assertIn('tb.token', self.kodi_utils.PERSISTED_SETTING_IDS)

		self.assertTrue(self.kodi_utils.set_setting('tb.token', 'test-token'))

		self.assertEqual(self.database_settings()['tb.token'], 'test-token')
		self.assertFalse(self.settings_file.exists())

	def test_debrid_credentials_survive_late_skin_save_and_restart(self):
		stale_skin_settings = '<settings><setting id="skin.setting" type="string">old</setting></settings>'
		self.settings_file.write_text(stale_skin_settings, encoding='utf-8')
		credentials = {'ad.account_id': 'ad-user', 'ad.token': 'ad-token', 'tb.account_id': 'tb-user', 'tb.token': 'tb-token'}

		self.assertTrue(self.kodi_utils.set_settings(credentials))
		self.settings_file.write_text(stale_skin_settings.replace('old', 'new'), encoding='utf-8')
		self.kodi_utils.clear_property('pov_lite_settings')
		self.kodi_utils.make_settings_dict()
		fresh = self.configure_module(load_kodi_utils('test_settings_cleanup_restart', self.kodi_utils.window))

		self.assertEqual({setting_id: fresh.get_setting(setting_id) for setting_id in credentials}, credentials)
		self.assertEqual(self.database_settings(), credentials)
		self.assertIn('>new<', self.settings_file.read_text(encoding='utf-8'))

	def test_empty_database_value_blocks_stale_xml_credential(self):
		self.assertTrue(self.kodi_utils.set_setting('ad.token', ''))
		self.settings_file.write_text('<settings><setting id="ad.token">stale-token</setting></settings>', encoding='utf-8')

		self.kodi_utils.make_settings_dict()

		self.assertEqual(self.kodi_utils.get_setting('ad.token'), '')
		self.assertEqual(self.database_settings()['ad.token'], '')

	def test_legacy_credentials_migrate_once(self):
		self.settings_file.write_text('<settings><setting id="ad.token">legacy-token</setting></settings>', encoding='utf-8')

		self.kodi_utils.make_settings_dict()
		self.settings_file.write_text('<settings><setting id="ad.token">stale-token</setting></settings>', encoding='utf-8')
		self.kodi_utils.make_settings_dict()

		self.assertEqual(self.database_settings()['ad.token'], 'legacy-token')

	def test_concurrent_debrid_authorizations_preserve_both_bundles(self):
		first = self.configure_module(load_kodi_utils('test_settings_cleanup_first', self.kodi_utils.window))
		second = self.configure_module(load_kodi_utils('test_settings_cleanup_second', self.kodi_utils.window))
		barrier = threading.Barrier(2)
		results = []
		def write(module, settings):
			barrier.wait()
			results.append(module.set_settings(settings))
		threads = (
			threading.Thread(target=write, args=(first, {'ad.account_id': 'ad-user', 'ad.token': 'ad-token'})),
			threading.Thread(target=write, args=(second, {'tb.account_id': 'tb-user', 'tb.token': 'tb-token'}))
		)

		for thread in threads: thread.start()
		for thread in threads: thread.join()

		expected = {'ad.account_id': 'ad-user', 'ad.token': 'ad-token', 'tb.account_id': 'tb-user', 'tb.token': 'tb-token'}
		fresh = self.configure_module(load_kodi_utils('test_settings_cleanup_concurrent_restart', self.kodi_utils.window))
		self.assertEqual(results, [True, True])
		self.assertEqual(self.database_settings(), expected)
		self.assertEqual({setting_id: fresh.get_setting(setting_id) for setting_id in expected}, expected)
		self.assertEqual(json.loads(self.kodi_utils.window.getProperty('pov_lite_settings')), expected)


if __name__ == '__main__':
	unittest.main()
