import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_keymap(files=None, skin='skin.titan.bingie.lite'):
	files = {} if files is None else files
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.path_exists = Mock(side_effect=lambda path: path in files or path == 'special://profile/keymaps/')
	kodi_utils.make_directorys = Mock()
	kodi_utils.execute_builtin = Mock()
	kodi_utils.delete_file = Mock(side_effect=lambda path: files.pop(path, None))
	kodi_utils.current_skin = Mock(return_value=skin)

	class File:
		def __init__(self, path, mode='r'): self.path, self.mode = path, mode
		def read(self): return files[self.path]
		def write(self, contents): files[self.path] = contents
		def close(self): pass

	kodi_utils.open_file = Mock(side_effect=File)
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	module = load_module('test_keymap_lifecycle_new_module', ROOT / 'resources' / 'lib' / 'modules' / 'keymap.py', {'modules': modules, 'modules.kodi_utils': kodi_utils})
	return module, kodi_utils, files


def load_focused(skin):
	modules = types.ModuleType('modules')
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.current_skin = Mock(return_value=skin)
	kodi_utils.get_visibility = Mock(return_value=False)
	kodi_utils.execute_builtin = Mock()
	modules.kodi_utils = kodi_utils
	module = load_module('test_keymap_lifecycle_focused_module', ROOT / 'resources' / 'lib' / 'modules' / 'focused_playback.py', {'modules': modules, 'modules.kodi_utils': kodi_utils})
	return module, kodi_utils


class KeymapLifecycleTests(unittest.TestCase):
	def test_direct_install_is_rejected_while_skin_is_inactive(self):
		keymap, kodi_utils, files = load_keymap(skin='skin.estuary')
		self.assertFalse(keymap.install())
		self.assertNotIn(keymap.KEYMAP_PATH, files)
		kodi_utils.open_file.assert_not_called()

	def test_keymap_is_scoped_to_custom_info_window(self):
		keymap, kodi_utils, files = load_keymap()
		self.assertTrue(keymap.Lifecycle().tick())
		self.assertIn('<window1123>', files[keymap.KEYMAP_PATH])
		self.assertNotIn('<global>', files[keymap.KEYMAP_PATH])
		kodi_utils.execute_builtin.assert_called_once_with('ReloadKeymaps')

	def test_unchanged_skin_state_does_not_touch_the_keymap_again(self):
		keymap, kodi_utils, files = load_keymap()
		now = [0.0]
		lifecycle = keymap.Lifecycle(clock=lambda: now[0])
		self.assertTrue(lifecycle.tick())
		kodi_utils.open_file.reset_mock()
		self.assertFalse(lifecycle.tick())
		kodi_utils.open_file.assert_not_called()

	def test_active_keymap_drift_is_repaired_only_after_reconcile_interval(self):
		keymap, kodi_utils, files = load_keymap()
		now = [0.0]
		lifecycle = keymap.Lifecycle(clock=lambda: now[0], reconcile_interval=60.0)
		self.assertTrue(lifecycle.tick())
		files[keymap.KEYMAP_PATH] = '<keymap><!-- corrupted --></keymap>'
		kodi_utils.open_file.reset_mock()

		now[0] = 59.9
		self.assertFalse(lifecycle.tick())
		kodi_utils.open_file.assert_not_called()
		now[0] = 60.0
		self.assertTrue(lifecycle.tick())
		self.assertEqual(files[keymap.KEYMAP_PATH], keymap.KEYMAP_XML)

	def test_inactive_keymap_reappearance_is_removed_after_reconcile_interval(self):
		keymap, kodi_utils, files = load_keymap()
		now = [0.0]
		lifecycle = keymap.Lifecycle(clock=lambda: now[0], reconcile_interval=60.0)
		lifecycle.tick()
		kodi_utils.current_skin.return_value = 'skin.estuary'
		self.assertTrue(lifecycle.tick())
		files[keymap.KEYMAP_PATH] = keymap.KEYMAP_XML

		now[0] = 59.9
		self.assertFalse(lifecycle.tick())
		self.assertIn(keymap.KEYMAP_PATH, files)
		now[0] = 60.0
		self.assertTrue(lifecycle.tick())
		self.assertNotIn(keymap.KEYMAP_PATH, files)

	def test_failed_install_is_retried_without_advancing_lifecycle_state(self):
		keymap, kodi_utils, files = load_keymap()
		original_open = kodi_utils.open_file.side_effect
		failed = True

		class FailedWrite:
			def write(self, contents): pass
			def close(self): pass

		def open_file(path, mode='r'):
			if mode == 'w' and failed: return FailedWrite()
			return original_open(path, mode)

		kodi_utils.open_file.side_effect = open_file
		lifecycle = keymap.Lifecycle()
		with self.assertRaises(IOError): lifecycle.tick()
		self.assertIsNone(lifecycle.active)
		failed = False
		self.assertTrue(lifecycle.tick())
		self.assertEqual(lifecycle.active, True)

	def test_failed_removal_is_retried_without_advancing_lifecycle_state(self):
		keymap, kodi_utils, files = load_keymap()
		lifecycle = keymap.Lifecycle()
		lifecycle.tick()
		kodi_utils.current_skin.return_value = 'skin.estuary'
		kodi_utils.delete_file.side_effect = lambda path: None
		with self.assertRaises(IOError): lifecycle.tick()
		self.assertEqual(lifecycle.active, True)
		kodi_utils.delete_file.side_effect = lambda path: files.pop(path, None)
		self.assertTrue(lifecycle.tick())
		self.assertEqual(lifecycle.active, False)

	def test_skin_transition_removes_only_owned_file(self):
		keymap, kodi_utils, files = load_keymap()
		lifecycle = keymap.Lifecycle()
		lifecycle.tick()
		kodi_utils.current_skin.return_value = 'skin.estuary'
		self.assertTrue(lifecycle.tick())
		self.assertNotIn(keymap.KEYMAP_PATH, files)

		files[keymap.KEYMAP_PATH] = '<keymap><!-- user file --></keymap>'
		lifecycle.active = None
		self.assertFalse(lifecycle.tick())
		self.assertIn(keymap.KEYMAP_PATH, files)

	def test_stale_global_action_restores_context_menu_in_inactive_skin(self):
		focused, kodi_utils = load_focused('skin.estuary')
		self.assertFalse(focused.source_select_focused())
		kodi_utils.get_visibility.assert_called_once_with(focused.PLAYBACK_WINDOW_VISIBILITY)
		kodi_utils.execute_builtin.assert_called_once_with('Action(ContextMenu)')

	def test_stale_global_action_never_restores_context_menu_during_playback(self):
		focused, kodi_utils = load_focused('skin.estuary')
		kodi_utils.get_visibility.side_effect = lambda condition: condition == focused.PLAYBACK_WINDOW_VISIBILITY
		self.assertFalse(focused.source_select_focused())
		kodi_utils.current_skin.assert_not_called()
		kodi_utils.execute_builtin.assert_not_called()


if __name__ == '__main__': unittest.main()
