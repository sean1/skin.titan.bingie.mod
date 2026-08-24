import json
import sys
import types
import unittest
from pathlib import Path

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'resources' / 'lib'
sys.path.insert(0, str(LIB))

from modules.source_search import PROVIDER_OUTCOME_VERSION, ProviderOutcome, RequestCoalescer


class FakeCursor:
	def __init__(self, row=None):
		self.row = row
		self.executions = []

	def execute(self, query, values):
		self.executions.append((query, values))

	def fetchone(self):
		return self.row


def load_cache_module():
	class BaseCache:
		def jsloads(self, value): return json.loads(value)
		def jsdumps(self, value): return json.dumps(value)
		def _get_timestamp(self, value): return int(value.timestamp())

	caches = types.ModuleType('caches')
	caches.BaseCache = BaseCache
	caches.external_db = ':memory:'
	return load_module('test_provider_outcome_cache', LIB / 'caches' / 'providers_cache.py', {'caches': caches})


class ProviderOutcomeContractTests(unittest.TestCase):
	def test_legacy_nonempty_cache_is_success_but_legacy_empty_is_miss(self):
		self.assertEqual(ProviderOutcome.from_cache([{'hash': 'one'}]).status, 'success')
		self.assertIsNone(ProviderOutcome.from_cache([]))

	def test_versioned_empty_cache_is_a_legitimate_hit(self):
		payload = {'provider_outcome': PROVIDER_OUTCOME_VERSION, 'status': 'empty', 'sources': []}
		outcome = ProviderOutcome.from_cache(payload)
		self.assertEqual(outcome.status, 'empty')
		self.assertEqual(outcome.sources, [])

	def test_failed_and_partial_results_are_not_cacheable(self):
		module = load_cache_module()
		cache = module.ExternalProvidersCache.__new__(module.ExternalProvidersCache)
		cache.dbcur = FakeCursor()
		for status, sources in (('failed', []), ('partial', [{'hash': 'one'}])):
			with self.subTest(status=status):
				self.assertFalse(cache.set('provider', 'movie', '1', 'Title', '2024', '', '', ProviderOutcome(sources, status), 3))
		self.assertEqual(cache.dbcur.executions, [])

	def test_successful_empty_result_is_stored_with_versioned_envelope(self):
		module = load_cache_module()
		cache = module.ExternalProvidersCache.__new__(module.ExternalProvidersCache)
		cache.dbcur = FakeCursor()
		self.assertTrue(cache.set('provider', 'movie', '1', 'Title', '2024', '', '', ProviderOutcome([], 'empty'), 3))
		payload = json.loads(cache.dbcur.executions[0][1][-1])
		self.assertEqual(payload, {'provider_outcome': PROVIDER_OUTCOME_VERSION, 'status': 'empty', 'sources': []})

	def test_provider_flags_preserve_partial_sources_without_caching(self):
		provider = types.SimpleNamespace(scrape_failed=True)
		outcome = ProviderOutcome.from_provider([{'hash': 'one'}], provider)
		self.assertEqual(outcome.status, 'partial')
		self.assertEqual(outcome.sources, [{'hash': 'one'}])
		self.assertFalse(outcome.cacheable)

	def test_bundled_coalescer_can_propagate_fetch_failures(self):
		coalescer = RequestCoalescer(raise_errors=True)
		with self.assertRaises(ZeroDivisionError): coalescer.get('key', lambda: 1 / 0, 1)
		with self.assertRaises(ZeroDivisionError): coalescer.get('key', lambda: ['unused'], 1)


if __name__ == '__main__':
	unittest.main()
