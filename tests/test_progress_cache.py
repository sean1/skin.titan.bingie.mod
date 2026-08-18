import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_progress_cache(external=False):
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.watched_db = 'watched.db'
	kodi_utils.database_connect = Mock()
	kodi_utils.notification = Mock()
	kodi_utils.external_browse = Mock(return_value=external)
	kodi_utils.set_property = Mock()
	kodi_utils.widget_refresh = Mock()
	kodi_utils.container_refresh = Mock()
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils}
	path = ROOT / 'resources' / 'lib' / 'caches' / 'progress_cache.py'
	return load_module('test_progress_cache_module', path, stubs)


class ProgressCacheTests(unittest.TestCase):
	def setUp(self):
		self.progress = load_progress_cache()
		self.dbcon, self.dbcur = Mock(), Mock()
		self.dbcur.rowcount = 1
		self.dbcon.cursor.return_value = self.dbcur
		self.progress.kodi_utils.database_connect.return_value = self.dbcon

	def test_erase_bookmark_is_one_indexed_delete_without_refresh(self):
		self.progress.erase_bookmark('movie', '101')

		self.progress.kodi_utils.database_connect.assert_called_once_with('watched.db', timeout=1, isolation_level=None)
		self.dbcur.execute.assert_called_once_with(self.progress.DELETE_BM, ('movie', '101', '', ''))
		self.dbcon.close.assert_called_once_with()
		self.progress.kodi_utils.external_browse.assert_not_called()
		self.progress.kodi_utils.widget_refresh.assert_not_called()
		self.progress.kodi_utils.container_refresh.assert_not_called()

	def test_erase_bookmark_normalizes_episode_numbers(self):
		self.progress.erase_bookmark('episode', '101', '2', '3')

		self.dbcur.execute.assert_called_once_with(self.progress.DELETE_BM, ('episode', '101', 2, 3))

	def test_explicit_refresh_uses_current_container(self):
		self.progress.erase_bookmark('movie', '101', refresh='progress')

		self.progress.kodi_utils.container_refresh.assert_called_once_with()
		self.progress.kodi_utils.set_property.assert_not_called()
		self.progress.kodi_utils.widget_refresh.assert_not_called()

	def test_progress_refresh_invalidates_only_the_matching_external_widget(self):
		progress = load_progress_cache(external=True)
		dbcon, dbcur = Mock(), Mock()
		dbcur.rowcount = 1
		dbcon.cursor.return_value = dbcur
		progress.kodi_utils.database_connect.return_value = dbcon

		progress.erase_bookmark('episode', '101', '2', '3', refresh='progress')

		property_name, property_value = progress.kodi_utils.set_property.call_args.args
		self.assertEqual(property_name, 'BingieProgressRefreshEpisode')
		self.assertGreater(int(property_value), 0)
		progress.kodi_utils.widget_refresh.assert_not_called()
		progress.kodi_utils.container_refresh.assert_not_called()

	def test_legacy_explicit_refresh_keeps_global_widget_signal(self):
		progress = load_progress_cache(external=True)
		dbcon, dbcur = Mock(), Mock()
		dbcur.rowcount = 1
		dbcon.cursor.return_value = dbcur
		progress.kodi_utils.database_connect.return_value = dbcon

		progress.erase_bookmark('movie', '101', refresh='true')

		progress.kodi_utils.widget_refresh.assert_called_once_with()
		progress.kodi_utils.set_property.assert_not_called()

	def test_noop_delete_skips_explicit_refresh(self):
		self.dbcur.rowcount = 0

		self.progress.erase_bookmark('movie', '101', refresh='true')

		self.progress.kodi_utils.external_browse.assert_not_called()
		self.progress.kodi_utils.set_property.assert_not_called()
		self.progress.kodi_utils.widget_refresh.assert_not_called()
		self.progress.kodi_utils.container_refresh.assert_not_called()

	def test_set_bookmark_closes_before_targeted_progress_refresh(self):
		progress = load_progress_cache(external=True)
		dbcon = Mock()
		events = []
		dbcon.close.side_effect = lambda: events.append('close')
		progress.kodi_utils.set_property.side_effect = lambda *args: events.append('refresh')
		progress.kodi_utils.database_connect.return_value = dbcon

		self.assertTrue(progress.set_bookmark('episode', '101', 100, 200, 'Show', 2, 3, 'progress'))

		progress.kodi_utils.database_connect.assert_called_once_with('watched.db', timeout=1, isolation_level=None)
		self.assertEqual(dbcon.execute.call_args.args[0], progress.SET_BM)
		self.assertEqual(events, ['close', 'refresh'])
		self.assertEqual(progress.kodi_utils.set_property.call_args.args[0], 'BingieProgressRefreshEpisode')
		progress.kodi_utils.widget_refresh.assert_not_called()


if __name__ == '__main__':
	unittest.main()
