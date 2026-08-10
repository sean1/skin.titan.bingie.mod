import importlib.util
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path


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
	old_modules = {name: sys.modules.get(name) for name in ('modules', 'modules.kodi_utils')}
	sys.modules['modules'] = modules
	sys.modules['modules.kodi_utils'] = kodi_utils
	try:
		path = ROOT / 'resources' / 'lib' / 'modules' / 'cache.py'
		spec = importlib.util.spec_from_file_location('test_database_maintenance_cache', path)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
	finally:
		for name, old_module in old_modules.items():
			if old_module is None: sys.modules.pop(name, None)
			else: sys.modules[name] = old_module
	return module


class DatabaseMaintenanceTests(unittest.TestCase):
	def setUp(self):
		self.cache = load_cache_module()

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


if __name__ == '__main__':
	unittest.main()
