import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_metadata():
	tmdb_api = types.ModuleType('indexers.tmdb_api')
	tmdb_api.tmdb_image_base = 'https://image.test/%s%s'
	tmdb_api.resized_tmdb_image = lambda image, resolution: image
	indexers = types.ModuleType('indexers')
	indexers.__path__ = []
	indexers.tmdb_api = tmdb_api
	meta_cache = types.ModuleType('caches.meta_cache')
	meta_cache.MetaCache = Mock
	caches = types.ModuleType('caches')
	caches.__path__ = []
	modules = types.ModuleType('modules')
	modules.__path__ = []
	utils = types.ModuleType('modules.utils')
	utils.LIST_WORKERS = 5
	utils.jsondate_to_datetime = Mock()
	utils.subtract_dates = Mock()
	utils.TaskPool = Mock
	stubs = {
		'indexers': indexers, 'indexers.tmdb_api': tmdb_api, 'caches': caches, 'caches.meta_cache': meta_cache,
		'modules': modules, 'modules.utils': utils
	}
	path = ROOT / 'resources' / 'lib' / 'indexers' / 'metadata.py'
	return load_module('test_metadata_lifecycle_module', path, stubs)


class MetadataLifecycleTests(unittest.TestCase):
	def setUp(self):
		self.metadata = load_metadata()
		self.cache = Mock()
		self.metadata.MetaCache = Mock(return_value=self.cache)


	def test_fetch_error_still_releases_owned_claim(self):
		self.cache.get.return_value = None
		self.cache.get_or_claim.return_value = (None, 'owner-1', True)
		fetch = Mock(side_effect=RuntimeError('fetch failed'))

		with self.assertRaises(RuntimeError): self.metadata._cached_meta('tvshow', 'tmdb_id', 101, fetch)

		self.cache.release_claim.assert_called_once_with('tvshow', 'tmdb_id', 101, 'owner-1')


if __name__ == '__main__':
	unittest.main()
