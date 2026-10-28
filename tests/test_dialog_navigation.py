import types
import unittest
from pathlib import Path

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_dialogs():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	for name in (
		'local_string', 'build_url', 'media_path', 'select_dialog', 'show_busy_dialog', 'hide_busy_dialog', 'notification', 'ok_dialog', 'get_property', 'set_property',
		'clear_property', 'container_refresh', 'execute_builtin', 'confirm_dialog', 'container_content', 'sleep', 'current_window_id'
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
	mylist = types.ModuleType('modules.mylist')
	mylist.context_item = lambda *args, **kwargs: None
	mylist.refresh_info_state = lambda *args, **kwargs: None
	mylist.refresh_after_info = lambda: None
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils, 'modules.settings': settings, 'modules.cache': cache, 'modules.utils': utils, 'modules.mylist': mylist}
	path = ROOT / 'resources' / 'lib' / 'modules' / 'dialogs.py'
	return load_module('test_dialog_navigation_dialogs', path, stubs)


if __name__ == '__main__':
	unittest.main()
