import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_smartplay_cache(database_path):
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.watched_db = database_path
	kodi_utils.database_connect = sqlite3.connect
	kodi_utils.external_browse = lambda: True
	kodi_utils.set_property = Mock()
	kodi_utils.container_refresh = Mock()
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	return load_module('test_smartplay_cursor_cache_module', ROOT / 'resources' / 'lib' / 'caches' / 'smartplay_cache.py', {'modules': modules, 'modules.kodi_utils': kodi_utils})


def load_database_cache(temp_dir):
	kodi_utils = types.ModuleType('modules.kodi_utils')
	for name in ('navigator_db', 'watched_db', 'views_db', 'trakt_db', 'maincache_db', 'metacache_db', 'debridcache_db', 'external_db'):
		setattr(kodi_utils, name, str(Path(temp_dir) / ('%s.db' % name)))
	kodi_utils.databases_path = temp_dir
	kodi_utils.database_connect = sqlite3.connect
	kodi_utils.local_string = lambda value: str(value)
	kodi_utils.path_exists = lambda path: True
	kodi_utils.make_directory = lambda path: None
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	return load_module('test_smartplay_database_cache_module', ROOT / 'resources' / 'lib' / 'modules' / 'cache.py', {'modules': modules, 'modules.kodi_utils': kodi_utils})


