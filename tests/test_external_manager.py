import sys
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'resources' / 'lib'))

from modules.source_search import CORE_EXTERNAL_PROVIDERS


def module_stub(**values):
	module = types.ModuleType('stub')
	for key, value in values.items(): setattr(module, key, value)
	return module


class Progress:
	full_screen = False

	def make(self, meta): pass
	def kill(self): pass
	def update(self, *args): pass


def load_sources_module():
	progress = types.SimpleNamespace(create=lambda *args: None, update=lambda *args: None, close=lambda: None)
	stubs = {
		'magneto': module_stub(sources=lambda *args, **kwargs: []),
		'windows': module_stub(open_window=lambda *args, **kwargs: None, create_window=lambda *args, **kwargs: None),
		'caches.providers_cache': module_stub(ExternalProvidersCache=object),
		'indexers.metadata': module_stub(movie_meta=lambda *args: {}, tvshow_meta=lambda *args: {}, season_episodes_meta=lambda *args: [], get_title=lambda meta: ''),
		'modules.debrid': module_stub(debrid_enabled=lambda: [], debrid_type_enabled=lambda *args: [], Source=object, DebridCheck=object),
		'modules.player': module_stub(POVPlayer=object),
		'modules.kodi_utils': module_stub(
			progressDialogBG=progress, notification=lambda *args: None, show_busy_dialog=lambda: None, hide_busy_dialog=lambda: None, close_all_dialog=lambda: None,
			get_property=lambda *args: '', set_property=lambda *args: None, clear_property=lambda *args: None, local_string=lambda value: str(value),
			monitor=types.SimpleNamespace(abortRequested=lambda: False), sleep=time.sleep, get_setting=lambda key, fallback=None: fallback,
			translate_path=lambda path: path, scrapers_path=''
		),
		'modules.settings': module_stub(
			check_prescrape_sources=lambda *args: False, quality_filter=lambda *args: [], sort_to_top=lambda *args: False,
			results_xml_style=lambda: 'list default', results_xml_window_number=lambda *args: 2000, default_internal_scrapers=(), cloud_scrapers=(),
			display_sleep_time=lambda: 0.001
		),
		'modules.source_utils': module_stub(
			pack_enable_check=lambda *args: (False, False), sources_quality_count=lambda sources: {}, get_cache_expiry=lambda *args: (1, 1, 1),
			get_file_info=lambda *args, **kwargs: ('SD', '')
		),
		'modules.utils': module_stub(manual_function_import=lambda *args: None, get_datetime=lambda: None, safe_string=str, string_to_float=float),
	}
	path = ROOT / 'resources' / 'lib' / 'modules' / 'sources.py'
	return load_module('test_sources_runtime', path, stubs)


SOURCES = load_sources_module()


class FakeExternalSource:
	calls = []

	def __init__(self, meta, resolutions): pass

	def results(self, info, args):
		provider = args[0]
		self.calls.append(provider)
		return [
			{'source': 'torrent', 'hash': '%s-%d' % (provider, index), 'url': 'magnet:%s-%d' % (provider, index), 'quality': '1080p'}
			for index in range(2)
		]


class FakeDebridCheck:
	hash_list = []
	cached = set()
	checked_batches = []

	@classmethod
	def set_cached_hashes(cls, hash_list):
		cls.hash_list = hash_list
		cls.checked_batches.append(set(hash_list))

	def __init__(self, meta, name): pass

	def cache_check(self):
		return [item for item in self.hash_list if item in self.cached]


class RecordingExecutor:
	worker_counts = []

	def __init__(self, max_workers):
		self.worker_counts.append(max_workers)
		self.executor = ThreadPoolExecutor(max_workers)

	def submit(self, *args, **kwargs):
		return self.executor.submit(*args, **kwargs)

	def shutdown(self, *args, **kwargs):
		return self.executor.shutdown(*args, **kwargs)


