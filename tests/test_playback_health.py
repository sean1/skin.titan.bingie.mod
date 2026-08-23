import json
import types
import unittest
from pathlib import Path

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


class FakeCache:
	data = None
	sets = []

	def get(self, key): return self.data

	def set(self, key, data, expiry):
		type(self).data = data
		type(self).sets.append((key, data, expiry))


def health_module():
	FakeCache.data, FakeCache.sets = None, []
	caches = types.ModuleType('caches')
	main_cache = types.ModuleType('caches.main_cache')
	main_cache.MainCache = FakeCache
	return load_module('test_playback_health_runtime', ROOT / 'resources/lib/modules/playback_health.py', {'caches': caches, 'caches.main_cache': main_cache})


class PlaybackHealthTests(unittest.TestCase):
	def setUp(self): self.module = health_module()

	def test_source_context_keeps_only_known_provider_and_safe_hostname(self):
		item = {'debrid': 'Real-Debrid', 'title': 'Private title', 'tmdb': '123', 'hash': 'secret'}
		self.assertEqual(self.module.source_context(item, 'https://user:pass@CDN.Example.com/private/file?token=secret#hash'), {'provider': 'realdebrid', 'host': 'cdn.example.com'})
		self.assertEqual(self.module.source_context({'debrid': 'unknown'}, 'https://cdn.example.com/file'), {})
		self.assertEqual(self.module.source_context({'debrid': 'rd'}, 'smb://server/private'), {'provider': 'realdebrid'})
		self.assertEqual(self.module.source_context({'debrid': 'rd'}, 'http://127.0.0.1/private'), {'provider': 'realdebrid'})

	def test_persisted_data_never_contains_media_or_url_fields(self):
		context = self.module.source_context({'debrid': 'AllDebrid', 'title': 'Hidden', 'tmdb': 99, 'hash': 'abc'}, 'https://cdn.example.org/path/title.mkv?token=topsecret')
		context.update({'url': 'https://bad.invalid/private', 'path': '/private', 'query': 'token', 'title': 'Hidden', 'tmdb': 99, 'hash': 'abc'})
		self.assertTrue(self.module.record(context, 'resolve_ok', now=100))
		serialized = json.dumps(FakeCache.data)
		for secret in ('Hidden', 'topsecret', '/private', 'bad.invalid', 'title.mkv', '"tmdb"', '"hash"', '"url"', '"path"', '"query"'):
			self.assertNotIn(secret, serialized)
		self.assertIn('alldebrid', serialized)
		self.assertIn('cdn.example.org', serialized)

	def test_penalty_is_neutral_until_three_attempts_and_orders_health(self):
		for now in (1, 2): self.module.record({'provider': 'rd'}, 'resolve_fail', now=now)
		self.assertEqual(self.module.provider_penalty('realdebrid', now=2), 0)
		self.module.record({'provider': 'rd'}, 'resolve_fail', now=3)
		self.assertEqual(self.module.provider_penalty('realdebrid', now=3), 80)
		for now in (1, 2, 3): self.module.record({'provider': 'ad'}, 'resolve_ok', now=now)
		self.assertEqual(self.module.provider_penalty('alldebrid', now=3), 0)
		self.assertGreater(self.module.provider_penalty('realdebrid', now=3), self.module.provider_penalty('alldebrid', now=3))

	def test_stage_counters_do_not_double_count_one_play(self):
		context = {'provider': 'torbox'}
		self.module.record(context, 'resolve_ok', now=1)
		self.module.record(context, 'startup_ok', latency=4, now=2)
		self.module.record(context, 'healthy_play', elapsed=120, now=3)
		stages = FakeCache.data['providers']['torbox']['stages']
		self.assertEqual(stages['resolve']['attempts'], 1)
		self.assertEqual(stages['startup']['attempts'], 1)
		self.assertEqual(stages['stream']['attempts'], 1)
		self.assertEqual(self.module.provider_penalty('torbox', now=3), 0)

	def test_decay_returns_old_sparse_history_to_neutral(self):
		for now in (0, 1, 2, 3): self.module.record({'provider': 'realdebrid'}, 'stream_error', now=now)
		self.assertGreater(self.module.provider_penalty('rd', now=3), 0)
		self.assertEqual(self.module.provider_penalty('rd', now=3 + 14 * 24 * 60 * 60), 0)

	def test_penalty_is_bounded_and_bad_measurements_are_ignored(self):
		for now in range(20): self.module.record({'provider': 'tb'}, 'startup_fail', latency=float('inf'), now=now)
		self.assertLessEqual(self.module.provider_penalty('torbox', now=20), 100)
		for now in range(20, 40): self.module.record({'provider': 'tb'}, 'startup_ok', latency=600, now=now)
		self.assertGreaterEqual(self.module.provider_penalty('torbox', now=40), 0)
		self.assertLessEqual(self.module.provider_penalty('torbox', now=40), 100)

	def test_corrupt_missing_and_unknown_state_are_neutral(self):
		for value in (None, 'bad', {'version': 999}, {'version': 1, 'providers': {'realdebrid': {'stages': 'bad'}}}):
			FakeCache.data = value
			self.assertEqual(self.module.provider_penalty('realdebrid', now=10), 0)
		self.assertEqual(self.module.provider_penalty('not-a-provider', now=10), 0)

	def test_host_history_is_capped_at_32_and_evicts_oldest(self):
		for index in range(40): self.module.record({'provider': 'rd', 'host': 'cdn%02d.example.com' % index}, 'resolve_ok', now=index + 1)
		self.assertEqual(len(FakeCache.data['hosts']), 32)
		self.assertNotIn('realdebrid|cdn00.example.com', FakeCache.data['hosts'])
		self.assertIn('realdebrid|cdn39.example.com', FakeCache.data['hosts'])

	def test_host_penalty_requires_three_matching_provider_attempts(self):
		context = {'provider': 'rd', 'host': 'bad.example.com'}
		for now in (1, 2): self.module.record(context, 'stream_error', now=now)
		self.assertEqual(self.module.host_penalty(context, now=2), 0)
		self.module.record(context, 'stream_error', now=3)
		self.assertEqual(self.module.host_penalty(context, now=3), 80)
		self.assertEqual(self.module.host_penalty({'provider': 'ad', 'host': 'bad.example.com'}, now=3), 0)
		self.assertEqual(self.module.host_penalty({'provider': 'rd', 'host': '127.0.0.1'}, now=3), 0)

	def test_shared_hostname_keeps_provider_health_independent(self):
		host = 'shared.cdn.example.com'
		for now in (1, 2, 3): self.module.record({'provider': 'rd', 'host': host}, 'stream_error', now=now)
		for now in (4, 5, 6): self.module.record({'provider': 'ad', 'host': host}, 'healthy_play', now=now)
		self.assertEqual(self.module.host_penalty({'provider': 'rd', 'host': host}, now=6), 80)
		self.assertEqual(self.module.host_penalty({'provider': 'ad', 'host': host}, now=6), 0)
		self.assertEqual(len(FakeCache.data['hosts']), 2)

	def test_learned_bandwidth_needs_repeat_success_and_respects_stalls(self):
		context = {'provider': 'rd'}
		self.module.record(context, 'healthy_play', bitrate_mbps=50, now=1)
		self.module.record(context, 'healthy_play', bitrate_mbps=40, now=2)
		self.assertEqual(self.module.learned_bandwidth(20, now=2), 20)
		self.module.record(context, 'healthy_play', bitrate_mbps=60, now=3)
		self.assertEqual(self.module.learned_bandwidth(20, now=3), 40)
		self.module.record(context, 'stalled_play', bitrate_mbps=30, stalls=1, now=4)
		self.assertEqual(self.module.learned_bandwidth(20, now=4), 21)

	def test_bandwidth_samples_are_numeric_bounded_and_device_local(self):
		context = {'provider': 'tb'}
		for index in range(20): self.module.record(context, 'healthy_play', bitrate_mbps=index + 1, now=index + 1)
		self.assertEqual(len(FakeCache.data['bandwidth']), 12)
		serialized = json.dumps(FakeCache.data['bandwidth'])
		self.assertNotIn('provider', serialized)
		self.module.record(context, 'healthy_play', bitrate_mbps=float('inf'), now=30)
		self.assertEqual(len(FakeCache.data['bandwidth']), 12)

	def test_cache_record_is_versioned_and_has_ninety_day_expiry(self):
		self.module.record({'provider': 'ad'}, 'resolve_ok', now=1)
		key, state, expiry = FakeCache.sets[-1]
		self.assertEqual(key, 'bingie_playback_health_v1')
		self.assertEqual(state['version'], 1)
		self.assertEqual(expiry.days, 90)


if __name__ == '__main__':
	unittest.main()
