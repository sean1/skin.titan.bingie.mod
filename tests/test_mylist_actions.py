import sqlite3
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from tests.module_isolation import load_module, temporary_modules
from tests.test_mylist_cache import LIB, ROOT, load_mylist_cache


def load_mylist_controller(database_path):
	cache, stubs = load_mylist_cache(database_path)
	kodi_utils = stubs['modules.kodi_utils']
	properties = {}
	kodi_utils.get_property = lambda key: properties.get(key, '')
	kodi_utils.set_property = Mock(side_effect=properties.__setitem__)
	kodi_utils.clear_property = Mock(side_effect=lambda key: properties.pop(key, None))
	kodi_utils.notification = Mock()
	kodi_utils.container_refresh = Mock()
	kodi_utils.widget_refresh = Mock()
	kodi_utils.get_visibility = lambda condition: condition == 'Window.IsActive(Videos)'
	kodi_utils.external_browse = lambda: False
	kodi_utils.build_url = lambda params: 'plugin://skin.titan.bingie.lite/?%s' % urlencode(params)
	caches = types.ModuleType('caches')
	caches.__path__ = []
	stubs.update({'caches': caches, 'caches.mylist_cache': cache})
	controller = load_module('test_mylist_controller_module', LIB / 'modules' / 'mylist.py', stubs)
	return controller, cache, kodi_utils, properties, stubs


def command_params(command):
	if not command.startswith('RunPlugin(') or not command.endswith(')'): raise AssertionError('Expected a RunPlugin command')
	url = command[len('RunPlugin('):-1]
	if urlsplit(url).netloc != 'skin.titan.bingie.lite': raise AssertionError('Unexpected add-on route')
	return {key: values[0] for key, values in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}


