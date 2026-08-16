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
		'navigator_db', 'watched_db', 'views_db', 'trakt_db', 'maincache_db', 'metacache_db', 'debridcache_db', 'external_db', 'databases_path', 'packages_path'
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
		self.cache.kodi_utils.get_setting = lambda setting: 'true'
		self.cache.remove_old_databases = lambda: None

	def _create_legacy_metacache(self):
		with sqlite3.connect(self.cache.metacache_db) as dbcon:
			dbcon.execute('CREATE TABLE metadata (db_type TEXT not null, tmdb_id TEXT not null, imdb_id TEXT, tvdb_id TEXT, expires INTEGER, meta TEXT, UNIQUE (db_type, tmdb_id))')
			dbcon.execute('CREATE INDEX pov_select_id_media ON metadata (tmdb_id, db_type)')
			dbcon.executemany('INSERT INTO metadata VALUES (?, ?, ?, ?, ?, ?)', (
				('tvshow', 'z-tmdb', 'duplicate-imdb', 'duplicate-tvdb', 4102444800, 'z-meta'),
				('tvshow', 'a-tmdb', 'duplicate-imdb', 'duplicate-tvdb', 4102444800, 'a-meta')
			))

	def _external_id_result(self, id_column, media_id, reverse_unordered_selects=False):
		with sqlite3.connect(self.cache.metacache_db) as dbcon:
			dbcon.execute('PRAGMA reverse_unordered_selects = %s' % ('ON' if reverse_unordered_selects else 'OFF'))
			return dbcon.execute(self.meta_cache.GET_MOVIE_SHOW % id_column, ('tvshow', media_id, 0)).fetchone()[0]

	def _metadata_indexes(self):
		with sqlite3.connect(self.cache.metacache_db) as dbcon:
			return {name: sql for name, sql in dbcon.execute("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'metadata'")}

	def test_marker_filter_serializes_each_item_once_and_preserves_order_and_identity(self):
		kept_first = {'name': 'first'}
		removed_early = {'name': 'blocked'}
		kept_last = {'name': 'last'}
		removed_late = {'name': 'later'}
		items = [kept_first, removed_early, kept_last, removed_late]
		markers = ('blocked', 'later')
		expected = [item for item in items if all(marker not in json.dumps(item).lower() for marker in markers)]
		calls = {id(item): 0 for item in items}
		def serialize(item):
			calls[id(item)] += 1
			return json.dumps(item)

		result = self.cache._filter_items_without_markers(items, markers, serialize)

		self.assertEqual(result, expected)
		self.assertEqual(list(map(id, result)), [id(kept_first), id(kept_last)])
		self.assertEqual(calls, {id(item): 1 for item in items})

	def test_removed_list_cleanup_drops_watched_pages_from_every_saved_menu_type(self):
		with tempfile.TemporaryDirectory() as temp_dir:
			self.cache.maincache_db = str(Path(temp_dir) / 'maincache.db')
			self.cache.database_connect = sqlite3.connect
			with sqlite3.connect(self.cache.maincache_db) as dbcon:
				dbcon.execute('CREATE TABLE maincache (id TEXT UNIQUE, expires INTEGER, data TEXT)')

			class NavigatorCache:
				def __init__(self):
					self.dbcon = sqlite3.connect(':memory:')
					self.dbcur = self.dbcon.cursor()
					self.dbcur.execute('CREATE TABLE navigator (list_name TEXT, list_type TEXT, list_contents TEXT, UNIQUE (list_name, list_type))')
					rows = (
						('MovieList', 'default', [{'action': 'watched_movies'}, {'action': 'in_progress_movies'}]),
						('TVShowList', 'edited', [{'action': 'watched_tvshows'}, {'action': 'tmdb_tv_popular'}]),
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
			main_cache = types.ModuleType('caches.main_cache')
			main_cache.clear_main_cache_property = lambda key: None
			navigator_cache_module = types.ModuleType('caches.navigator_cache')
			navigator_cache_module.navigator_cache = navigator_cache

			with temporary_modules({'caches.main_cache': main_cache, 'caches.navigator_cache': navigator_cache_module}):
				self.assertTrue(self.cache.purge_removed_list_data())

			contents = {
				(name, list_type): json.loads(items)
				for name, list_type, items in navigator_cache.dbcur.execute('SELECT list_name, list_type, list_contents FROM navigator')
			}
			self.assertEqual(contents[('MovieList', 'default')], [{'action': 'in_progress_movies'}])
			self.assertEqual(contents[('TVShowList', 'edited')], [{'action': 'tmdb_tv_popular'}])
			self.assertEqual(contents[('Keep Watching', 'shortcut_folder')], [{'action': 'navigator.because_you_watched'}])

	def test_metacache_external_id_index_migration_preserves_lookup_order_and_is_idempotent(self):
		with tempfile.TemporaryDirectory() as temp_dir:
			self._configure_database_check(temp_dir)
			self._create_legacy_metacache()
			before = {
				'imdb_id': self._external_id_result('imdb_id', 'duplicate-imdb'),
				'tvdb_id': self._external_id_result('tvdb_id', 'duplicate-tvdb')
			}
			self.assertEqual(before, {'imdb_id': 'a-meta', 'tvdb_id': 'a-meta'})
			self.assertEqual(self._external_id_result('imdb_id', 'duplicate-imdb', reverse_unordered_selects=True), before['imdb_id'])
			self.assertEqual(self._external_id_result('tvdb_id', 'duplicate-tvdb', reverse_unordered_selects=True), before['tvdb_id'])

			self.cache.check_databases()

			indexes = self._metadata_indexes()
			self.assertNotIn('pov_select_id_media', indexes)
			self.assertEqual(indexes['pov_select_imdb_media'], 'CREATE INDEX pov_select_imdb_media ON metadata (db_type, imdb_id, tmdb_id)')
			self.assertEqual(indexes['pov_select_tvdb_media'], 'CREATE INDEX pov_select_tvdb_media ON metadata (db_type, tvdb_id, tmdb_id)')
			with sqlite3.connect(self.cache.metacache_db) as dbcon:
				for id_column, index_name in (('imdb_id', 'pov_select_imdb_media'), ('tvdb_id', 'pov_select_tvdb_media')):
					plan = dbcon.execute('EXPLAIN QUERY PLAN ' + self.meta_cache.GET_MOVIE_SHOW % id_column, ('tvshow', 'missing', 0)).fetchone()[3]
					self.assertIn('USING INDEX %s' % index_name, plan)
			self.assertEqual(self._external_id_result('imdb_id', 'duplicate-imdb'), before['imdb_id'])
			self.assertEqual(self._external_id_result('tvdb_id', 'duplicate-tvdb'), before['tvdb_id'])
			self.assertEqual(self._external_id_result('imdb_id', 'duplicate-imdb', reverse_unordered_selects=True), before['imdb_id'])
			self.assertEqual(self._external_id_result('tvdb_id', 'duplicate-tvdb', reverse_unordered_selects=True), before['tvdb_id'])
			with sqlite3.connect(self.cache.metacache_db) as dbcon:
				schema_version = dbcon.execute('PRAGMA schema_version').fetchone()[0]

			self.cache.check_databases()

			self.assertEqual(self._metadata_indexes(), indexes)
			self.assertEqual(self._external_id_result('imdb_id', 'duplicate-imdb'), before['imdb_id'])
			self.assertEqual(self._external_id_result('tvdb_id', 'duplicate-tvdb'), before['tvdb_id'])
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
		self.cache.remove_old_databases = lambda: calls.append(('remove_databases',))
		self.cache.remove_old_packages = lambda: calls.append(('remove_packages',))
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
		self.assertIn(('remove_databases',), calls)
		self.assertIn(('remove_packages',), calls)
		self.assertEqual(calls[-1], ('notification', 32576, 1500))

	def test_removed_service_migration_preserves_new_torbox_data(self):
		with tempfile.TemporaryDirectory() as temp_dir:
			self.cache.maincache_db = str(Path(temp_dir) / 'maincache.db')
			self.cache.debridcache_db = str(Path(temp_dir) / 'debridcache.db')
			self.cache.database_connect = sqlite3.connect
			with sqlite3.connect(self.cache.maincache_db) as dbcon:
				dbcon.execute('CREATE TABLE maincache (id TEXT UNIQUE, expires INTEGER, data TEXT)')
				dbcon.executemany('INSERT INTO maincache VALUES (?, 0, "")', (
					('pov_lite_tb_movie',), ('pov_lite_pm_movie',), ('torbox_usenet_queries',), ('keep',)
				))
			with sqlite3.connect(self.cache.debridcache_db) as dbcon:
				dbcon.execute('CREATE TABLE debrid_data (hash TEXT, debrid TEXT, cached TEXT, expires INTEGER)')
				dbcon.executemany('INSERT INTO debrid_data VALUES (?, ?, "True", 0)', (('tb-hash', 'tb'), ('pm-hash', 'pm'), ('keep-hash', 'rd')))
			settings = {}
			self.cache.kodi_utils.get_setting = lambda key: settings.get(key, '')
			self.cache.kodi_utils.set_setting = lambda key, value: settings.__setitem__(key, value) or True
			self.cache.kodi_utils.logger = lambda *args: None
			main_cache = types.ModuleType('caches.main_cache')
			main_cache.clear_main_cache_property = lambda key: None

			with temporary_modules({'caches.main_cache': main_cache}):
				self.assertTrue(self.cache.purge_removed_service_data())

			with sqlite3.connect(self.cache.maincache_db) as dbcon:
				self.assertEqual({row[0] for row in dbcon.execute('SELECT id FROM maincache')}, {'pov_lite_tb_movie', 'keep'})
			with sqlite3.connect(self.cache.debridcache_db) as dbcon:
				self.assertEqual({row[0] for row in dbcon.execute('SELECT hash FROM debrid_data')}, {'tb-hash', 'keep-hash'})
			self.assertEqual(settings['migration.removed_services.6_08_03'], 'true')


if __name__ == '__main__':
	unittest.main()
