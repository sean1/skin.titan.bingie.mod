import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_watched_cache():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.watched_db = 'watched.db'
	kodi_utils.local_string = Mock(return_value='Please wait')
	kodi_utils.progressDialogBG = Mock()
	kodi_utils.external_browse = Mock(return_value=False)
	kodi_utils.widget_refresh = Mock()
	kodi_utils.container_refresh = Mock()
	kodi_utils.notification = Mock()
	kodi_utils.database_connect = Mock()
	settings = types.ModuleType('modules.settings')
	settings.watched_indicators = Mock(return_value='local')
	settings.metadata_user_info = Mock(return_value={'language': 'en'})
	settings.date_offset = Mock(return_value=0)
	metadata = types.ModuleType('indexers.metadata')
	metadata.tvshow_meta = Mock(return_value={'season_data': []})
	metadata.season_episodes_meta = Mock(return_value=[])
	dropped_cache = types.ModuleType('caches.dropped_cache')
	dropped_cache.get_hidden_items = Mock(return_value=[])
	utils = types.ModuleType('modules.utils')
	utils.LIST_WORKERS = 5
	utils.adjust_premiered_date = Mock()
	utils.get_datetime = Mock(return_value=datetime(2024, 1, 2))
	utils.paginate_list = Mock()
	utils.sort_for_article = Mock()
	utils.TaskPool = Mock
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	modules.settings = settings
	indexers = types.ModuleType('indexers')
	indexers.__path__ = []
	indexers.metadata = metadata
	caches = types.ModuleType('caches')
	caches.__path__ = []
	stubs = {
		'caches': caches, 'caches.dropped_cache': dropped_cache, 'indexers': indexers, 'indexers.metadata': metadata,
		'modules': modules, 'modules.kodi_utils': kodi_utils, 'modules.settings': settings, 'modules.utils': utils
	}
	path = ROOT / 'resources' / 'lib' / 'caches' / 'watched_cache.py'
	return load_module('test_watched_cache_refactor_module', path, stubs)


class WatchedCacheRefactorTests(unittest.TestCase):
	def setUp(self):
		self.watched = load_watched_cache()

	def test_tvshow_loader_skips_specials_and_collects_all_regular_seasons(self):
		meta = {'season_data': [{'season_number': 0}, {'season_number': 1}, {'season_number': 2}]}
		self.watched.metadata.season_episodes_meta.side_effect = [['s1e1'], ['s2e1']]

		result = self.watched._tvshow_episodes_meta(meta, {'language': 'en'})

		self.assertEqual(result, ['s1e1', 's2e1'])
		self.assertEqual([call.args[0] for call in self.watched.metadata.season_episodes_meta.call_args_list], [1, 2])

	def test_batch_marks_only_aired_episodes_and_closes_progress(self):
		params = {'action': 'mark_as_watched', 'tmdb_id': 101}
		episodes = [
			{'season': 1, 'episode': 1, 'premiered': '2024-01-01'},
			{'season': 1, 'episode': 2, 'premiered': '2024-01-03'}
		]
		loader = Mock(return_value=episodes)
		self.watched.adjust_premiered_date.side_effect = [(datetime(2024, 1, 1), '2024-01-01'), (datetime(2024, 1, 3), '2024-01-03')]
		self.watched.get_last_played_value = Mock(return_value='now')
		self.watched.batch_mark_as_watched_unwatched = Mock()

		self.watched._mark_episode_batch(params, loader, '')

		loader.assert_called_once_with({'season_data': []}, {'language': 'en'})
		self.watched.batch_mark_as_watched_unwatched.assert_called_once_with('local', [('episode', 101, 1, 1, 'now', '')], 'mark_as_watched')
		self.assertEqual([call.args[0] for call in self.watched.kodi_utils.progressDialogBG.update.call_args_list], [50, 100])
		self.watched.kodi_utils.progressDialogBG.close.assert_called_once_with()
		self.watched.kodi_utils.container_refresh.assert_called_once_with()

	def test_batch_closes_progress_without_refresh_when_loader_fails(self):
		loader = Mock(side_effect=RuntimeError('metadata failed'))

		with self.assertRaises(RuntimeError): self.watched._mark_episode_batch({'action': 'mark_as_watched', 'tmdb_id': 101}, loader, '')

		self.watched.kodi_utils.progressDialogBG.close.assert_called_once_with()
		self.watched.kodi_utils.container_refresh.assert_not_called()

	def test_season_zero_keeps_existing_notification_guard(self):
		self.watched._mark_episode_batch = Mock()

		self.watched.mark_as_watched_unwatched_season({'season': '0', 'action': 'mark_as_watched'})

		self.watched.kodi_utils.notification.assert_called_once_with(32575)
		self.watched._mark_episode_batch.assert_not_called()

	def test_tvshow_status_excludes_specials(self):
		watched_info = {101: [('episode', 101, '', 0, 1), ('episode', 101, '', 1, 1), ('episode', 101, '', 1, 2)]}

		self.assertEqual(self.watched.get_watched_status_tvshow(watched_info, 101, 2), (1, 5, 2, 0))

	def test_season_status_counts_only_requested_season(self):
		watched_info = {101: [('episode', 101, '', 1, 1), ('episode', 101, '', 2, 1)]}

		self.assertEqual(self.watched.get_watched_status_season(watched_info, 101, 1, 2), (0, 4, 1, 1))

	def test_zero_aired_episodes_never_reports_complete(self):
		self.assertEqual(self.watched.get_watched_status_tvshow({101: []}, 101, 0), (0, 4, 0, 0))


if __name__ == '__main__':
	unittest.main()
