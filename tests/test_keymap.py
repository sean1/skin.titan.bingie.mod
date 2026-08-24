import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_keymap(files=None):
	files = {} if files is None else files
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.path_exists = Mock(side_effect=lambda path: path in files or path == 'special://profile/keymaps/')
	kodi_utils.make_directorys = Mock()
	kodi_utils.execute_builtin = Mock()

	class File:
		def __init__(self, path, mode='r'):
			self.path, self.mode = path, mode

		def read(self): return files[self.path]
		def write(self, contents): files[self.path] = contents
		def close(self): pass

	kodi_utils.open_file = Mock(side_effect=File)
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	return load_module('test_keymap_module', ROOT / 'resources' / 'lib' / 'modules' / 'keymap.py', {'modules': modules, 'modules.kodi_utils': kodi_utils}), kodi_utils, files


class KeymapTests(unittest.TestCase):
	def test_installs_custom_info_longpress_bindings_and_reloads_keymaps(self):
		keymap, kodi_utils, files = load_keymap()

		self.assertTrue(keymap.install())

		contents = files[keymap.KEYMAP_PATH]
		self.assertIn('<window1123>', contents)
		self.assertNotIn('<global>', contents)
		self.assertNotIn('<Home>', contents)
		self.assertNotIn('<Videos>', contents)
		self.assertIn('managed keymap v4', contents)
		self.assertEqual(contents.count('mod="longpress"'), 6)
		self.assertEqual(contents.count(keymap.SOURCE_SELECT_ACTION), 6)
		self.assertIn('<play_pause mod="longpress">', contents)
		self.assertIn('<p mod="longpress">', contents)
		self.assertIn('<return mod="longpress">', contents)
		self.assertIn('<enter mod="longpress">', contents)
		self.assertIn('<numpadenter mod="longpress">', contents)
		self.assertNotIn('<return>', contents)
		self.assertNotIn('<enter>', contents)
		self.assertNotIn('<numpadenter>', contents)
		self.assertIn('<play mod="longpress">', contents)
		kodi_utils.execute_builtin.assert_called_once_with('ReloadKeymaps')

	def test_matching_owned_keymap_is_unchanged(self):
		keymap, kodi_utils, files = load_keymap()
		files[keymap.KEYMAP_PATH] = keymap.KEYMAP_XML

		self.assertFalse(keymap.install())

		kodi_utils.open_file.assert_called_once_with(keymap.KEYMAP_PATH)
		kodi_utils.execute_builtin.assert_not_called()

	def test_replaces_only_the_bingie_owned_keymap(self):
		keymap, kodi_utils, files = load_keymap({'special://profile/keymaps/user.xml': '<keymap />'})
		files[keymap.KEYMAP_PATH] = '<keymap><!-- old BINGIE version --></keymap>'

		self.assertTrue(keymap.install())

		self.assertEqual(files['special://profile/keymaps/user.xml'], '<keymap />')
		self.assertEqual(files[keymap.KEYMAP_PATH], keymap.KEYMAP_XML)
		written_paths = [call.args[0] for call in kodi_utils.open_file.call_args_list if len(call.args) > 1 and call.args[1] == 'w']
		self.assertEqual(written_paths, [keymap.KEYMAP_PATH])


if __name__ == '__main__':
	unittest.main()
