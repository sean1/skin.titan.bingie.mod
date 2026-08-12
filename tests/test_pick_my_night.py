import types
import unittest
from pathlib import Path
from urllib.parse import unquote, urlencode
from unittest.mock import Mock, call

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_discover_module():
	tmdb_api = types.ModuleType('indexers.tmdb_api')
	tmdb_api.base_url = 'https://api.themoviedb.org/3'
	indexers = types.ModuleType('indexers')
	indexers.tmdb_api = tmdb_api
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.local_string = lambda value: str(value)
	kodi_utils.build_url = lambda params: 'plugin://skin.titan.bingie.lite/?%s' % urlencode(params)
	kodi_utils.make_listitem = lambda: None
	kodi_utils.get_addoninfo = lambda key: key
	kodi_utils.media_path = lambda path: path
	kodi_utils.get_property = lambda key: ''
	meta_lists = types.ModuleType('modules.meta_lists')
	utils = types.ModuleType('modules.utils')
	utils.safe_string = lambda value: value
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	modules.meta_lists = meta_lists
	stubs = {
		'indexers': indexers, 'indexers.tmdb_api': tmdb_api, 'modules': modules,
		'modules.kodi_utils': kodi_utils, 'modules.meta_lists': meta_lists, 'modules.utils': utils
	}
	path = ROOT / 'resources' / 'lib' / 'menus' / 'discover.py'
	return load_module('test_pick_my_night_discover', path, stubs)


class PickMyNightTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.discover = load_discover_module()

	def setUp(self):
		self.discover.kodi_utils.execute_builtin = Mock(side_effect=lambda command: command)

	def test_movie_and_tv_results_wait_for_dialog_to_close_after_cancel(self):
		for mediatype, mode, action in (
			('movie', 'build_movie_list', 'tmdb_movies_discover'),
			('tvshow', 'build_tvshow_list', 'tmdb_tv_discover')
		):
			with self.subTest(mediatype=mediatype):
				menu = self.discover.Discover({})
				menu._selection_dialog = Mock(side_effect=(mediatype, '', '', 'crowd_pleasers'))

				result = menu.pick_my_night()

				commands = self.discover.kodi_utils.execute_builtin.call_args_list
				self.assertEqual(commands[0], call('CancelAlarm(BingiePickMyNight,silent)'))
				alarm = commands[1].args[0]
				self.assertIn('mode=%s' % mode, alarm)
				self.assertIn('action=%s' % action, alarm)
				self.assertTrue(alarm.endswith(',00:00:01,silent)'))
				self.assertEqual(result, alarm)
				self.discover.kodi_utils.execute_builtin.reset_mock()

	def test_cancelled_selection_does_not_schedule_navigation(self):
		menu = self.discover.Discover({})
		menu._selection_dialog = Mock(return_value=None)

		self.assertIsNone(menu.pick_my_night())
		self.discover.kodi_utils.execute_builtin.assert_not_called()

	def test_horror_slasher_mood_uses_movie_genre_and_tv_keywords(self):
		for mediatype, expected_filter in (
			('movie', '&with_genres=27'),
			('tvshow', '&with_keywords=315058|12377|162846|1299|11100|12339')
		):
			with self.subTest(mediatype=mediatype):
				menu = self.discover.Discover({})
				menu._selection_dialog = Mock(side_effect=(mediatype, 'horror_supernatural', '', 'crowd_pleasers'))

				menu.pick_my_night()

				alarm = unquote(self.discover.kodi_utils.execute_builtin.call_args_list[1].args[0])
				self.assertIn(expected_filter, alarm)
				self.discover.kodi_utils.execute_builtin.reset_mock()


if __name__ == '__main__':
	unittest.main()
