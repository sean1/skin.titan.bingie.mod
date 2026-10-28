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


if __name__ == '__main__':
	unittest.main()
