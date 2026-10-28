import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


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


if __name__ == '__main__':
	unittest.main()
