import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock


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
	previous = {name: sys.modules.get(name) for name in stubs}
	sys.modules.update(stubs)
	try:
		path = ROOT / 'resources' / 'lib' / 'indexers' / 'metadata.py'
		spec = importlib.util.spec_from_file_location('test_metadata_lifecycle_module', path)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
	finally:
		for name, old_module in previous.items():
			if old_module is None: sys.modules.pop(name, None)
			else: sys.modules[name] = old_module
	return module


class MetadataLifecycleTests(unittest.TestCase):
	def setUp(self):
		self.metadata = load_metadata()
		self.cache = Mock()
		self.metadata.MetaCache = Mock(return_value=self.cache)

	def test_normalizes_trakt_ids_in_declared_priority_order(self):
		media_ids = {'tmdb': 101, 'imdb': 'tt0101', 'tvdb': 202}

		self.assertEqual(self.metadata._normalize_media_id('trakt_dict', media_ids, ('tmdb', 'imdb')), ('tmdb_id', 101))
		self.assertEqual(self.metadata._normalize_media_id('trakt_dict', {'imdb': 'tt0101'}, ('tmdb', 'imdb')), ('imdb_id', 'tt0101'))
		self.assertEqual(self.metadata._normalize_media_id('trakt_dict', {}, ('tmdb', 'imdb')), (None, None))

	def test_movie_public_path_normalizes_and_delegates_to_shared_lifecycle(self):
		self.metadata._cached_meta = Mock(return_value={'tmdb_id': 101})

		result = self.metadata.movie_meta('trakt_dict', {'tmdb': 101, 'imdb': 'tt0101'}, {'language': 'en'}, 'today')

		self.assertEqual(result, {'tmdb_id': 101})
		args = self.metadata._cached_meta.call_args.args
		self.assertEqual(args[:3], ('movie', 'tmdb_id', 101))
		self.assertIs(args[4], self.metadata._set_best_trailer)

	def test_tvshow_public_path_normalizes_and_delegates_to_shared_lifecycle(self):
		self.metadata._cached_meta = Mock(return_value={'tvdb_id': 202})

		result = self.metadata.tvshow_meta('trakt_dict', {'tvdb': 202}, {'language': 'en'}, 'today')

		self.assertEqual(result, {'tvdb_id': 202})
		args = self.metadata._cached_meta.call_args.args
		self.assertEqual(args[:3], ('tvshow', 'tvdb_id', 202))
		self.assertTrue(callable(args[4]))

	def test_cache_hit_is_prepared_without_claiming(self):
		cached = {'tmdb_id': 101}
		prepare = Mock(return_value={'prepared': True})
		fetch = Mock()
		self.cache.get.return_value = cached

		result = self.metadata._cached_meta('movie', 'tmdb_id', 101, fetch, prepare)

		self.assertEqual(result, {'prepared': True})
		prepare.assert_called_once_with(cached)
		self.cache.get_or_claim.assert_not_called()
		fetch.assert_not_called()

	def test_claimed_cache_value_is_prepared_without_fetching(self):
		cached = {'tmdb_id': 101}
		prepare = Mock(return_value={'prepared': True})
		fetch = Mock()
		self.cache.get.return_value = None
		self.cache.get_or_claim.return_value = (cached, 'other-owner', False)

		result = self.metadata._cached_meta('tvshow', 'tmdb_id', 101, fetch, prepare)

		self.assertEqual(result, {'prepared': True})
		prepare.assert_called_once_with(cached)
		fetch.assert_not_called()
		self.cache.release_claim.assert_not_called()

	def test_blocked_claim_returns_none_without_release(self):
		self.cache.get.return_value = None
		self.cache.get_or_claim.return_value = (None, 'other-owner', False)
		fetch = Mock()

		self.assertIsNone(self.metadata._cached_meta('movie', 'tmdb_id', 101, fetch))
		fetch.assert_not_called()
		self.cache.release_claim.assert_not_called()

	def test_fetch_result_releases_owned_claim(self):
		self.cache.get.return_value = None
		self.cache.get_or_claim.return_value = (None, 'owner-1', True)
		fetch = Mock(return_value={'tmdb_id': 101})

		result = self.metadata._cached_meta('movie', 'tmdb_id', 101, fetch)

		self.assertEqual(result, {'tmdb_id': 101})
		fetch.assert_called_once_with(self.cache, 'owner-1')
		self.cache.release_claim.assert_called_once_with('movie', 'tmdb_id', 101, 'owner-1')

	def test_fetch_error_still_releases_owned_claim(self):
		self.cache.get.return_value = None
		self.cache.get_or_claim.return_value = (None, 'owner-1', True)
		fetch = Mock(side_effect=RuntimeError('fetch failed'))

		with self.assertRaises(RuntimeError): self.metadata._cached_meta('tvshow', 'tmdb_id', 101, fetch)

		self.cache.release_claim.assert_called_once_with('tvshow', 'tmdb_id', 101, 'owner-1')

	def test_failed_renewal_returns_prepared_cached_value(self):
		cached = {'tmdb_id': 101}
		prepare = Mock(return_value={'prepared': True})
		self.cache.renew_claim.return_value = False
		self.cache.get.return_value = cached

		result = self.metadata._renew_metadata_claim(self.cache, 'tvshow', 'imdb_id', 'tt0101', 'owner-1', prepare)

		self.assertEqual(result, (False, {'prepared': True}))
		prepare.assert_called_once_with(cached)


if __name__ == '__main__':
	unittest.main()
