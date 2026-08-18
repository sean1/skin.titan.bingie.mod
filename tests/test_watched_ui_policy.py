import re
import unittest
from pathlib import Path

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


class WatchedUiPolicyTests(unittest.TestCase):
	def test_default_menus_hide_watched_pages_and_keep_in_progress(self):
		menu_lists = load_module('test_watched_ui_menu_lists', ROOT / 'resources' / 'lib' / 'modules' / 'menu_lists.py', {})
		movie_actions = {item.get('action') for item in menu_lists.movie_list}
		tv_actions = {item.get('action') for item in menu_lists.tvshow_list}

		self.assertNotIn('watched_movies', movie_actions)
		self.assertNotIn('watched_tvshows', tv_actions)
		self.assertIn('in_progress_movies', movie_actions)
		self.assertIn('in_progress_tvshows', tv_actions)
		self.assertIn('build_in_progress_episode', {item.get('mode') for item in menu_lists.tvshow_list})

	def test_listing_builders_have_no_watched_item_exclusion_policy(self):
		for filename in ('movies.py', 'tvshows.py', 'seasons.py', 'episodes.py'):
			with self.subTest(filename=filename):
				source = (ROOT / 'resources' / 'lib' / 'menus' / filename).read_text(encoding='utf-8')
				self.assertNotIn('widget_hide_watched', source)

	def test_clear_progress_uses_targeted_progress_refresh(self):
		for filename in ('movies.py', 'seasons.py', 'episodes.py'):
			with self.subTest(filename=filename):
				source = (ROOT / 'resources' / 'lib' / 'menus' / filename).read_text(encoding='utf-8')
				self.assertIn("'mode': 'watched_unwatched_erase_bookmark'", source)
				self.assertNotIn("'refresh': 'true'", source)
				self.assertIn("'refresh': 'progress'", source)

	def test_every_continue_watching_widget_has_a_targeted_refresh_key(self):
		source = ''.join((ROOT / 'xml' / filename).read_text(encoding='utf-8') for filename in ('IncludesStaticMenus.xml', 'IncludesHubs.xml'))
		movie_paths = re.findall(r'plugin://skin\.titan\.bingie\.lite/\?[^"<]*action=in_progress_movies[^"<]*', source)
		episode_paths = re.findall(r'plugin://skin\.titan\.bingie\.lite/\?[^"<]*mode=build_in_progress_episode[^"<]*', source)
		self.assertTrue(movie_paths)
		self.assertTrue(episode_paths)
		self.assertTrue(all('BingieProgressRefreshMovie' in path for path in movie_paths))
		self.assertTrue(all('BingieProgressRefreshEpisode' in path for path in episode_paths))

	def test_every_builtin_media_widget_has_a_targeted_watched_refresh_key(self):
		static_source = (ROOT / 'xml' / 'IncludesStaticMenus.xml').read_text(encoding='utf-8')
		static_widget_source = static_source[:static_source.index('</item>')] + static_source[static_source.index('<variable name="BingieHomeContinueMoviesPath">'):]
		hub_source = (ROOT / 'xml' / 'IncludesHubs.xml').read_text(encoding='utf-8')
		paths = re.findall(r'plugin://skin\.titan\.bingie\.lite/\?[^"<]*', static_widget_source + hub_source)
		movie_paths = [path for path in paths if 'mode=build_movie_list' in path]
		tv_paths = [path for path in paths if 'mode=build_tvshow_list' in path or 'mode=build_in_progress_episode' in path]
		self.assertTrue(movie_paths)
		self.assertTrue(tv_paths)
		self.assertTrue(all('BingieWatchedRefreshMovie' in path for path in movie_paths))
		self.assertTrue(all('BingieWatchedRefreshTV' in path for path in tv_paths))


if __name__ == '__main__':
	unittest.main()
