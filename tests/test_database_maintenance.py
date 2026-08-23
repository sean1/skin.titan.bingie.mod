import json
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path

from tests.module_isolation import load_module, temporary_modules


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

	def test_menu_normalization_hides_watched_pages_adds_dropped_shows_and_is_idempotent(self):
		class NavigatorCache:
			def __init__(self):
				self.dbcon = sqlite3.connect(':memory:')
				self.dbcur = self.dbcon.cursor()
				self.dbcur.execute('CREATE TABLE navigator (list_name TEXT, list_type TEXT, list_contents TEXT, UNIQUE (list_name, list_type))')
				rows = (
					('RootList', 'default', [{'action': 'in_progress_movies'}]),
					('MovieList', 'default', [{'action': 'watched_movies'}, {'action': 'in_progress_movies'}]),
					('TVShowList', 'edited', [{'action': 'watched_tvshows'}, {'action': 'in_progress_tvshows'}, {'mode': 'build_next_episode'}, {'action': 'tmdb_tv_popular'}]),
					('Keep Watching', 'shortcut_folder', [{'action': 'watched_movies'}, {'action': 'navigator.because_you_watched'}])
				)
				self.dbcur.executemany('INSERT INTO navigator VALUES (?, ?, ?)', ((name, list_type, json.dumps(items)) for name, list_type, items in rows))

			jsloads = staticmethod(json.loads)
			jsdumps = staticmethod(json.dumps)

			def set_list(self, list_name, list_type, items):
				self.dbcur.execute('INSERT OR REPLACE INTO navigator VALUES (?, ?, ?)', (list_name, list_type, self.jsdumps(items)))

		navigator_cache = NavigatorCache()
		self.addCleanup(navigator_cache.dbcon.close)
		self.cache.kodi_utils.logger = lambda *args: None
		navigator_cache_module = types.ModuleType('caches.navigator_cache')
		navigator_cache_module.navigator_cache = navigator_cache
		menu_lists = types.ModuleType('modules.menu_lists')
		menu_lists.root_list = [{'action': 'dropped_tvshows', 'name': 'Dropped Shows'}]

		with temporary_modules({'caches.navigator_cache': navigator_cache_module, 'modules.menu_lists': menu_lists}):
			self.assertTrue(self.cache.normalize_menu_data())
			first_contents = list(navigator_cache.dbcur.execute('SELECT list_name, list_type, list_contents FROM navigator ORDER BY list_name, list_type'))
			self.assertTrue(self.cache.normalize_menu_data())
			second_contents = list(navigator_cache.dbcur.execute('SELECT list_name, list_type, list_contents FROM navigator ORDER BY list_name, list_type'))

		contents = {(name, list_type): json.loads(items) for name, list_type, items in first_contents}
		self.assertEqual(first_contents, second_contents)
		self.assertEqual(contents[('MovieList', 'default')], [{'action': 'in_progress_movies'}])
		self.assertEqual(contents[('TVShowList', 'edited')], [{'action': 'tmdb_tv_popular'}])
		self.assertEqual(contents[('Keep Watching', 'shortcut_folder')], [])
		self.assertEqual(contents[('RootList', 'default')], [{'action': 'in_progress_movies'}, {'action': 'dropped_tvshows', 'name': 'Dropped Shows'}])

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

	def test_metacache_external_id_indexes_support_stable_lookup_and_schema_is_idempotent(self):
		with tempfile.TemporaryDirectory() as temp_dir:
			self._configure_database_check(temp_dir)
			self.cache.check_databases()
			with sqlite3.connect(self.cache.metacache_db) as dbcon:
				dbcon.executemany('INSERT INTO metadata VALUES (?, ?, ?, ?, ?, ?)', (
					('tvshow', 'z-tmdb', 'duplicate-imdb', 'duplicate-tvdb', 4102444800, 'z-meta'),
					('tvshow', 'a-tmdb', 'duplicate-imdb', 'duplicate-tvdb', 4102444800, 'a-meta')
				))

			indexes = self._metadata_indexes()
			self.assertEqual(indexes['pov_select_imdb_media'], 'CREATE INDEX pov_select_imdb_media ON metadata (db_type, imdb_id, tmdb_id)')
			self.assertEqual(indexes['pov_select_tvdb_media'], 'CREATE INDEX pov_select_tvdb_media ON metadata (db_type, tvdb_id, tmdb_id)')
			with sqlite3.connect(self.cache.metacache_db) as dbcon:
				for id_column, index_name in (('imdb_id', 'pov_select_imdb_media'), ('tvdb_id', 'pov_select_tvdb_media')):
					plan = dbcon.execute('EXPLAIN QUERY PLAN ' + self.meta_cache.GET_MOVIE_SHOW % id_column, ('tvshow', 'missing', 0)).fetchone()[3]
					self.assertIn('USING INDEX %s' % index_name, plan)
			self.assertEqual(self._external_id_result('imdb_id', 'duplicate-imdb'), 'a-meta')
			self.assertEqual(self._external_id_result('tvdb_id', 'duplicate-tvdb'), 'a-meta')
			self.assertEqual(self._external_id_result('imdb_id', 'duplicate-imdb', reverse_unordered_selects=True), 'a-meta')
			self.assertEqual(self._external_id_result('tvdb_id', 'duplicate-tvdb', reverse_unordered_selects=True), 'a-meta')
			with sqlite3.connect(self.cache.metacache_db) as dbcon:
				schema_version = dbcon.execute('PRAGMA schema_version').fetchone()[0]

			self.cache.check_databases()

			self.assertEqual(self._metadata_indexes(), indexes)
			self.assertEqual(self._external_id_result('imdb_id', 'duplicate-imdb'), 'a-meta')
			self.assertEqual(self._external_id_result('tvdb_id', 'duplicate-tvdb'), 'a-meta')
			with sqlite3.connect(self.cache.metacache_db) as dbcon:
				self.assertEqual(dbcon.execute('PRAGMA schema_version').fetchone()[0], schema_version)

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

	def test_clean_databases_preserves_targets_and_followup_work(self):
		calls = []
		self.cache.check_databases = lambda: calls.append(('check',))
		self.cache.purge_database = lambda db, tables, expiry: calls.append(('purge', db, tables, expiry))
		class WatchedConnection:
			def execute(self, query): calls.append(('watched_execute', query.strip()))
			def close(self): calls.append(('watched_close',))
		self.cache.database_connect = lambda db, isolation_level=None: WatchedConnection()
		self.cache.limit_metacache_database = lambda: calls.append(('limit',))
		self.cache.kodi_utils.notification = lambda *args: calls.append(('notification', *args))

		self.cache.clean_databases(current_time=123, database_check=True, silent=False)

		self.assertEqual([call for call in calls if call[0] == 'purge'], [
			('purge', 'maincache_db', ('maincache',), 123),
			('purge', 'external_db', ('results_data',), 123),
			('purge', 'debridcache_db', ('debrid_data',), 123),
			('purge', 'metacache_db', ('function_cache', 'season_metadata', 'metadata'), 123)
		])
		self.assertEqual(calls[0], ('check',))
		self.assertIn(('watched_execute', 'VACUUM'), calls)
		self.assertIn(('watched_close',), calls)
		self.assertIn(('limit',), calls)
		self.assertEqual(calls[-1], ('notification', 32576, 1500))


if __name__ == '__main__':
	unittest.main()
