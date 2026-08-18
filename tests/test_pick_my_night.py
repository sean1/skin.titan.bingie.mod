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

	def test_direct_movie_and_tv_results_skip_media_selection_and_wait_for_dialog_to_close(self):
		for mediatype, mode, action in (
			('movie', 'build_movie_list', 'tmdb_movies_discover'),
			('tvshow', 'build_tvshow_list', 'tmdb_tv_discover')
		):
			with self.subTest(mediatype=mediatype):
				menu = self.discover.Discover({'mediatype': mediatype})
				menu._selection_dialog = Mock(side_effect=('', '', 'crowd_pleasers'))

				result = menu.pick_my_night()

				commands = self.discover.kodi_utils.execute_builtin.call_args_list
				self.assertEqual(commands[0], call('CancelAlarm(BingiePickMyNight,silent)'))
				alarm = commands[1].args[0]
				self.assertIn('mode=%s' % mode, alarm)
				self.assertIn('action=%s' % action, alarm)
				self.assertTrue(alarm.endswith(',00:00:01,silent)'))
				self.assertEqual(result, alarm)
				self.assertEqual(menu._selection_dialog.call_count, 3)
				self.discover.kodi_utils.execute_builtin.reset_mock()

	def test_legacy_entry_still_asks_for_media_type(self):
		menu = self.discover.Discover({})
		menu._selection_dialog = Mock(side_effect=('movie', '', '', 'crowd_pleasers'))

		menu.pick_my_night()

		self.assertEqual(menu._selection_dialog.call_count, 4)

	def test_results_are_not_limited_to_ten_pages(self):
		menu = self.discover.Discover({'mediatype': 'movie'})
		menu._selection_dialog = Mock(side_effect=('', '', 'crowd_pleasers'))

		menu.pick_my_night()

		alarm = unquote(self.discover.kodi_utils.execute_builtin.call_args_list[1].args[0])
		self.assertIn('&target_results=200', alarm)
		self.assertNotIn('&max_pages=', alarm)

	def test_cancelled_selection_does_not_schedule_navigation(self):
		menu = self.discover.Discover({})
		menu._selection_dialog = Mock(return_value=None)

		self.assertIsNone(menu.pick_my_night())
		self.discover.kodi_utils.execute_builtin.assert_not_called()

	def test_movies_and_tv_offer_only_keyword_driven_moods(self):
		expected = [
			'', 'time_bending', 'apocalypse', 'survival', 'zombie', 'space_frontiers', 'artificial_intelligence_virtual_reality',
			'ancient_rome_egypt', 'pirate', 'treasure_hunt_quest', 'haunted', 'creature_features', 'vampire_werewolf',
			'killers_slashers', 'psychological_thriller', 'heist_bank_robbery', 'spy_espionage', 'organized_crime',
			'conspiracy', 'dark_futures', 'mysteries', 'soldiers_special_forces', 'dark_comedy', 'workplace_comedy_romance'
		]
		for mediatype in ('movie', 'tvshow'):
			with self.subTest(mediatype=mediatype):
				menu = self.discover.Discover({'mediatype': mediatype})
				menu._selection_dialog = Mock(return_value=None)

				menu.pick_my_night()

				self.assertEqual(menu._selection_dialog.call_args.args[1], expected)

	def test_every_specific_mood_uses_the_same_tmdb_keywords_for_movies_and_tv(self):
		moods = {
			'time_bending': '4379|10854',
			'apocalypse': '4458|10150|12332|355070|298669',
			'survival': '10349',
			'zombie': '12377|186565',
			'space_frontiers': '191132|3801|252937|1612',
			'artificial_intelligence_virtual_reality': '310|4563',
			'ancient_rome_egypt': '5049|157894',
			'pirate': '12988|185200',
			'treasure_hunt_quest': '6956|207372',
			'haunted': '162846|3358',
			'creature_features': '1299|11100|14909',
			'vampire_werewolf': '3133|12564',
			'killers_slashers': '12339|10714',
			'psychological_thriller': '12565|184312|226106',
			'heist_bank_robbery': '10051|15363',
			'spy_espionage': '470|5265|4289',
			'organized_crime': '10291|10391|3149',
			'conspiracy': '10410',
			'dark_futures': '4565|12190',
			'mysteries': '12570',
			'soldiers_special_forces': '13065|162365|6092|15218',
			'dark_comedy': '10123|8201|9755',
			'workplace_comedy_romance': '210605|212796|182325'
		}
		for mediatype in ('movie', 'tvshow'):
			for mood, keywords in moods.items():
				with self.subTest(mediatype=mediatype, mood=mood):
					menu = self.discover.Discover({'mediatype': mediatype})
					menu._selection_dialog = Mock(side_effect=(mood, '', 'crowd_pleasers'))

					menu.pick_my_night()

					alarm = unquote(self.discover.kodi_utils.execute_builtin.call_args_list[1].args[0])
					self.assertIn('&with_keywords=%s' % keywords, alarm)
					self.assertNotIn('&with_genres=', alarm)
					self.discover.kodi_utils.execute_builtin.reset_mock()

	def test_sparse_moods_use_relaxed_result_thresholds(self):
		for mood in ('zombie', 'ancient_rome_egypt', 'pirate', 'treasure_hunt_quest', 'workplace_comedy_romance'):
			with self.subTest(mood=mood):
				menu = self.discover.Discover({'mediatype': 'tvshow'})
				menu._selection_dialog = Mock(side_effect=(mood, '', 'critically_loved'))

				menu.pick_my_night()

				alarm = unquote(self.discover.kodi_utils.execute_builtin.call_args_list[1].args[0])
				self.assertIn('&sort_by=vote_count.desc&vote_count.gte=1&vote_average.gte=7', alarm)
				self.discover.kodi_utils.execute_builtin.reset_mock()

	def test_artificial_intelligence_mood_keeps_selected_style_thresholds(self):
		menu = self.discover.Discover({'mediatype': 'tvshow'})
		menu._selection_dialog = Mock(side_effect=('artificial_intelligence_virtual_reality', '', 'critically_loved'))

		menu.pick_my_night()

		alarm = unquote(self.discover.kodi_utils.execute_builtin.call_args_list[1].args[0])
		self.assertIn('&sort_by=vote_average.desc&vote_count.gte=500&vote_average.gte=7', alarm)


if __name__ == '__main__':
	unittest.main()
