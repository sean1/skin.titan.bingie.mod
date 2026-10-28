import sqlite3
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


class ThreadTaskPool:
	def __init__(self, maxsize=None):
		self.maxsize = maxsize

	def tasks(self, target, items, thread):
		threads = [thread(target=target, args=item) for item in items]
		for worker in threads: worker.start()
		return threads


def load_watched_cache(external=False):
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.watched_db = 'watched.db'
	kodi_utils.local_string = Mock(return_value='Please wait')
	kodi_utils.progressDialogBG = Mock()
	kodi_utils.external_browse = Mock(return_value=external)
	kodi_utils.set_property = Mock()
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
	utils.TaskPool = ThreadTaskPool
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


	def test_batch_closes_progress_without_refresh_when_loader_fails(self):
		loader = Mock(side_effect=RuntimeError('metadata failed'))

		with self.assertRaises(RuntimeError): self.watched._mark_episode_batch({'action': 'mark_as_watched', 'tmdb_id': 101}, loader, '')

		self.watched.kodi_utils.progressDialogBG.close.assert_called_once_with()
		self.watched.kodi_utils.container_refresh.assert_not_called()


	def test_single_watched_change_and_progress_delete_share_one_transaction(self):
		with tempfile.NamedTemporaryFile() as database_file:
			self._create_watched_schema(database_file.name)
			connection = sqlite3.connect(database_file.name)
			connection.execute("INSERT INTO progress VALUES ('movie', '101', '', '', '50', '100', 'now', 0, 'Movie')")
			connection.commit()
			connection.close()
			watched = load_watched_cache(external=True)
			watched.kodi_utils.database_connect.side_effect = lambda path, **kwargs: sqlite3.connect(database_file.name, **kwargs)

			watched.mark_as_watched_unwatched_movie({'action': 'mark_as_watched', 'tmdb_id': '101', 'title': 'Movie'})

			connection = sqlite3.connect(database_file.name)
			self.assertEqual(connection.execute("SELECT COUNT(*) FROM watched_status WHERE media_id = '101'").fetchone()[0], 1)
			self.assertEqual(connection.execute("SELECT COUNT(*) FROM progress WHERE media_id = '101'").fetchone()[0], 0)
			connection.close()
			watched.kodi_utils.set_property.assert_called_once()
			self.assertEqual(watched.kodi_utils.set_property.call_args.args[0], 'BingieWatchedRefreshMovie')
			watched.kodi_utils.widget_refresh.assert_not_called()

	def test_single_watched_change_rolls_back_when_progress_delete_fails(self):
		with tempfile.NamedTemporaryFile() as database_file:
			self._create_watched_schema(database_file.name)
			connection = sqlite3.connect(database_file.name)
			connection.execute("CREATE TRIGGER fail_progress_delete BEFORE DELETE ON progress BEGIN SELECT RAISE(ABORT, 'fail'); END")
			connection.execute("INSERT INTO progress VALUES ('movie', '101', '', '', '50', '100', 'now', 0, 'Movie')")
			connection.commit()
			connection.close()
			watched = load_watched_cache(external=True)
			watched.kodi_utils.database_connect.side_effect = lambda path, **kwargs: sqlite3.connect(database_file.name, **kwargs)

			watched.mark_as_watched_unwatched_movie({'action': 'mark_as_watched', 'tmdb_id': '101', 'title': 'Movie'})

			connection = sqlite3.connect(database_file.name)
			self.assertEqual(connection.execute("SELECT COUNT(*) FROM watched_status WHERE media_id = '101'").fetchone()[0], 0)
			self.assertEqual(connection.execute("SELECT COUNT(*) FROM progress WHERE media_id = '101'").fetchone()[0], 1)
			connection.close()
			watched.kodi_utils.notification.assert_called_once_with(32574)
			watched.kodi_utils.set_property.assert_not_called()

	def test_batch_watched_changes_commit_status_and_progress_atomically(self):
		with tempfile.NamedTemporaryFile() as database_file:
			self._create_watched_schema(database_file.name)
			connection = sqlite3.connect(database_file.name)
			connection.executemany("INSERT INTO progress VALUES ('episode', '101', ?, ?, '50', '100', 'now', 0, 'Show')", [(1, 1), (1, 2)])
			connection.commit()
			connection.close()
			watched = load_watched_cache()
			watched.kodi_utils.database_connect.side_effect = lambda path, **kwargs: sqlite3.connect(database_file.name, **kwargs)
			rows = [('episode', '101', 1, 1, 'now', 'Show'), ('episode', '101', 1, 2, 'now', 'Show')]

			self.assertTrue(watched.batch_mark_as_watched_unwatched('local', rows, 'mark_as_watched'))

			connection = sqlite3.connect(database_file.name)
			self.assertEqual(connection.execute('SELECT COUNT(*) FROM watched_status').fetchone()[0], 2)
			self.assertEqual(connection.execute('SELECT COUNT(*) FROM progress').fetchone()[0], 0)
			connection.close()


	@staticmethod
	def _create_watched_schema(path):
		connection = sqlite3.connect(path)
		connection.execute('CREATE TABLE watched_status (db_type TEXT, media_id TEXT, season INTEGER, episode INTEGER, last_played TEXT, title TEXT, UNIQUE (db_type, media_id, season, episode))')
		connection.execute('CREATE TABLE progress (db_type TEXT, media_id TEXT, season INTEGER, episode INTEGER, resume_point TEXT, curr_time TEXT, last_played TEXT, resume_id INTEGER, title TEXT, UNIQUE (db_type, media_id, season, episode))')
		connection.commit()
		connection.close()


if __name__ == '__main__':
	unittest.main()
