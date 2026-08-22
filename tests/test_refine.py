import json
import types
import unittest
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse, urlencode
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_refine_module():
	tmdb_api = types.ModuleType('indexers.tmdb_api')
	tmdb_api.base_url = 'https://api.themoviedb.org/3'
	indexers = types.ModuleType('indexers')
	indexers.tmdb_api = tmdb_api
	properties = {}
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.get_property = lambda key: properties.get(key, '')
	kodi_utils.set_property = lambda key, value: properties.__setitem__(key, value)
	kodi_utils.get_infolabel = lambda key: 'movies'
	kodi_utils.media_path = lambda path: path
	kodi_utils.build_url = lambda params: 'plugin://skin.titan.bingie.lite/?%s' % urlencode(params)
	kodi_utils.execute_builtin = Mock(side_effect=lambda command: command)
	kodi_utils.select_dialog = Mock()
	kodi_utils.notification = Mock()
	kodi_utils.dialog = types.SimpleNamespace(numeric=Mock())
	meta_lists = types.ModuleType('modules.meta_lists')
	meta_lists.movie_genres = {'Action': ('28', 'genre_action.png'), 'Comedy': ('35', 'genre_comedy.png')}
	meta_lists.tvshow_genres = {'Comedy': ('35', 'genre_comedy.png'), 'Drama': ('18', 'genre_drama.png')}
	meta_lists.movie_certifications = ('G', 'PG', 'PG-13', 'R', 'NC-17', 'NR')
	meta_lists.meta_languages = {'English': {'iso': 'en'}, 'French': {'iso': 'fr'}}
	modules = types.ModuleType('modules')
	modules.kodi_utils, modules.meta_lists = kodi_utils, meta_lists
	stubs = {'indexers': indexers, 'indexers.tmdb_api': tmdb_api, 'modules': modules, 'modules.kodi_utils': kodi_utils, 'modules.meta_lists': meta_lists}
	module = load_module('test_refine_module', ROOT / 'resources/lib/menus/refine.py', stubs)
	module._properties = properties
	return module


