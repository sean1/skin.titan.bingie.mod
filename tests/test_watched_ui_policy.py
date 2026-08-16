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


if __name__ == '__main__':
	unittest.main()