class SmartPlayCursorCacheTests(unittest.TestCase):
	def setUp(self):
		self.temp_dir = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp_dir.cleanup)
		self.database_path = str(Path(self.temp_dir.name) / 'watched.db')
		self.cache = load_smartplay_cache(self.database_path)
		self.cache.initialize()
		with sqlite3.connect(self.database_path) as dbcon:
			dbcon.execute("""CREATE TABLE watched_status (db_type TEXT, media_id TEXT, season INTEGER, episode INTEGER, last_played TEXT, title TEXT, UNIQUE (db_type, media_id, season, episode))""")
			dbcon.execute("""CREATE TABLE progress (db_type TEXT, media_id TEXT, season INTEGER, episode INTEGER, resume_point TEXT, curr_time TEXT, last_played TEXT, resume_id INTEGER, title TEXT, UNIQUE (db_type, media_id, season, episode))""")

	def test_lookup_and_forward_only_idempotent_advance(self):
		self.assertIsNone(self.cache.lookup('101'))
		self.assertTrue(self.cache.advance('101', '1', '2'))
		self.assertEqual(self.cache.lookup(101), (1, 2))
		self.assertFalse(self.cache.advance(101, 1, 2))
		self.assertFalse(self.cache.advance(101, 1, 1))
		self.assertTrue(self.cache.advance(101, 2, 1))
		self.assertEqual(self.cache.lookup(101), (2, 1))

	def test_cursor_is_one_anonymous_row_per_show(self):
		self.cache.advance(101, 1, 2)
		self.cache.advance(101, 1, 3)
		with sqlite3.connect(self.database_path) as dbcon:
			columns = [row[1] for row in dbcon.execute('PRAGMA table_info(smartplay_cursor)')]
			rows = dbcon.execute('SELECT * FROM smartplay_cursor').fetchall()
		self.assertEqual(columns, ['tmdb_id', 'season', 'episode'])
		self.assertEqual(rows, [(101, 1, 3)])

	def test_invalid_identity_values_are_rejected(self):
		for args in ((0, 1, 1), ('invalid', 1, 1), (101, 0, 1), (101, 1, 0), (True, 1, 1), (101, 1.5, 1)):
			with self.subTest(args=args), self.assertRaises(ValueError): self.cache.advance(*args)

	def test_advance_and_exact_progress_delete_share_one_transaction(self):
		with sqlite3.connect(self.database_path) as dbcon:
			dbcon.executemany('INSERT INTO progress VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (
				('episode', '101', 1, 2, '95', '3000', '', 0, 'Completed'),
				('episode', '101', 1, 3, '40', '1200', '', 0, 'Keep'),
				('episode', '202', 1, 2, '50', '1500', '', 0, 'Other show'),
				('movie', '101', 1, 2, '50', '1500', '', 0, 'Other media type')
			))

		self.assertEqual(self.cache.advance_and_delete_progress(101, 1, 3, 1, 2), (True, True))
		with sqlite3.connect(self.database_path) as dbcon:
			remaining = dbcon.execute('SELECT media_id, season, episode FROM progress ORDER BY media_id, season, episode').fetchall()
		self.assertEqual(self.cache.lookup(101), (1, 3))
		self.assertEqual(remaining, [('101', 1, 2), ('101', 1, 3), ('202', 1, 2)])

	def test_progress_delete_commits_even_when_replayed_episode_does_not_advance_cursor(self):
		self.cache.advance(101, 2, 1)
		with sqlite3.connect(self.database_path) as dbcon:
			dbcon.execute('INSERT INTO progress VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', ('episode', '101', 1, 2, '95', '3000', '', 0, 'Replay'))

		self.assertEqual(self.cache.advance_and_delete_progress(101, 1, 3, 1, 2), (False, True))
		self.assertEqual(self.cache.lookup(101), (2, 1))

	def test_completed_episode_advances_cursor_and_refreshes_progress_only(self):
		with sqlite3.connect(self.database_path) as dbcon:
			dbcon.execute('INSERT INTO progress VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', ('episode', '101', 1, 2, '95', '3000', '', 0, 'Completed'))

		self.assertEqual(self.cache.complete_episode(101, 1, 2), (True, True))
		self.assertEqual(self.cache.lookup(101), (1, 2))
		self.cache.kodi_utils.set_property.assert_called_once()
		self.cache.kodi_utils.container_refresh.assert_not_called()

	def test_completed_special_deletes_exact_progress_without_advancing_cursor(self):
		self.cache.advance(101, 2, 3)
		with sqlite3.connect(self.database_path) as dbcon:
			dbcon.executemany('INSERT INTO progress VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (
				('episode', '101', 0, 4, '95', '3000', '', 0, 'Completed special'),
				('episode', '101', 0, 5, '40', '1200', '', 0, 'Keep special'),
				('episode', '202', 0, 4, '50', '1500', '', 0, 'Other show')
			))

		self.assertEqual(self.cache.complete_episode(101, 0, 4), (False, True))
		self.assertEqual(self.cache.lookup(101), (2, 3))
		with sqlite3.connect(self.database_path) as dbcon:
			remaining = dbcon.execute("SELECT media_id, season, episode FROM progress WHERE db_type = 'episode' ORDER BY media_id, season, episode").fetchall()
		self.assertEqual(remaining, [('101', 0, 5), ('202', 0, 4)])
		self.cache.kodi_utils.set_property.assert_called_once()

	def test_completed_special_without_cursor_only_deletes_progress(self):
		with sqlite3.connect(self.database_path) as dbcon:
			dbcon.execute('INSERT INTO progress VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', ('episode', '101', 0, 1, '95', '3000', '', 0, 'Completed special'))

		self.assertEqual(self.cache.complete_episode('101', '0', '1'), (False, True))
		self.assertIsNone(self.cache.lookup(101))

	def test_failed_special_progress_delete_rolls_back_without_changing_cursor(self):
		self.cache.advance(101, 2, 3)
		with sqlite3.connect(self.database_path) as dbcon:
			dbcon.execute('INSERT INTO progress VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', ('episode', '101', 0, 1, '95', '3000', '', 0, 'Completed special'))
			dbcon.execute("""CREATE TRIGGER reject_special_progress_delete BEFORE DELETE ON progress BEGIN SELECT RAISE(ABORT, 'reject delete'); END""")

		with self.assertRaises(sqlite3.IntegrityError): self.cache.complete_episode(101, 0, 1)
		self.assertEqual(self.cache.lookup(101), (2, 3))
		with sqlite3.connect(self.database_path) as dbcon:
			self.assertEqual(dbcon.execute('SELECT media_id, season, episode FROM progress').fetchall(), [('101', 0, 1)])

	def test_failed_progress_delete_rolls_back_cursor_advance(self):
		with sqlite3.connect(self.database_path) as dbcon:
			dbcon.execute('INSERT INTO progress VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', ('episode', '101', 1, 1, '95', '3000', '', 0, 'Completed'))
			dbcon.execute("""CREATE TRIGGER reject_progress_delete BEFORE DELETE ON progress BEGIN SELECT RAISE(ABORT, 'reject delete'); END""")

		with self.assertRaises(sqlite3.IntegrityError): self.cache.advance_and_delete_progress(101, 1, 2, 1, 1)
		self.assertIsNone(self.cache.lookup(101))
		with sqlite3.connect(self.database_path) as dbcon:
			self.assertEqual(dbcon.execute('SELECT media_id, season, episode FROM progress').fetchall(), [('101', 1, 1)])

	def test_startup_watched_history_clear_preserves_cursor_schema_and_row(self):
		cache = load_database_cache(self.temp_dir.name)
		cache.check_databases()
		with sqlite3.connect(cache.watched_db) as dbcon:
			dbcon.execute('INSERT INTO watched_status VALUES (?, ?, ?, ?, ?, ?)', ('episode', '101', 1, 1, '', 'Completed'))
			dbcon.execute('INSERT INTO smartplay_cursor VALUES (?, ?, ?)', (101, 1, 2))

		cache.check_databases()

		with sqlite3.connect(cache.watched_db) as dbcon:
			self.assertEqual(dbcon.execute('SELECT * FROM watched_status').fetchall(), [])
			self.assertEqual(dbcon.execute('SELECT * FROM smartplay_cursor').fetchall(), [(101, 1, 2)])


if __name__ == '__main__':
	unittest.main()
