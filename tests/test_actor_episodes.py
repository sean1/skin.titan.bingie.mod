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

	def test_matches_cast_and_guest_ids_without_matching_crew_or_names(self):
		for credits in ({'cast': [{'id': 42}], 'guest_stars': []}, {'cast': [], 'guest_stars': [{'id': '42'}]}):
			with self.subTest(credits=credits): self.assertTrue(self.module.credited_actor(credits, '42'))
		self.assertFalse(self.module.credited_actor({'cast': [{'id': 17, 'name': 'Target Actor'}], 'guest_stars': [], 'crew': [{'id': 42}]}, 42))

	def test_filters_and_sorts_numerically_across_seasons(self):
		episodes = [{'season': '10', 'episode': '1'}, {'season': '2', 'episode': '10'}, {'season': '2', 'episode': '2'}, {'season': '1', 'episode': '1'}]
		self.module.episode_credits = lambda show, season, episode: {'cast': [], 'guest_stars': [{'id': 42}] if season != '1' else []}
		self.assertEqual(self.module.filter_actor_episodes(100, episodes, 42, 'Target Actor'), [episodes[2], episodes[1], episodes[0]])
		self.progress.close.assert_called_once_with()
		self.module.kodi_utils.notification.assert_not_called()

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

	def test_cancellation_after_first_batch_discards_partial_results_and_closes_dialog(self):
		episodes = [{'season': 1, 'episode': number} for number in range(1, 7)]
		self.module.episode_credits = Mock(return_value={'cast': [{'id': 42}], 'guest_stars': []})
		self.progress.iscanceled.side_effect = [False, True]
		self.assertEqual(self.module.filter_actor_episodes(100, episodes, 42, 'Target Actor'), [])
		self.assertEqual(self.module.episode_credits.call_count, 4)
		self.progress.close.assert_called_once_with()
		self.module.kodi_utils.notification.assert_not_called()

	def test_shutdown_before_lookup_closes_dialog_without_fetching(self):
		self.module.kodi_utils.monitor.abortRequested.return_value = True
		self.module.episode_credits = Mock()
		self.assertEqual(self.module.filter_actor_episodes(100, [{'season': 1, 'episode': 1}], 42, 'Target Actor'), [])
		self.module.episode_credits.assert_not_called()
		self.progress.close.assert_called_once_with()


if __name__ == '__main__': unittest.main()
