import sys
import types
import unittest
from pathlib import Path

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_entry():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.logger = lambda *args: None
	kodi_utils.path_exists = lambda *args: False
	kodi_utils.translate_path = lambda path: path
	kodi_utils.monitor = object()
	kodi_utils.get_property = lambda *args: ''
	kodi_utils.set_property = lambda *args: None
	kodi_utils.clear_property = lambda *args: None
	kodi_utils.get_setting = lambda *args: ''
	kodi_utils.set_setting = lambda *args: None
	kodi_utils.xbmc_monitor = object
	kodi_utils.xbmc_player = object
	settings = types.ModuleType('modules.settings')
	prefetch = types.ModuleType('modules.prefetch')
	prefetch.NextPagePrefetch = object
	stubs = {'modules.kodi_utils': kodi_utils, 'modules.settings': settings, 'modules.prefetch': prefetch}
	sys.path.insert(0, str(ROOT / 'resources' / 'lib'))
	try:
		path = ROOT / 'resources' / 'lib' / 'entry.py'
		return load_module('test_focused_fanart_entry', path, stubs)
	finally: sys.path.pop(0)


if __name__ == '__main__':
	unittest.main()
