import json
import types
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse, urlencode
from unittest.mock import Mock, patch

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

	def test_changed_tracks_staged_edits_clear_and_apply(self):
		menu = self.refine.Refine({'mediatype': 'movie'})
		menu.initialize()
		menu._select = Mock(return_value=('7.0', '7.0'))

		self.assertEqual(menu.rating()['Changed'], 'true')
		self.assertEqual(self.refine._properties['Refine.Changed'], 'true')
		menu.clear()
		self.assertEqual(self.refine._properties['Refine.Changed'], 'false')

		menu._select = Mock(return_value=('7.0', '7.0'))
		menu.rating()
		menu.apply()
		self.assertEqual(self.refine._properties['Refine.Changed'], 'false')

	def test_changed_compares_against_applied_state_for_each_media_type(self):
		movie = self.refine.Refine({'mediatype': 'movie'})
		movie.draft['rating'] = '8.0'
		movie.apply()

		tv = self.refine.Refine({'mediatype': 'tvshow'})
		tv.initialize()
		tv.draft['rating'] = '8.0'
		self.assertEqual(tv._save()['Changed'], 'true')
		self.assertEqual(movie._publish()['Changed'], 'false')

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
		menu._multiselect = Mock(return_value=[('G', 'G'), ('PG', 'PG')])

		menu.mpaa()

		self.assertEqual(json.loads(self.refine._properties['Bingie.Refine.Draft.movie'])['mpaa'], 'G|PG')
		self.assertEqual(self.refine._properties['Refine.MPAA'], 'G, PG')
		self.assertEqual(self.refine._properties['Refine.Count'], '1')

		menu._multiselect = Mock(return_value=[])
		menu.mpaa()
		self.assertEqual(self.refine._properties['Refine.MPAA'], 'Any')
		self.assertEqual(self.refine._properties['Refine.Count'], '0')
		self.assertTrue(menu._multiselect.call_args.kwargs['allow_empty'])

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
		self.assertEqual(self.refine.kodi_utils.select_dialog.call_args.kwargs['allow_empty'], 'true')

	def test_every_curated_theme_is_available_and_encodes_only_its_keywords(self):
		self.assertEqual(len(self.refine.THEME_OPTIONS), 25)
		for mediatype in ('movie', 'tvshow'):
			for label, keywords in self.refine.THEME_OPTIONS[1:]:
				with self.subTest(mediatype=mediatype, label=label):
					menu = self.refine.Refine({'mediatype': mediatype})
					menu.draft.update({'theme': keywords, 'theme_label': label})
					command = unquote(menu.apply())
					self.assertIn('&with_keywords=%s' % keywords, command)
					self.assertNotIn('&with_genres=', command)
					self.assertNotIn('&vote_count.gte=', command)

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

	def test_tv_network_is_encoded_and_movies_ignore_network_state(self):
		tv = self.refine.Refine({'mediatype': 'tvshow'})
		tv.draft.update({'network': '213', 'network_label': 'Netflix'})
		self.assertIn('with_networks=213', unquote(tv.apply()))

		movie = self.refine.Refine({'mediatype': 'movie'})
		movie.draft.update({'network': '213', 'network_label': 'Netflix'})
		command = unquote(movie.apply())
		self.assertNotIn('with_networks=', command)
		self.assertEqual(movie._publish()['Network'], 'Any')
		self.assertEqual(movie._active_count(), 0)

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

	def test_all_presets_apply_their_recipes_and_keep_unrelated_filters(self):
		cases = {
			'Crowd Favorites': ('popularity', '7.0', '1000', '', '', ''),
			'New & Noteworthy': ('primary_release_date', '6.5', '50', '', 'true', ''),
			'New Releases': ('primary_release_date', '', '1', '', '', '90'),
			'Hidden Gems': ('vote_average', '7.0', '50', '500', '', '')
		}
		for name, expected in cases.items():
			with self.subTest(name=name):
				menu = self.refine.Refine({'mediatype': 'movie'})
				menu.draft.update({'language': 'fr', 'language_label': 'French', 'year_start': '2020', 'genres': '28', 'genres_label': 'Action', 'mpaa': 'R'})
				menu._select = Mock(return_value=(name, name))
				values = menu.preset()
				self.assertEqual((menu.draft['sort'], menu.draft['rating'], menu.draft['votes'], menu.draft['max_votes'], menu.draft['released_only'], menu.draft['release_window']), expected)
				self.assertEqual((menu.draft['language'], menu.draft['year_start'], menu.draft['genres'], menu.draft['mpaa']), ('fr', '' if name == 'New Releases' else '2020', '28', 'R'))
				self.assertEqual(values['Preset'], name)

	def test_family_night_sets_media_specific_dependencies_and_switching_clears_them(self):
		movie = self.refine.Refine({'mediatype': 'movie'})
		movie._select = Mock(return_value=('Family Night', 'Family Night'))
		self.assertEqual(movie.preset()['Preset'], 'Family Night')
		self.assertEqual((movie.draft['genres'], movie.draft['genres_label'], movie.draft['mpaa']), ('10751', 'Family', 'G|PG'))
		movie._select = Mock(return_value=('Top Rated', 'Top Rated'))
		movie.preset()
		self.assertEqual((movie.draft['genres'], movie.draft['mpaa']), ('', ''))

		tv = self.refine.Refine({'mediatype': 'tvshow'})
		tv._select = Mock(return_value=('Family Night', 'Family Night'))
		self.assertEqual(tv.preset()['Preset'], 'Family Night')
		self.assertEqual((tv.draft['genres'], tv.draft['mpaa']), ('10751', ''))

	def test_manual_dependent_changes_make_family_custom(self):
		menu = self.refine.Refine({'mediatype': 'movie'})
		menu._select = Mock(return_value=('Family Night', 'Family Night'))
		menu.preset()
		menu._multiselect = Mock(return_value=[('PG', 'PG')])
		self.assertEqual(menu.mpaa()['Preset'], 'Custom')
		menu._select = Mock(return_value=('Top Rated', 'Top Rated'))
		menu.preset()
		self.assertEqual((menu.draft['genres'], menu.draft['mpaa']), ('', ''))

	def test_maximum_votes_and_release_cutoff_are_encoded_for_each_media_type(self):
		class FixedDate:
			@classmethod
			def today(cls): return cls()
			def isoformat(self): return '2026-08-22'

		for mediatype, date_key in (('movie', 'primary_release_date'), ('tvshow', 'first_air_date')):
			with self.subTest(mediatype=mediatype), patch.object(self.refine, 'date', FixedDate):
				menu = self.refine.Refine({'mediatype': mediatype})
				menu.draft.update({'max_votes': '500', 'released_only': 'true'})
				command = unquote(menu.apply())
				self.assertIn('vote_count.lte=500', command)
				self.assertIn('%s.lte=2026-08-22' % date_key, command)

	def test_new_releases_uses_rolling_90_day_window_for_movies_and_tv(self):
		class FixedDate(date):
			@classmethod
			def today(cls): return cls(2026, 8, 22)

		for mediatype, date_key, sort in (('movie', 'primary_release_date', 'primary_release_date'), ('tvshow', 'first_air_date', 'first_air_date')):
			with self.subTest(mediatype=mediatype), patch.object(self.refine, 'date', FixedDate):
				menu = self.refine.Refine({'mediatype': mediatype})
				menu.draft.update({'genres': '28', 'genres_label': 'Action', 'language': 'fr', 'language_label': 'French'})
				menu._select = Mock(return_value=('New Releases', 'New Releases'))
				values = menu.preset()
				command = unquote(menu.apply())
				self.assertEqual((menu.draft['sort'], menu.draft['votes'], menu.draft['release_window']), (sort, '1', '90'))
				self.assertEqual((menu.draft['genres'], menu.draft['language']), ('28', 'fr'))
				self.assertEqual(values['Preset'], 'New Releases')
				self.assertIn('%s.gte=2026-05-24' % date_key, command)
				self.assertIn('%s.lte=2026-08-22' % date_key, command)
				self.assertIn('vote_count.gte=1', command)
				self.assertIn('sort_by=%s.desc' % sort, command)

	def test_release_window_is_selectable_counted_and_changes_preset_label(self):
		menu = self.refine.Refine({'mediatype': 'tvshow'})
		menu.draft.update({'year_start': '2020', 'year_end': '2025'})
		menu._select = Mock(return_value=('New Releases', 'New Releases'))
		self.assertEqual(menu.preset()['Preset'], 'New Releases')
		self.assertEqual((menu.draft['year_start'], menu.draft['year_end']), ('', ''))
		self.assertEqual(menu._publish()['ReleaseWindow'], 'Last 90 days')

		menu._select = Mock(return_value=('Last 30 days', '30'))
		values = menu.release_window()
		self.assertEqual((values['ReleaseWindow'], values['Preset']), ('Last 30 days', 'Custom'))
		self.assertEqual(values['Count'], '3')
		menu._select = Mock(return_value=('Last 90 days', '90'))
		self.assertEqual(menu.release_window()['Preset'], 'New Releases')

		menu.clear()
		self.assertEqual((menu.draft['release_window'], self.refine._properties['Refine.ReleaseWindow']), ('', 'Any'))

	def test_absolute_year_and_release_window_are_mutually_exclusive(self):
		menu = self.refine.Refine({'mediatype': 'movie'})
		menu.draft.update({'year_start': '2020', 'year_end': '2025'})
		menu._select = Mock(return_value=('Last 90 days', '90'))
		menu.release_window()
		self.assertEqual((menu.draft['year_start'], menu.draft['year_end'], menu.draft['release_window']), ('', '', '90'))

		self.refine.kodi_utils.dialog.numeric.side_effect = ('2024', '2026')
		menu.year()
		self.assertEqual((menu.draft['year_start'], menu.draft['year_end'], menu.draft['release_window']), ('2024', '2026', ''))


if __name__ == '__main__':
	unittest.main()
