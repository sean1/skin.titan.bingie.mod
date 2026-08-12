import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_menu_editor():
	navigator = Mock()
	navigator_module = types.ModuleType('caches.navigator_cache')
	navigator_module.navigator_cache = navigator
	caches = types.ModuleType('caches')
	caches.__path__ = []
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.local_string = str
	kodi_utils.media_path = lambda value='': value
	menu_lists = types.ModuleType('modules.menu_lists')
	menu_lists.default_menu_items = ('RootList', 'MovieList', 'TVShowList')
	menu_lists.main_menu_items = {}
	menu_lists.main_menus = {}
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	modules.menu_lists = menu_lists
	stubs = {
		'caches': caches, 'caches.navigator_cache': navigator_module, 'modules': modules,
		'modules.kodi_utils': kodi_utils, 'modules.menu_lists': menu_lists
	}
	path = ROOT / 'resources' / 'lib' / 'modules' / 'menu_editor.py'
	return load_module('test_menu_editor_timing_module', path, stubs), navigator


class MenuEditorTimingTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.menu_editor, cls.navigator = load_menu_editor()

	def setUp(self):
		self.events = []
		self.navigator.reset_mock()
		self.navigator.set_list = Mock(side_effect=lambda *args: self.events.append(('set', args)))
		self.menu_editor.kodi_utils.notification = Mock(side_effect=lambda *args: self.events.append(('notification', args)))
		self.menu_editor.kodi_utils.container_refresh = Mock(side_effect=lambda: self.events.append(('refresh', ())))
		self.menu_editor.kodi_utils.sleep = Mock()
		self.editor = self.menu_editor.MenuEditor.__new__(self.menu_editor.MenuEditor)

	def test_database_update_notifies_then_refreshes_without_waiting(self):
		contents = [{'name': 'fixture'}]

		self.editor._db_execute('set', 'RootList', contents)

		self.assertEqual(self.events, [('set', ('RootList', 'edited', contents)), ('notification', (32576, 1500)), ('refresh', ())])
		self.menu_editor.kodi_utils.sleep.assert_not_called()

	def test_no_refresh_update_returns_without_waiting(self):
		self.editor._db_execute('make_new_folder', 'Fixture', [], list_type='shortcut_folder', refresh=False)

		self.assertEqual(self.events, [('set', ('Fixture', 'shortcut_folder', [])), ('notification', (32576, 1500))])
		self.menu_editor.kodi_utils.container_refresh.assert_not_called()
		self.menu_editor.kodi_utils.sleep.assert_not_called()


if __name__ == '__main__':
	unittest.main()
