import json
import sys
import types
import unittest
from pathlib import Path

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'resources' / 'lib'
sys.path.insert(0, str(LIB))

from modules.source_search import PROVIDER_OUTCOME_VERSION, ProviderOutcome


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


	def test_failed_and_partial_results_are_not_cacheable(self):
		module = load_cache_module()
		cache = module.ExternalProvidersCache.__new__(module.ExternalProvidersCache)
		cache.dbcur = FakeCursor()
		for status, sources in (('failed', []), ('partial', [{'hash': 'one'}])):
			with self.subTest(status=status):
				self.assertFalse(cache.set('provider', 'movie', '1', 'Title', '2024', '', '', ProviderOutcome(sources, status), 3))
		self.assertEqual(cache.dbcur.executions, [])


if __name__ == '__main__':
	unittest.main()
