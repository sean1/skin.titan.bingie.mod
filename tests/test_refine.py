import types
import unittest
from pathlib import Path
from urllib.parse import unquote, urlencode
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
	kodi_utils.get_visibility = Mock(return_value=False)
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
	meta_lists.networks = ({'id': 213, 'name': 'Netflix', 'logo': 'netflix.png'}, {'id': 49, 'name': 'HBO', 'logo': 'hbo.png'})
	modules = types.ModuleType('modules')
	modules.kodi_utils, modules.meta_lists = kodi_utils, meta_lists
	stubs = {'indexers': indexers, 'indexers.tmdb_api': tmdb_api, 'modules': modules, 'modules.kodi_utils': kodi_utils, 'modules.meta_lists': meta_lists}
	module = load_module('test_refine_module', ROOT / 'resources/lib/menus/refine.py', stubs)
	module._properties = properties
	return module


class RefineTests(unittest.TestCase):
	def setUp(self):
		self.refine = load_refine_module()


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


if __name__ == '__main__':
	unittest.main()
