import re
import unittest
from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]


class WatchedUiPolicyTests(unittest.TestCase):

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

if __name__ == '__main__':
	unittest.main()
