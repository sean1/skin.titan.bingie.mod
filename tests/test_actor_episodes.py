import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_actor_episodes():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.progressDialog = Mock()
	kodi_utils.progressDialog.iscanceled.return_value = False
	kodi_utils.monitor = Mock()
	kodi_utils.monitor.abortRequested.return_value = False
	kodi_utils.notification = Mock()
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	tmdb_api = types.ModuleType('indexers.tmdb_api')
	tmdb_api.base_url = 'https://api.themoviedb.org/3'
	tmdb_api.get_tmdb = Mock()
	tmdb_api.cache_object = Mock()
	tmdb_api.EXPIRES_1_WEEK = 168
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils, 'indexers': types.ModuleType('indexers'), 'indexers.tmdb_api': tmdb_api}
	return load_module('test_actor_episodes_module', ROOT / 'resources/lib/menus/actor_episodes.py', stubs)


class ActorEpisodesTests(unittest.TestCase):
	def setUp(self):
		self.module = load_actor_episodes()
		self.progress = self.module.kodi_utils.progressDialog


	def test_failed_lookup_preserves_matches_and_warns_of_partial_results(self):
		episodes = [{'season': 1, 'episode': number} for number in range(1, 4)]
		def credits(show, season, episode):
			if episode == 2: raise OSError('Network unavailable')
			if episode == 3: return {}
			return {'cast': [{'id': 42}], 'guest_stars': []}
		self.module.episode_credits = credits
		self.assertEqual(self.module.filter_actor_episodes(100, episodes, 42, 'Target Actor'), episodes[:1])
		self.module.kodi_utils.notification.assert_called_once_with('Could not check 2 episodes. Results may be incomplete.')
		self.progress.close.assert_called_once_with()


if __name__ == '__main__': unittest.main()
