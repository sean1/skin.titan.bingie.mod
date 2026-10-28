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


if __name__ == '__main__': unittest.main()
