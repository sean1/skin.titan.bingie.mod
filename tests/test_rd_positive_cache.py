import sqlite3
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


class FakeDebridCache:
	writes = []

	def __enter__(self): return self
	def __exit__(self, *_args): pass
	def get_many(self, _hashes): return []
	def set_many(self, hashes, debrid): self.writes.append((tuple(hashes), debrid))


def load_debrid_module():
	class FakeAPI:
		def check_cache(self, hashes): return {item: False for item in hashes}

	debrids = types.ModuleType('debrids')
	debrids.__path__ = []
	api_modules = {}
	for name in ('all_debrid_api', 'real_debrid_api', 'torbox_api'):
		module = types.ModuleType('debrids.%s' % name)
		setattr(module, {'all_debrid_api': 'AllDebridAPI', 'real_debrid_api': 'RealDebridAPI', 'torbox_api': 'TorBoxAPI'}[name], FakeAPI)
		setattr(debrids, name, module)
		api_modules['debrids.%s' % name] = module
	caches = types.ModuleType('caches')
	caches.__path__ = []
	debrid_cache = types.ModuleType('caches.debrid_cache')
	debrid_cache.DebridCache = FakeDebridCache
	metadata = types.ModuleType('indexers.metadata')
	indexers = types.ModuleType('indexers')
	indexers.__path__ = []
	indexers.metadata = metadata
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.local_string = str
	kodi_utils.get_setting = lambda *_args: 'false'
	kodi_utils.notification = lambda *_args: None
	kodi_utils.logger = lambda *_args: None
	for name in ('show_busy_dialog', 'hide_busy_dialog', 'ok_dialog', 'confirm_dialog', 'select_dialog'):
		setattr(kodi_utils, name, lambda *_args, **_kwargs: None)
	settings = types.ModuleType('modules.settings')
	settings.default_internal_scrapers = ()
	settings.enabled_debrids_check = lambda _name: True
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	modules.settings = settings
	fenom = types.ModuleType('fenom')
	fenom.__path__ = []
	client = types.ModuleType('fenom.client')
	client.randomagent = lambda: 'test-agent'
	stubs = {
		'debrids': debrids, 'caches': caches, 'caches.debrid_cache': debrid_cache, 'indexers': indexers, 'indexers.metadata': metadata,
		'modules': modules, 'modules.kodi_utils': kodi_utils, 'modules.settings': settings, 'fenom': fenom, 'fenom.client': client, **api_modules
	}
	return load_module('test_rd_positive_cache_debrid', ROOT / 'resources/lib/modules/debrid.py', stubs)


class MemoryBaseCache:
	db_file = ':memory:'

	def __init__(self):
		self.dbcon = sqlite3.connect(':memory:')
		self.dbcur = self.dbcon.cursor()
		self.dbcur.execute('CREATE TABLE debrid_data (hash TEXT NOT NULL, debrid TEXT NOT NULL, cached TEXT, expires INTEGER, UNIQUE(hash, debrid))')

	def _get_timestamp(self, value): return int(value.timestamp())
	def __enter__(self): return self
	def __exit__(self, *_args): self.dbcon.close()


def load_cache_module():
	caches = types.ModuleType('caches')
	caches.BaseCache = MemoryBaseCache
	caches.debridcache_db = ':memory:'
	return load_module('test_rd_positive_cache_database', ROOT / 'resources/lib/caches/debrid_cache.py', {'caches': caches})