class RefineTests(unittest.TestCase):
	def setUp(self):
		self.refine = load_refine_module()

	def test_initialize_publishes_defaults_and_listing_identity(self):
		values = self.refine.Refine({'mediatype': 'tvshow'}).initialize()

		self.assertEqual(values['Type'], 'tvshow')
		self.assertEqual(values['Eligible'], 'true')
		self.assertEqual(values['Count'], '0')
		self.assertEqual(values['Genres'], 'Any')

	def test_choices_are_staged_and_clear_restores_defaults(self):
		menu = self.refine.Refine({'mediatype': 'movie'})
		menu._select = Mock(return_value=('Rating', 'vote_average'))
		menu.sort()
		menu._select = Mock(return_value=('Ascending', 'asc'))
		menu.order()

		stored = json.loads(self.refine._properties['Bingie.Refine.Draft.movie'])
		self.assertEqual((stored['sort'], stored['order']), ('vote_average', 'asc'))
		self.assertEqual(self.refine._properties['Refine.Count'], '1')

		menu.clear()
		self.assertEqual(self.refine._properties['Refine.Count'], '0')
		self.assertEqual(self.refine._properties['Refine.Sort'], 'Popularity')

	def test_initialize_discards_unapplied_changes_and_restores_applied_values(self):
		menu = self.refine.Refine({'mediatype': 'movie'})
		menu.draft['rating'] = '7.0'
		menu.apply()
		menu.draft['rating'] = '9.0'
		menu._save()

		values = self.refine.Refine({'mediatype': 'movie'}).initialize()

		self.assertEqual(values['Rating'], '7.0')
		self.assertEqual(json.loads(self.refine._properties['Bingie.Refine.Draft.movie'])['rating'], '7.0')

	def test_movie_mpaa_choice_is_staged_published_and_counted(self):
		menu = self.refine.Refine({'mediatype': 'movie'})
		menu._select = Mock(return_value=('PG-13', 'PG-13'))

		menu.mpaa()

		self.assertEqual(json.loads(self.refine._properties['Bingie.Refine.Draft.movie'])['mpaa'], 'PG-13')
		self.assertEqual(self.refine._properties['Refine.MPAA'], 'PG-13')
		self.assertEqual(self.refine._properties['Refine.Count'], '1')

		menu._select = Mock(return_value=('Any', ''))
		menu.mpaa()
		self.assertEqual(self.refine._properties['Refine.MPAA'], 'Any')
		self.assertEqual(self.refine._properties['Refine.Count'], '0')

	def test_genre_dialog_displays_names_and_stores_tmdb_ids(self):
		menu = self.refine.Refine({'mediatype': 'movie'})
		self.refine.kodi_utils.select_dialog.return_value = [('Action', '28'), ('Comedy', '35')]

		menu.genres()

		function_list = self.refine.kodi_utils.select_dialog.call_args.args[0]
		items = json.loads(self.refine.kodi_utils.select_dialog.call_args.kwargs['items'])
		self.assertEqual(function_list, [('Action', '28'), ('Comedy', '35')])
		self.assertEqual([item['line1'] for item in items], ['Action', 'Comedy'])
		self.assertEqual(menu.draft['genres'], '28,35')
		self.assertEqual(self.refine._properties['Refine.Genres'], 'Action, Comedy')

	def test_show_results_encodes_all_filters_and_starts_a_fresh_movie_listing(self):
		menu = self.refine.Refine({'mediatype': 'movie'})
		menu.draft.update({
			'sort': 'vote_average', 'sort_label': 'Rating', 'order': 'desc', 'order_label': 'Descending', 'genres': '28,35',
			'genres_label': 'Action, Comedy', 'year_start': '2015', 'year_end': '2026', 'rating': '7.0', 'votes': '250',
			'language': 'fr', 'language_label': 'French', 'mpaa': 'PG-13'
		})

		command = unquote(menu.apply())

		self.assertIn('ActivateWindow(Videos,plugin://skin.titan.bingie.lite/', command)
		self.assertIn('mode=build_movie_list', command)
		self.assertIn('action=tmdb_movies_discover', command)
		self.assertIn('name=Refined+Movies', command)
		self.assertIn('page=%s', command)
		for fragment in ('sort_by=vote_average.desc', 'with_genres=28,35', 'primary_release_date.gte=2015-01-01', 'primary_release_date.lte=2026-12-31', 'vote_average.gte=7.0', 'vote_count.gte=250', 'with_original_language=fr', 'certification_country=US', 'certification=PG-13'):
			self.assertIn(fragment, command)

	def test_tv_results_use_tv_discover_and_first_air_dates(self):
		menu = self.refine.Refine({'mediatype': 'tvshow'})
		menu.draft.update({'year_start': '2020', 'year_end': '2024', 'mpaa': 'R'})

		command = unquote(menu.show_results())

		self.assertIn('action=tmdb_tv_discover', command)
		self.assertIn('name=Refined+TV+Shows', command)
		self.assertIn('first_air_date.gte=2020-01-01', command)
		self.assertIn('first_air_date.lte=2024-12-31', command)
		self.assertNotIn('certification=', command)
		self.assertEqual(menu._publish()['MPAA'], 'Any')
		self.assertEqual(menu._active_count(), 1)

	def test_year_range_counts_once_and_reversed_range_is_not_staged(self):
		menu = self.refine.Refine({'mediatype': 'movie'})
		self.refine.kodi_utils.dialog.numeric.side_effect = ('2020', '2025')
		menu.year()
		self.assertEqual(self.refine._properties['Refine.Year'], '2020–2025')
		self.assertEqual(self.refine._properties['Refine.Count'], '1')

		self.refine.kodi_utils.dialog.numeric.side_effect = ('2030', '2020')
		menu.year()
		self.assertEqual((menu.draft['year_start'], menu.draft['year_end']), ('2020', '2025'))
		self.refine.kodi_utils.notification.assert_called_once()

	def test_drafts_are_independent_per_media_type(self):
		movie = self.refine.Refine({'mediatype': 'movie'})
		movie.draft['rating'] = '8.0'
		movie._save()

		self.assertEqual(self.refine.Refine({'mediatype': 'movie'}).draft['rating'], '8.0')
		self.assertEqual(self.refine.Refine({'mediatype': 'tvshow'}).draft['rating'], '')


if __name__ == '__main__':
	unittest.main()
