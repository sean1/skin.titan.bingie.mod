import json
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


class ListItem:
	def __init__(self):
		self.label = ''
		self.art = {}

	def setLabel(self, label):
		self.label = label

	def setArt(self, art):
		self.art = art


def load_navigator():
	navigator_cache = types.ModuleType('caches.navigator_cache')
	navigator_cache.navigator_cache = Mock()
	caches = types.ModuleType('caches')
	caches.navigator_cache = navigator_cache
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.local_string = str
	kodi_utils.build_url = Mock()
	kodi_utils.make_listitem = ListItem
	kodi_utils.media_path = lambda path='': path
	kodi_utils.add_item = Mock()
	kodi_utils.add_items = Mock()
	kodi_utils.add_dir = Mock()
	kodi_utils.set_category = Mock()
	kodi_utils.set_content = Mock()
	kodi_utils.end_directory = Mock()
	kodi_utils.execJSONRPC = Mock()
	settings = types.ModuleType('modules.settings')
	settings.addon_fanart = lambda: 'fanart.jpg'
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	modules.settings = settings
	stubs = {'caches': caches, 'caches.navigator_cache': navigator_cache, 'modules': modules, 'modules.kodi_utils': kodi_utils, 'modules.settings': settings}
	return load_module('test_video_sources_navigator', ROOT / 'resources' / 'lib' / 'menus' / 'navigator.py', stubs), kodi_utils


class VideoSourceTests(unittest.TestCase):
	def setUp(self):
		self.navigator, self.ku = load_navigator()
		self.menu = object.__new__(self.navigator.Navigator)
		values = {'handle': 7, 'fanart': 'fanart.jpg'}
		self.menu.params_get = lambda key, default=None: values.get(key, default)

	def test_lists_personal_video_sources_and_manage_entry(self):
		self.menu.params_get = lambda key, default=None: {'handle': 7, 'fanart': 'fanart.jpg', 'group': 'myvideos'}.get(key, default)
		self.ku.execJSONRPC.return_value = json.dumps({'result': {'sources': [
			{'label': 'Vids', 'file': 'smb://server/Vids/'},
			{'label': 'Video add-ons', 'file': 'addons://sources/video/'},
			{'label': 'Family', 'file': 'nfs://server/Family/'},
		]}})

		self.menu.video_sources()

		request = json.loads(self.ku.execJSONRPC.call_args.args[0])
		self.assertEqual(request['method'], 'Files.GetSources')
		self.assertEqual(request['params'], {'media': 'video'})
		self.assertEqual([(call.args[1], call.args[2].label) for call in self.ku.add_item.call_args_list], [
			('smb://server/Vids/', 'Vids'), ('nfs://server/Family/', 'Family'), ('sources://video/', 'Manage Sources...')
		])
		self.ku.set_category.assert_called_once_with(7, 'My Videos')
		self.ku.set_content.assert_called_once_with(7, 'files')
		self.ku.end_directory.assert_called_once_with(7, cacheToDisc=False)

	def test_invalid_source_response_still_offers_management(self):
		self.menu.params_get = lambda key, default=None: {'handle': 7, 'fanart': 'fanart.jpg', 'group': 'myvideos'}.get(key, default)
		self.ku.execJSONRPC.return_value = 'not json'

		self.menu.video_sources()

		self.assertEqual([(call.args[1], call.args[2].label) for call in self.ku.add_item.call_args_list], [('sources://video/', 'Manage Sources...')])

	def test_other_menu_groups_return_no_video_sources(self):
		self.menu.params_get = lambda key, default=None: {'handle': 7, 'fanart': 'fanart.jpg', 'group': 'movies'}.get(key, default)

		self.menu.video_sources()

		self.ku.execJSONRPC.assert_not_called()
		self.ku.add_item.assert_not_called()
		self.ku.end_directory.assert_called_once_with(7, cacheToDisc=False)


if __name__ == '__main__':
	unittest.main()
