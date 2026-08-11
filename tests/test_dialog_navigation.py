import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_dialogs():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	for name in (
		'local_string', 'build_url', 'media_path', 'select_dialog', 'show_busy_dialog', 'hide_busy_dialog', 'notification', 'ok_dialog', 'get_property', 'set_property',
		'clear_property', 'container_refresh', 'execute_builtin', 'confirm_dialog', 'container_content', 'sleep'
	): setattr(kodi_utils, name, lambda *args, **kwargs: None)
	settings = types.ModuleType('modules.settings')
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	modules.settings = settings
	cache = types.ModuleType('modules.cache')
	cache.clear_cache = lambda: None
	utils = types.ModuleType('modules.utils')
	utils.get_datetime = lambda: None
	utils.safe_string = str
	utils.valid_tmdb_id = lambda value: bool(value)
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils, 'modules.settings': settings, 'modules.cache': cache, 'modules.utils': utils}
	previous = {name: sys.modules.get(name) for name in stubs}
	sys.modules.update(stubs)
	try:
		path = ROOT / 'resources' / 'lib' / 'modules' / 'dialogs.py'
		spec = importlib.util.spec_from_file_location('test_dialog_navigation_dialogs', path)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
	finally:
		for name, old_module in previous.items():
			if old_module is None: sys.modules.pop(name, None)
			else: sys.modules[name] = old_module
	return module


class DialogNavigationTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.dialogs = load_dialogs()

	def setUp(self):
		self.properties = {}
		self.commands = []
		self.dialogs.get_property = lambda key: self.properties.get(key, '')
		self.dialogs.set_property = self.properties.__setitem__
		self.dialogs.clear_property = lambda key: self.properties.pop(key, None)
		self.dialogs.execute_builtin = self.commands.append
		self.dialogs.kodi_utils.get_visibility = lambda condition: condition == 'Window.IsActive(1123)'
		self.dialogs._stop_owned_trailer_preview = lambda *args, **kwargs: None

	def test_back_from_video_info_reopens_saved_actor_page(self):
		actor_values = {prop: '' for prop in self.dialogs.POV_ACTOR_PROPERTIES}
		actor_values.update({'PovActorId': '1245', 'PovActorName': 'Scarlett Johansson', 'PovActorReady': 'true'})
		self.properties[self.dialogs.POV_PAGE_HISTORY_PROPERTY] = json.dumps([{'page': 'actor', 'values': actor_values}])

		self.dialogs.pov_page_back()

		self.assertEqual(self.commands, ['ReplaceWindow(1122)'])
		self.assertEqual(self.properties['PovActorId'], '1245')
		self.assertEqual(self.properties['PovActorName'], 'Scarlett Johansson')
		self.assertNotIn(self.dialogs.POV_PAGE_HISTORY_PROPERTY, self.properties)

	def test_back_from_actor_reopens_saved_video_info_page(self):
		info_values = {prop: '' for prop in self.dialogs.POV_INFO_PROPERTIES}
		info_values.update({'PovInfoType': 'movie', 'PovInfoTmdb': '497698', 'PovInfoTitle': 'Black Widow'})
		self.properties[self.dialogs.POV_PAGE_HISTORY_PROPERTY] = json.dumps([{'page': 'info', 'values': info_values}])
		self.dialogs.kodi_utils.get_visibility = lambda condition: condition == 'Window.IsActive(1122)'

		self.dialogs.pov_page_back()

		self.assertEqual(self.commands, ['ReplaceWindow(1123)', 'SetFocus(80)'])
		self.assertEqual(self.properties['PovInfoTmdb'], '497698')
		self.assertEqual(self.properties['PovInfoTitle'], 'Black Widow')
		self.assertNotIn(self.dialogs.POV_PAGE_HISTORY_PROPERTY, self.properties)


if __name__ == '__main__':
	unittest.main()