class MyListActionTests(unittest.TestCase):
	def setUp(self):
		self.temp_dir = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp_dir.cleanup)
		self.database_path = Path(self.temp_dir.name) / 'watched.db'
		self.controller, self.cache, self.kodi_utils, self.properties, self.stubs = load_mylist_controller(self.database_path)
		self.store = self.cache.MyList(str(self.database_path))

	def assert_no_success_refresh(self):
		self.kodi_utils.set_property.assert_not_called()
		self.kodi_utils.container_refresh.assert_not_called()
		self.kodi_utils.widget_refresh.assert_not_called()
		self.assertNotIn('BingieMyListRefresh', self.properties)


	def test_episode_context_saves_the_series_and_series_title(self):
		label, command = self.controller.context_item('episode', 999, 'Series', tvshow_id=202)
		params = command_params(command)
		self.assertEqual(label, 'Add to My List')
		self.assertEqual((params['mediatype'], params['tmdb_id'], params['title']), ('tvshow', '202', 'Series'))
		self.assertNotIn('season', params)
		self.assertNotIn('episode', params)
		self.assertTrue(self.controller.action(params))
		self.assertEqual([(item['media_id'], item['title']) for item in self.store.items('tvshow')[0]], [('202', 'Series')])

	def test_mutating_another_title_does_not_change_the_current_info_button(self):
		self.properties.update({'PovInfoType': 'tvshow', 'PovInfoTmdb': '101', 'PovInfoMyListSaved': 'false'})
		self.assertTrue(self.controller.action({'action': 'add', 'mediatype': 'movie', 'tmdb_id': 101, 'title': 'Movie'}))
		self.assertEqual(self.properties['PovInfoMyListSaved'], 'false')
		self.assertIn('BingieMyListRefresh', self.properties)

	def test_info_action_uses_the_displayed_series_identity(self):
		self.properties.update({'PovInfoType': 'tvshow', 'PovInfoTmdb': '202', 'PovInfoTitle': 'Series', 'PovInfoMyListSaved': 'false'})
		self.assertTrue(self.controller.from_info({'action': 'add'}))
		self.assertTrue(self.store.contains('tvshow', 202))
		self.assertFalse(self.store.contains('movie', 202))
		self.assertEqual(self.store.items('tvshow')[0][0]['title'], 'Series')
		self.assertEqual(self.properties['PovInfoMyListSaved'], 'true')
		self.assertTrue(self.controller.from_info({'action': 'remove'}))
		self.assertFalse(self.store.contains('tvshow', 202))
		self.assertEqual(self.properties['PovInfoMyListSaved'], 'false')

	def test_replaying_a_captured_info_add_keeps_the_movie_saved(self):
		self.properties.update({'PovInfoType': 'movie', 'PovInfoTmdb': '101', 'PovInfoTitle': 'Movie', 'PovInfoMyListSaved': 'false'})
		self.controller.refresh_info_state('movie', 101, 'Movie')
		params = command_params(self.properties['PovInfoMyListCommand'])
		self.assertEqual((params['mode'], params['action'], params['mediatype'], params['tmdb_id']), ('my_list_from_info', 'add', 'movie', '101'))
		self.assertTrue(self.controller.from_info(params))
		before = self.store.items('movie')
		self.assertTrue(self.controller.from_info(params))
		self.assertTrue(self.store.contains('movie', 101))
		self.assertEqual(self.store.items('movie'), before)
		self.assertEqual(self.properties['PovInfoMyListSaved'], 'true')

	def test_captured_info_identity_is_preserved_after_navigation_to_another_title(self):
		self.properties.update({'PovInfoType': 'tvshow', 'PovInfoTmdb': '202', 'PovInfoTitle': 'Current Show', 'PovInfoMyListSaved': 'false'})
		self.assertTrue(self.controller.from_info({'action': 'add', 'mediatype': 'movie', 'tmdb_id': '101', 'title': 'Clicked Movie'}))
		self.assertTrue(self.store.contains('movie', 101))
		self.assertFalse(self.store.contains('tvshow', 202))
		self.assertEqual(self.properties['PovInfoMyListSaved'], 'false')


	def test_failed_database_write_leaves_info_state_and_refresh_unchanged(self):
		self.properties.update({'PovInfoType': 'movie', 'PovInfoTmdb': '101', 'PovInfoMyListSaved': 'false'})
		with patch.object(self.kodi_utils, 'database_connect', side_effect=sqlite3.OperationalError('unavailable')):
			self.assertFalse(self.controller.action({'action': 'add', 'mediatype': 'movie', 'tmdb_id': 101, 'title': 'Movie'}))
		self.assertEqual(self.properties['PovInfoMyListSaved'], 'false')
		self.assert_no_success_refresh()
		self.kodi_utils.notification.assert_called_once_with('Could not update My List')


	def test_refreshing_unknown_info_state_clears_a_stale_saved_command(self):
		self.properties.update({'PovInfoMyListSaved': 'true', 'PovInfoMyListCommand': 'RunPlugin(stale-action)'})
		with patch.object(self.kodi_utils, 'database_connect', side_effect=sqlite3.OperationalError('unavailable')):
			self.controller.refresh_info_state('movie', 101, 'Movie')
		self.assertEqual(self.properties['PovInfoMyListSaved'], '')
		self.assertEqual(self.properties['PovInfoMyListCommand'], '')
		self.kodi_utils.container_refresh.assert_not_called()
		self.kodi_utils.notification.assert_not_called()



	def test_unavailable_metadata_keeps_saved_movies_and_shows_removable(self):
		from tests.test_menu_completion import load_menu_module
		from tests.test_menu_media import load_media_module
		for mediatype in ('movie', 'tvshow'):
			for missing_result in ('blank', 'error'):
				with self.subTest(mediatype=mediatype, metadata=missing_result):
					self.store.add(mediatype, 101, 'Saved Title')
					with temporary_modules({**self.stubs, 'modules.mylist': self.controller}):
						module, _complete_directory, _kodi_utils = load_menu_module(mediatype)
						media = load_media_module()
						listitem = Mock()
						media.kodi_utils.make_listitem.return_value = listitem
						module.build_tmdb_detail_shelf_item = media.build_tmdb_detail_shelf_item
						menu = object.__new__(module.Menu)
						menu.params, menu.items, menu.saved_titles = {}, [], {'101': 'Saved Title'}
						menu.append = menu.items.append
						menu.id_type, menu.meta_user_info, menu.current_date = 'tmdb_id', {}, None
						provider = module.movie_meta if mediatype == 'movie' else module.tvshow_meta
						if missing_result == 'blank': provider.return_value = {'blank_entry': True, 'tmdb_id': 101}
						else: provider.side_effect = RuntimeError('metadata unavailable')
						if mediatype == 'movie': menu.build_movie_content(0, '101')
						else: menu.build_tvshow_content(0, '101')
					self.assertEqual(len(menu.items), 1)
					listitem.setLabel.assert_called_once_with('Saved Title')
					label, command = listitem.addContextMenuItems.call_args.args[0][0]
					self.assertEqual(label, 'Remove from My List')
					params = command_params(command)
					self.assertEqual((params['action'], params['mediatype'], params['tmdb_id']), ('remove', mediatype, '101'))
					self.assertTrue(self.controller.action(params))
					self.assertFalse(self.store.contains(mediatype, 101))


if __name__ == '__main__':
	unittest.main()
