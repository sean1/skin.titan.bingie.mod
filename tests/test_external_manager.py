import sys
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Thread

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
			get_file_info=lambda *args, **kwargs: ('SD', ''), clean_title=lambda value: ''.join(character for character in value.lower() if character.isalnum()),
			check_title=lambda title, release, *args, **kwargs: ''.join(character for character in title.lower() if character.isalnum()) in ''.join(character for character in release.lower() if character.isalnum())
		),
		'modules.utils': module_stub(manual_function_import=lambda *args: None, get_datetime=lambda: None, safe_string=str, string_to_float=lambda value, fallback='0': float(value or fallback)),
	}
	path = ROOT / 'resources' / 'lib' / 'modules' / 'sources.py'
	return load_module('test_sources_runtime', path, stubs)


class FakeDebridCache:
	rows = []

	def __enter__(self): return self
	def __exit__(self, *args): pass
	def get_many(self, hash_list): return [row for row in self.rows if row[0] in hash_list]
	def set_many(self, *args): pass


def load_debrid_module():
	debrids = module_stub(
		all_debrid_api=module_stub(AllDebridAPI=object), real_debrid_api=module_stub(RealDebridAPI=object), torbox_api=module_stub(TorBoxAPI=object)
	)
	kodi_utils = module_stub(
		local_string=str, get_setting=lambda *args: '', notification=lambda *args: None, show_busy_dialog=lambda: None, hide_busy_dialog=lambda: None,
		ok_dialog=lambda *args, **kwargs: None, confirm_dialog=lambda *args, **kwargs: False, select_dialog=lambda *args, **kwargs: None, logger=lambda *args: None
	)
	settings = module_stub(default_internal_scrapers=(), enabled_debrids_check=lambda *args: False)
	request_session = types.SimpleNamespace(headers={})
	stubs = {
		'debrids': debrids, 'caches.debrid_cache': module_stub(DebridCache=FakeDebridCache), 'indexers.metadata': module_stub(),
		'modules.kodi_utils': kodi_utils, 'modules.settings': settings, 'requests': module_stub(session=lambda: request_session),
		'fenom': module_stub(), 'fenom.client': module_stub(randomagent=lambda: 'test')
	}
	path = ROOT / 'resources' / 'lib' / 'modules' / 'debrid.py'
	return load_module('test_debrid_runtime', path, stubs)


SOURCES = load_sources_module()
REAL_EXTERNAL_SOURCE = SOURCES.ExternalSource


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
	cached = set()
	cached_by_provider = {}
	exact_providers = set()
	checked_batches = []

	@classmethod
	def request_context(cls, hash_list):
		hash_list = tuple(hash_list)
		cls.checked_batches.append(set(hash_list))
		return hash_list, ()

	def __init__(self, meta, name, hash_list, cached_hashes):
		self.name, self.hash_list, self.cached_hashes = name, tuple(hash_list), tuple(cached_hashes)

	def cache_check(self):
		cached = self.cached_by_provider.get(self.name, self.cached)
		cached = [item for item in self.hash_list if item in cached]
		if self.name in self.exact_providers: return {'cached': cached, 'checked': set(self.hash_list)}
		return cached


class RecordingExecutor:
	worker_counts = []
	submissions = []

	def __init__(self, max_workers):
		self.executor_id = len(self.worker_counts)
		self.worker_counts.append(max_workers)
		self.executor = ThreadPoolExecutor(max_workers)

	def submit(self, *args, **kwargs):
		self.submissions.append((self.executor_id, getattr(args[0], '__name__', 'unknown')))
		return self.executor.submit(*args, **kwargs)

	def shutdown(self, *args, **kwargs):
		return self.executor.shutdown(*args, **kwargs)


class ExternalManagerTests(unittest.TestCase):
	def setUp(self):
		FakeExternalSource.calls = []
		FakeDebridCheck.cached = set()
		FakeDebridCheck.cached_by_provider = {}
		FakeDebridCheck.exact_providers = set()
		FakeDebridCheck.checked_batches = []
		RecordingExecutor.worker_counts = []
		RecordingExecutor.submissions = []
		SOURCES.ExternalSource = FakeExternalSource
		SOURCES.DebridCheck = FakeDebridCheck
		SOURCES.TPE = RecordingExecutor

	def manager(self, force_full_search=False, eligibility_filter=lambda results: results, provider_names=None, debrid_names=None):
		provider_names = provider_names or (*sorted(CORE_EXTERNAL_PROVIDERS), 'bitsearch', 'dmm')
		debrid_names = debrid_names or ['realdebrid']
		providers = [(name, object()) for name in provider_names]
		meta = {'background': True, 'search_info': {'scrape_timeout': 1}}
		return SOURCES.ExternalManager(meta, providers, debrid_names, [], [], Progress(), eligibility_filter=eligibility_filter, force_full_search=force_full_search)


	def test_provider_associations_do_not_bypass_ambiguous_year_and_wrong_ids_reject(self):
		external = REAL_EXTERNAL_SOURCE({}, '4K 1080p 720p SD total')
		external.require_tv_year, external.year, external.conflicting_tv_years = True, '2002', ['1978']
		external.data, external.season, external.episode = {'imdb': 'tt123'}, '33', '1'
		sources = [
			{'name': 'Top.Gear.S33E01', 'identity_evidence': {'imdb_id': 'tt123'}},
			{'name': 'Top.Gear.2002.S33E01', 'identity_evidence': {'catalogue_imdb_id': 'tt999'}},
			{'name': 'Top.Gear.S33E01', 'identity_evidence': {'file_sid': 'tt123:33:2', 'file_match_required': True}},
			{'name': 'Top.Gear.S33E01', 'identity_evidence': {'file_match_required': True}},
			{'name': 'Top.Gear.S33E01', 'identity_evidence': {'file_sid': 'tt123:33:1', 'file_match_required': True}},
		]
		filtered = external.filter_ambiguous_tv_sources(sources, '')
		self.assertEqual(len(filtered), 2)
		self.assertTrue(all(item['_identity_status'] == 'unverified' for item in filtered))
		self.assertEqual(filtered[1]['identity_evidence']['file_sid'], 'tt123:33:1')


	def test_running_provider_cannot_publish_after_source_sink_closes(self):
		sink = SOURCES.SourceResultSink()
		release = Event()
		worker = Thread(target=lambda: (release.wait(), sink.extend([{'name': 'late'}])))
		worker.start()
		try:
			sink.close()
			release.set()
			worker.join(2.0)
		finally: release.set()

		self.assertFalse(worker.is_alive())
		self.assertEqual(sink.items, [])


if __name__ == '__main__':
	unittest.main()
