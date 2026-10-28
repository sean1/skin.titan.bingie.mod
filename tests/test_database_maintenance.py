import sqlite3
import tempfile
import types
import unittest
from pathlib import Path

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_cache_module():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	for name in (
		'navigator_db', 'watched_db', 'views_db', 'trakt_db', 'maincache_db', 'metacache_db', 'debridcache_db', 'external_db', 'databases_path'
	): setattr(kodi_utils, name, name)
	kodi_utils.database_connect = sqlite3.connect
	kodi_utils.local_string = lambda value: str(value)
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils}
	path = ROOT / 'resources' / 'lib' / 'modules' / 'cache.py'
	return load_module('test_database_maintenance_cache', path, stubs)


def load_meta_cache_module():
	caches = types.ModuleType('caches')
	caches.BaseCache = object
	caches.metacache_db = 'metacache_db'
	window_property_cache = types.ModuleType('caches.window_property_cache')
	window_property_cache.WindowPropertyCache = lambda *args: None
	modules = types.ModuleType('modules')
	modules.kodi_utils = types.ModuleType('modules.kodi_utils')
	stubs = {
		'caches': caches,
		'caches.window_property_cache': window_property_cache,
		'modules': modules,
		'modules.kodi_utils': modules.kodi_utils
	}
	path = ROOT / 'resources' / 'lib' / 'caches' / 'meta_cache.py'
	return load_module('test_database_maintenance_meta_cache', path, stubs)


class DatabaseMaintenanceTests(unittest.TestCase):
	def setUp(self):
		self.cache = load_cache_module()
		self.meta_cache = load_meta_cache_module()

	def _configure_database_check(self, temp_dir):
		for name in ('navigator_db', 'watched_db', 'views_db', 'trakt_db', 'maincache_db', 'metacache_db', 'debridcache_db', 'external_db'):
			setattr(self.cache, name, str(Path(temp_dir) / ('%s.db' % name)))
		self.cache.databases_path = temp_dir
		self.cache.database_connect = sqlite3.connect
		self.cache.kodi_utils.path_exists = lambda path: True
		self.cache.kodi_utils.make_directory = lambda path: None

	def _external_id_result(self, id_column, media_id, reverse_unordered_selects=False):
		with sqlite3.connect(self.cache.metacache_db) as dbcon:
			dbcon.execute('PRAGMA reverse_unordered_selects = %s' % ('ON' if reverse_unordered_selects else 'OFF'))
			return dbcon.execute(self.meta_cache.GET_MOVIE_SHOW % id_column, ('tvshow', media_id, 0)).fetchone()[0]

	def _metadata_indexes(self):
		with sqlite3.connect(self.cache.metacache_db) as dbcon:
			return {name: sql for name, sql in dbcon.execute("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'metadata'")}


	def test_database_check_clears_watched_history_but_preserves_resume_and_dropped_rows(self):
		with tempfile.TemporaryDirectory() as temp_dir:
			self._configure_database_check(temp_dir)
			self.cache.check_databases()
			with sqlite3.connect(self.cache.watched_db) as dbcon:
				dbcon.execute('INSERT INTO watched_status VALUES (?, ?, ?, ?, ?, ?)', ('movie', '101', 0, 0, '2026-08-23', 'Completed'))
				dbcon.execute('INSERT INTO progress VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', ('episode', '202', 1, 2, '45', '1800', '2026-08-23', 0, 'Paused'))
				dbcon.execute('INSERT INTO dropped VALUES (?, ?, ?)', ('tvshow', '303', 'Dropped'))

			self.cache.check_databases()

			with sqlite3.connect(self.cache.watched_db) as dbcon:
				self.assertEqual(dbcon.execute('SELECT * FROM watched_status').fetchall(), [])
				self.assertEqual(dbcon.execute('SELECT db_type, media_id, season, episode FROM progress').fetchall(), [('episode', '202', 1, 2)])
				self.assertEqual(dbcon.execute('SELECT db_type, tmdb_id FROM dropped').fetchall(), [('tvshow', '303')])


	def test_purge_database_deletes_expired_rows_from_all_tables_and_vacuums_once(self):
		with tempfile.TemporaryDirectory() as temp_dir:
			database_path = str(Path(temp_dir) / 'cache.db')
			with sqlite3.connect(database_path) as dbcon:
				for table in ('function_cache', 'season_metadata', 'metadata'):
					dbcon.execute('CREATE TABLE %s (expires INTEGER)' % table)
					dbcon.executemany('INSERT INTO %s VALUES (?)' % table, ((9,), (10,), (11,)))
			statements = []
			def connect(path):
				dbcon = sqlite3.connect(path)
				dbcon.set_trace_callback(statements.append)
				return dbcon
			self.cache.database_connect = connect

			self.cache.purge_database(database_path, ('function_cache', 'season_metadata', 'metadata'), 10)

			with sqlite3.connect(database_path) as dbcon:
				for table in ('function_cache', 'season_metadata', 'metadata'):
					self.assertEqual(dbcon.execute('SELECT expires FROM %s' % table).fetchall(), [(11,)])
			self.assertEqual(sum(statement.strip().upper() == 'VACUUM' for statement in statements), 1)


if __name__ == '__main__':
	unittest.main()
