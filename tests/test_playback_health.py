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

	def test_learned_bandwidth_needs_repeat_success_and_respects_stalls(self):
		context = {'provider': 'rd'}
		self.module.record(context, 'healthy_play', bitrate_mbps=50, now=1)
		self.module.record(context, 'healthy_play', bitrate_mbps=40, now=2)
		self.assertEqual(self.module.learned_bandwidth(20, now=2), 20)
		self.assertIsNone(self.module.resolution_fallback_limit(now=2))
		self.module.record(context, 'healthy_play', bitrate_mbps=60, now=3)
		self.assertEqual(self.module.learned_bandwidth(20, now=3), 40)
		self.assertIsNone(self.module.resolution_fallback_limit(now=3))
		self.module.record(context, 'stalled_play', bitrate_mbps=30, stalls=1, now=4)
		self.assertEqual(self.module.learned_bandwidth(20, now=4), 21)
		self.assertIsNone(self.module.resolution_fallback_limit(now=4))
		self.module.record(context, 'stalled_play', bitrate_mbps=35, stalls=1, now=5)
		self.assertEqual(self.module.resolution_fallback_limit(now=5), 60)

	def test_resolution_fallback_limit_requires_repeated_stalls_and_preserves_proven_bitrate(self):
		context = {'provider': 'rd'}
		for bitrate, now in ((10, 1), (12, 2)):
			self.module.record(context, 'healthy_play', bitrate_mbps=bitrate, now=now)
		for bitrate, now in ((30, 3), (35, 4)):
			self.module.record(context, 'stalled_play', bitrate_mbps=bitrate, stalls=2, now=now)
		self.assertEqual(self.module.resolution_fallback_limit(now=4), 28)

	def test_bandwidth_samples_are_numeric_bounded_and_device_local(self):
		context = {'provider': 'tb'}
		for index in range(20): self.module.record(context, 'healthy_play', bitrate_mbps=index + 1, now=index + 1)
		self.assertEqual(len(FakeCache.data['bandwidth']), 12)
		serialized = json.dumps(FakeCache.data['bandwidth'])
		self.assertNotIn('provider', serialized)
		self.module.record(context, 'healthy_play', bitrate_mbps=float('inf'), now=30)
		self.assertEqual(len(FakeCache.data['bandwidth']), 12)


if __name__ == '__main__':
	unittest.main()
