import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module, temporary_modules


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'resources' / 'lib'


def load_mylist_cache(database_path):
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.watched_db = str(database_path)
	kodi_utils.database_connect = sqlite3.connect
	kodi_utils.logger = Mock()
	settings = types.ModuleType('modules.settings')
	settings.paginate = Mock(return_value=True)
	settings.page_limit = Mock(return_value=2)
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	modules.settings = settings
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils, 'modules.settings': settings}
	return load_module('test_mylist_cache_module', LIB / 'caches' / 'mylist_cache.py', stubs), stubs


class MyListCacheTests(unittest.TestCase):
	def setUp(self):
		self.temp_dir = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp_dir.cleanup)
		self.database_path = Path(self.temp_dir.name) / 'watched.db'
		self.cache, self.stubs = load_mylist_cache(self.database_path)
		self.store = self.cache.MyList(str(self.database_path))

	def items(self, mediatype='movie', **kwargs):
		return self.store.items(mediatype, **kwargs)[0]

	def test_save_and_remove_persist_across_store_recreation(self):
		self.assertTrue(self.store.add('movie', 101, 'Movie'))
		reopened = self.cache.MyList(str(self.database_path))
		self.assertTrue(reopened.contains('movie', 101))
		self.assertEqual(reopened.items('movie')[0][0]['title'], 'Movie')
		self.assertTrue(reopened.remove('movie', 101))
		self.assertFalse(self.cache.MyList(str(self.database_path)).contains('movie', 101))

	def test_movies_and_shows_with_the_same_tmdb_id_are_independent(self):
		self.assertTrue(self.store.add('movie', 101, 'Movie'))
		self.assertTrue(self.store.add('tvshow', 101, 'Show'))
		self.assertEqual([(item['media_id'], item['title']) for item in self.items('movie')], [('101', 'Movie')])
		self.assertEqual([(item['media_id'], item['title']) for item in self.items('tvshow')], [('101', 'Show')])
		self.assertTrue(self.store.remove('movie', 101))
		self.assertFalse(self.store.contains('movie', 101))
		self.assertTrue(self.store.contains('tvshow', 101))

	def test_repeated_add_preserves_a_single_row_and_saved_order(self):
		self.assertTrue(self.store.add('movie', 101, 'First'))
		self.assertTrue(self.store.add('movie', 102, 'Second'))
		before = self.items()
		self.assertEqual([item['media_id'] for item in before], ['102', '101'])
		self.assertTrue(self.store.add('movie', '101', 'First'))
		self.assertEqual(self.items(), before)
		self.assertTrue(self.store.contains('movie', 101))
		self.assertTrue(all(isinstance(item['saved_at'], int) for item in before))

	def test_invalid_identities_are_rejected_without_modifying_saved_items(self):
		self.store.add('movie', 101, 'Keep')
		before = self.items()
		for invalid_id in ('', None, 'not-a-number', '1.5', 1.5, 0, '0', -1, '-1', True, False):
			for operation in (self.store.add, self.store.remove, self.store.contains):
				with self.subTest(operation=operation.__name__, media_id=invalid_id):
					with self.assertRaises(ValueError): operation('movie', invalid_id)
		for mediatype in ('', None, 'actor', 'collection'):
			with self.subTest(mediatype=mediatype):
				with self.assertRaises(ValueError): self.store.add(mediatype, 202)
		self.assertEqual(self.items(), before)

	def test_invalid_explicit_series_parent_does_not_fall_back_to_the_episode_id(self):
		for parent_id in ('', 0, 'bad', -1, True):
			with self.subTest(parent_id=parent_id):
				with self.assertRaises(ValueError): self.store.add('episode', 9876, 'Episode', tvshow_id=parent_id)
		self.assertEqual(self.items('tvshow'), [])

	def test_separate_views_are_stably_paginated_newest_first(self):
		for media_id in range(101, 106): self.store.add('movie', media_id, 'Movie %s' % media_id)
		self.store.add('tvshow', 106, 'Show')
		first, pages = self.store.items('movie', page=1, limit=2)
		second, second_pages = self.store.items('movie', page=2, limit=2)
		last, last_pages = self.store.items('movie', page=3, limit=2)
		self.assertEqual((pages, second_pages, last_pages), (3, 3, 3))
		self.assertEqual([item['media_id'] for item in first + second + last], ['105', '104', '103', '102', '101'])
		self.assertEqual([item['media_id'] for item in self.items('tvshow')], ['106'])
		self.assertEqual(self.store.items('movie', limit=None)[1], 1)
		self.assertEqual(len(self.items('movie', limit=None)), 5)

	def test_database_initialization_and_cleanup_preserve_saved_items(self):
		kodi_utils = self.stubs['modules.kodi_utils']
		for name in ('navigator_db', 'watched_db', 'views_db', 'trakt_db', 'maincache_db', 'metacache_db', 'debridcache_db', 'external_db'):
			setattr(kodi_utils, name, str(Path(self.temp_dir.name) / ('watched.db' if name == 'watched_db' else '%s.db' % name)))
		kodi_utils.databases_path = self.temp_dir.name
		kodi_utils.local_string = str
		kodi_utils.path_exists = lambda path: True
		kodi_utils.make_directory = Mock()
		kodi_utils.open_file = Mock()
		caches = types.ModuleType('caches')
		caches.__path__ = []
		stubs = {**self.stubs, 'caches': caches, 'caches.mylist_cache': self.cache}
		maintenance = load_module('test_mylist_maintenance_module', LIB / 'modules' / 'cache.py', stubs)
		maintenance.limit_metacache_database = Mock()
		self.store.add('movie', 101, 'Movie')
		self.store.add('tvshow', 101, 'Show')
		with temporary_modules(stubs):
			maintenance.check_databases()
			maintenance.check_databases()
			maintenance.clean_databases(current_time=100, silent=True)
		reopened = self.cache.MyList(str(self.database_path))
		self.assertEqual([(item['media_id'], item['title']) for item in reopened.items('movie')[0]], [('101', 'Movie')])
		self.assertEqual([(item['media_id'], item['title']) for item in reopened.items('tvshow')[0]], [('101', 'Show')])


if __name__ == '__main__':
	unittest.main()