class ExternalManagerTests(unittest.TestCase):
	def setUp(self):
		FakeExternalSource.calls = []
		FakeDebridCheck.cached = set()
		FakeDebridCheck.checked_batches = []
		RecordingExecutor.worker_counts = []
		SOURCES.ExternalSource = FakeExternalSource
		SOURCES.DebridCheck = FakeDebridCheck
		SOURCES.TPE = RecordingExecutor

	def manager(self, force_full_search=False, eligibility_filter=lambda results: results, provider_names=None):
		provider_names = provider_names or (*sorted(CORE_EXTERNAL_PROVIDERS), 'bitsearch', 'dmm')
		providers = [(name, object()) for name in provider_names]
		meta = {'background': True, 'search_info': {'scrape_timeout': 1}}
		return SOURCES.ExternalManager(meta, providers, ['realdebrid'], [], [], Progress(), eligibility_filter=eligibility_filter, force_full_search=force_full_search)

	def test_cached_core_target_skips_fallback(self):
		FakeDebridCheck.cached = {'%s-%d' % (provider, index) for provider in CORE_EXTERNAL_PROVIDERS for index in range(2)}
		manager = self.manager()
		results = manager.results({})
		self.assertEqual(set(FakeExternalSource.calls), CORE_EXTERNAL_PROVIDERS)
		self.assertEqual(len(results), 8)
		self.assertTrue(manager.meta['full_search_available'])

	def test_insufficient_cached_core_runs_fallback(self):
		manager = self.manager()
		results = manager.results({})
		self.assertEqual(set(FakeExternalSource.calls), CORE_EXTERNAL_PROVIDERS | {'bitsearch', 'dmm'})
		self.assertEqual(len(results), 12)
		self.assertNotIn('full_search_available', manager.meta)

	def test_force_full_search_runs_every_provider(self):
		FakeDebridCheck.cached = {'%s-%d' % (provider, index) for provider in CORE_EXTERNAL_PROVIDERS for index in range(2)}
		manager = self.manager(force_full_search=True)
		manager.results({})
		self.assertEqual(set(FakeExternalSource.calls), CORE_EXTERNAL_PROVIDERS | {'bitsearch', 'dmm'})
		self.assertNotIn('full_search_available', manager.meta)
		self.assertEqual(RecordingExecutor.worker_counts, [6])

	def test_automatic_fallback_phase_starts_every_request(self):
		fallback = tuple('fallback-%d' % index for index in range(10))
		manager = self.manager(provider_names=(*sorted(CORE_EXTERNAL_PROVIDERS), *fallback))
		manager.results({})
		self.assertEqual(RecordingExecutor.worker_counts, [4, 10])

	def test_no_core_provider_path_starts_every_request(self):
		fallback = tuple('fallback-%d' % index for index in range(10))
		manager = self.manager(provider_names=fallback)
		manager.results({})
		self.assertEqual(RecordingExecutor.worker_counts, [10])

	def test_only_eligible_hashes_are_cache_checked(self):
		FakeDebridCheck.cached = {'%s-%d' % (provider, index) for provider in CORE_EXTERNAL_PROVIDERS for index in range(2)}
		manager = self.manager(eligibility_filter=lambda results: [item for item in results if item['hash'].endswith('-0')])
		manager.results({})
		self.assertEqual(FakeDebridCheck.checked_batches[0], {'%s-0' % provider for provider in CORE_EXTERNAL_PROVIDERS})
		self.assertEqual(set(FakeExternalSource.calls), CORE_EXTERNAL_PROVIDERS | {'bitsearch', 'dmm'})

	def test_staged_filter_applies_exclusion_modes(self):
		source = types.SimpleNamespace(
			quality_filter=['1080p'], include_3D_results=True, size_filter=0,
			filter_hevc=1, filter_hdr=0, filter_dv=0, filter_av1=0, hybrid_allowed=True
		)
		processor = SOURCES.ResultsProcessor(source)
		results = [
			{'quality': '1080p', 'extraInfo': '[B]HEVC[/B]'},
			{'quality': '1080p', 'extraInfo': '[B]H.264[/B]'}
		]
		self.assertEqual(processor.filter_staged_results(results), [results[1]])


if __name__ == '__main__':
	unittest.main()
