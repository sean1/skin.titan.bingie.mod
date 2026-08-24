import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_tmdb_module():
	main_cache = types.ModuleType('caches.main_cache')
	main_cache.cache_object = Mock(side_effect=lambda function, key, url, **kwargs: {'key': key, 'url': url, **kwargs})
	meta_cache = types.ModuleType('caches.meta_cache')
	meta_cache.cache_function = lambda *args, **kwargs: (lambda function: function)
	caches = types.ModuleType('caches')
	caches.main_cache = main_cache
	caches.meta_cache = meta_cache
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.get_setting = lambda key: ''
	kodi_utils.local_string = str
	kodi_utils.logger = Mock()
	settings = types.ModuleType('modules.settings')
	settings.get_language = lambda: 'en-US'
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	modules.settings = settings
	stubs = {
		'caches': caches, 'caches.main_cache': main_cache, 'caches.meta_cache': meta_cache,
		'modules': modules, 'modules.kodi_utils': kodi_utils, 'modules.settings': settings
	}
	return load_module('test_tmdb_search_params_module', ROOT / 'resources' / 'lib' / 'indexers' / 'tmdb_api.py', stubs), main_cache.cache_object


class TmdbSearchParameterTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.tmdb, cls.cache_object = load_tmdb_module()

	def setUp(self):
		self.cache_object.reset_mock()

	def test_get_tmdb_forwards_optional_request_parameters(self):
		response = Mock(headers={'Content-Type': 'application/json'}, ok=True)
		response.json.return_value = {'results': []}
		self.tmdb.session = Mock()
		self.tmdb.session.get.return_value = response

		result = self.tmdb.get_tmdb('https://api.example/search', {'query': 'A&B + #1'})

		self.assertEqual(result, {'results': []})
		self.tmdb.session.get.assert_called_once_with(
			'https://api.example/search', headers={'Authorization': 'Bearer '}, params={'query': 'A&B + #1'}, timeout=self.tmdb.timeout
		)

	def test_get_tmdb_keeps_non_search_request_signature_unchanged(self):
		response = Mock(headers={'Content-Type': 'application/json'}, ok=True)
		response.json.return_value = {'id': 1}
		self.tmdb.session = Mock()
		self.tmdb.session.get.return_value = response

		result = self.tmdb.get_tmdb('https://api.example/movie/1')

		self.assertEqual(result, {'id': 1})
		self.tmdb.session.get.assert_called_once_with(
			'https://api.example/movie/1', headers={'Authorization': 'Bearer '}, timeout=self.tmdb.timeout
		)

	def test_seven_media_search_builders_keep_cache_keys_and_separate_parameters(self):
		query = 'A&B + #1'
		cases = (
			(self.tmdb.tmdb_keyword_id, (query,), 'tmdb_keyword_id_%s' % query, '/search/keyword', {'query': query}),
			(self.tmdb.tmdb_company_id, (query,), 'tmdb_company_id_%s' % query, '/search/company', {'query': query}),
			(self.tmdb.tmdb_movies_title_year, (query, 2024), 'tmdb_movies_title_year_%s_2024' % query, '/search/movie', {'language': 'en-US', 'query': query, 'year': 2024}),
			(self.tmdb.tmdb_movies_search, (query, 3), 'tmdb_movies_search_%s_3' % query, '/search/movie', {'language': 'en-US', 'query': query, 'page': 3}),
			(self.tmdb.tmdb_movies_search_collections, (query, 4), 'tmdb_movies_search_collections_%s_4' % query, '/search/collection', {'language': 'en-US', 'query': query, 'page': 4}),
			(self.tmdb.tmdb_tv_title_year, (query, 2023), 'tmdb_tv_title_year_%s_2023' % query, '/search/tv', {'query': query, 'first_air_date_year': 2023, 'language': 'en-US'}),
			(self.tmdb.tmdb_tv_search, (query, 5), 'tmdb_tv_search_%s_5' % query, '/search/tv', {'language': 'en-US', 'query': query, 'page': 5})
		)

		for function, args, expected_key, expected_path, expected_params in cases:
			with self.subTest(function=function.__name__):
				result = function(*args)
				self.assertEqual(result['key'], expected_key)
				self.assertEqual(result['url'], [self.tmdb.base_url + expected_path, expected_params])
				self.assertNotIn(query, result['url'][0])

	def test_people_search_keeps_cache_key_and_separates_query_parameters(self):
		query = 'A&B + #1'
		previous_side_effect = self.cache_object.side_effect
		self.cache_object.side_effect = lambda *_args, **_kwargs: {'results': [{'id': 1}]}
		self.addCleanup(setattr, self.cache_object, 'side_effect', previous_side_effect)

		self.assertEqual(self.tmdb.tmdb_people_info(query), [{'id': 1}])
		self.cache_object.assert_called_once_with(
			self.tmdb.get_tmdb, 'tmdb_people_info_%s' % query,
			[self.tmdb.base_url + '/search/person', {'language': 'en-US', 'query': query}], expiration=self.tmdb.EXPIRES_4_HOURS
		)

	def test_non_search_builders_retain_single_url_cache_calls(self):
		result = self.tmdb.tmdb_movies_popular(2)

		self.assertEqual(result['key'], 'tmdb_movies_popular_2')
		self.assertEqual(result['url'], self.tmdb.base_url + '/movie/popular?language=en-US&page=2')


if __name__ == '__main__':
	unittest.main()
