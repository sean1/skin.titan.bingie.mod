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
	xbmcplugin.endOfDirectory = Mock()
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


class SettingsPersistenceTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.kodi_utils = load_kodi_utils()

	def configure_module(self, module):
		module.profile_path = '%s/' % self.profile
		module.persisted_settings_db = str(self.persisted_settings_db)
		module.make_directorys = lambda path: Path(path).mkdir(parents=True, exist_ok=True)
		module.FIXED_SETTINGS = {'fixed.setting': 'default'}
		return module

	def setUp(self):
		self.temp_dir = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp_dir.cleanup)
		self.profile = Path(self.temp_dir.name)
		self.persisted_settings_db = self.profile / 'persisted_settings.db'
		self.configure_module(self.kodi_utils)
		self.kodi_utils.xbmcplugin.endOfDirectory.reset_mock()

	def database_settings(self):
		with sqlite3.connect(self.persisted_settings_db) as dbcon:
			return dict(dbcon.execute('SELECT id, value FROM settings'))

	def test_fixed_and_fallback_settings_are_read_without_window_cache(self):
		self.assertEqual(self.kodi_utils.get_setting('fixed.setting'), 'default')
		self.assertEqual(self.kodi_utils.get_setting('unknown.setting', 'fallback'), 'fallback')

	def test_end_directory_caches_to_disk_by_default(self):
		self.kodi_utils.end_directory(7)

		self.kodi_utils.xbmcplugin.endOfDirectory.assert_called_once_with(7, cacheToDisc=True)

	def test_all_debrid_credentials_are_persisted(self):
		self.assertIn('ad.account_id', self.kodi_utils.PERSISTED_SETTING_IDS)
		self.assertIn('ad.token', self.kodi_utils.PERSISTED_SETTING_IDS)

		self.assertTrue(self.kodi_utils.set_setting('ad.token', 'test-token'))

		self.assertEqual(self.database_settings()['ad.token'], 'test-token')

	def test_torbox_credentials_are_persisted(self):
		self.assertIn('tb.account_id', self.kodi_utils.PERSISTED_SETTING_IDS)
		self.assertIn('tb.token', self.kodi_utils.PERSISTED_SETTING_IDS)

		self.assertTrue(self.kodi_utils.set_setting('tb.token', 'test-token'))

		self.assertEqual(self.database_settings()['tb.token'], 'test-token')

	def test_debrid_credentials_survive_restart(self):
		credentials = {'ad.account_id': 'ad-user', 'ad.token': 'ad-token', 'tb.account_id': 'tb-user', 'tb.token': 'tb-token'}

		self.assertTrue(self.kodi_utils.set_settings(credentials))
		fresh = self.configure_module(load_kodi_utils('test_settings_cleanup_restart', self.kodi_utils.window))

		self.assertEqual({setting_id: fresh.get_setting(setting_id) for setting_id in credentials}, credentials)
		self.assertEqual(self.database_settings(), credentials)

	def test_empty_database_value_round_trips(self):
		self.assertTrue(self.kodi_utils.set_setting('ad.token', ''))

		self.assertEqual(self.kodi_utils.get_setting('ad.token'), '')
		self.assertEqual(self.database_settings()['ad.token'], '')

	def test_retired_migration_settings_are_not_persisted(self):
		retired_ids = {'database.merge_status', 'migration.removed_services.6_08_03', 'migration.removed_personal_trakt.6_08_09', 'migration.removed_history.6_08_38', 'migration.tmdb_native_lists.2_03_03'}

		self.assertTrue(retired_ids.isdisjoint(self.kodi_utils.PERSISTED_SETTING_IDS))
		self.assertIn('database.maintenance.due', self.kodi_utils.PERSISTED_SETTING_IDS)

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


if __name__ == '__main__':
	unittest.main()
