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



if __name__ == '__main__':
	unittest.main()