class RealDebridPositiveCacheTests(unittest.TestCase):
	def setUp(self):
		FakeDebridCache.writes = []
		self.module = load_debrid_module()

	def test_rd_legacy_false_is_rechecked_and_only_positive_is_persisted(self):
		checker = self.module.DebridCheck({'imdb_id': 'tt1'}, 'realdebrid', ('positive', 'missing'), (
			('positive', 'rd', 'False', 9999999999), ('missing', 'rd', 'False', 9999999999)
		))
		checker.external_check_cache = mock.Mock(return_value=['positive'])

		self.assertEqual(checker.cache_check(), ['positive'])
		checker.external_check_cache.assert_called_once_with(['positive', 'missing'])
		self.assertEqual(FakeDebridCache.writes, [((('positive', 'True'),), 'rd')])

	def test_ad_exact_negative_remains_authoritative(self):
		checker = self.module.DebridCheck({}, 'alldebrid', ('missing',), (('missing', 'ad', 'False', 9999999999),))

		self.assertEqual(checker.cache_check(), {'cached': [], 'checked': {'missing'}})
		self.assertEqual(FakeDebridCache.writes, [])

	def test_successful_empty_auxiliary_check_is_briefly_reused(self):
		calls = []
		check = lambda _collector: calls.append(True) or True

		self.assertEqual(self.module._coalesced_auxiliary_results(('dmm', 'same'), check), [])
		self.assertEqual(self.module._coalesced_auxiliary_results(('dmm', 'same'), check), [])
		self.assertEqual(len(calls), 1)

	def test_failed_auxiliary_check_is_not_reused(self):
		calls = []
		check = lambda _collector: calls.append(True) and False

		self.module._coalesced_auxiliary_results(('dmm', 'failure'), check)
		self.module._coalesced_auxiliary_results(('dmm', 'failure'), check)
		self.assertEqual(len(calls), 2)

	def test_concurrent_identical_auxiliary_checks_are_coalesced(self):
		started, release, calls, results = threading.Event(), threading.Event(), [], []

		def check(collector):
			calls.append(True)
			started.set()
			release.wait(2)
			collector.append('cached-hash')
			return True

		first = threading.Thread(target=lambda: results.append(self.module._coalesced_auxiliary_results(('torrentio', 'same'), check)))
		second = threading.Thread(target=lambda: results.append(self.module._coalesced_auxiliary_results(('torrentio', 'same'), check)))
		first.start()
		self.assertTrue(started.wait(1))
		second.start()
		release.set()
		first.join(2)
		second.join(2)
		self.assertEqual(len(calls), 1)
		self.assertEqual(results, [['cached-hash'], ['cached-hash']])

	def test_torrentio_http_failure_is_not_reused(self):
		class FailedResponse:
			def raise_for_status(self): raise RuntimeError('503')
			def json(self): return {'streams': []}

		class Session:
			calls = 0
			def get(self, *_args, **_kwargs):
				self.calls += 1
				return FailedResponse()

		self.module.session = Session()
		self.assertEqual(self.module._tio_results('tt1', None, None), [])
		self.assertEqual(self.module._tio_results('tt1', None, None), [])
		self.assertEqual(self.module.session.calls, 2)

	def test_auxiliary_checks_reject_non_list_result_schemas(self):
		class Response:
			def __init__(self, payload): self.payload = payload
			def raise_for_status(self): return None
			def json(self): return self.payload

		class Session:
			def get(self, *_args, **_kwargs): return Response({'streams': {}})
			def post(self, *_args, **_kwargs): return Response({'available': 'not-a-list'})

		self.module.session = Session()
		self.assertFalse(self.module.tio_check_cache('tt1', None, None, []))
		fake_dmm = types.ModuleType('magneto.dmm')
		fake_dmm.get_secret = lambda: ('key', 'solution')
		with mock.patch.dict('sys.modules', {'magneto.dmm': fake_dmm}):
			self.assertFalse(self.module.dmm_check_cache(('a' * 40,), 'tt1', []))

	def test_dmm_rejects_list_entries_without_valid_hashes(self):
		class Response:
			def raise_for_status(self): return None
			def json(self): return {'available': [{}]}

		class Session:
			def post(self, *_args, **_kwargs): return Response()

		self.module.session = Session()
		fake_dmm = types.ModuleType('magneto.dmm')
		fake_dmm.get_secret = lambda: ('key', 'solution')
		with mock.patch.dict('sys.modules', {'magneto.dmm': fake_dmm}):
			self.assertFalse(self.module.dmm_check_cache(('a' * 40,), 'tt1', []))

	def test_positive_upsert_replaces_legacy_false_row(self):
		cache_module = load_cache_module()
		cache = cache_module.DebridCache()
		cache.set_many((('same-hash', 'False'),), 'rd')
		cache.set_many((('same-hash', 'True'),), 'rd')

		self.assertEqual(cache.dbcur.execute("SELECT cached FROM debrid_data WHERE hash = 'same-hash' AND debrid = 'rd'").fetchone(), ('True',))

	def test_late_negative_cannot_overwrite_an_unexpired_positive(self):
		cache_module = load_cache_module()
		cache = cache_module.DebridCache()
		cache.set_many((('same-hash', 'True'),), 'ad')
		cache.set_many((('same-hash', 'False'),), 'ad')

		self.assertEqual(cache.dbcur.execute("SELECT cached FROM debrid_data WHERE hash = 'same-hash' AND debrid = 'ad'").fetchone(), ('True',))


if __name__ == '__main__':
	unittest.main()
