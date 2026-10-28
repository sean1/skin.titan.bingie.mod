import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('test_livetv_guide_search_cache', ROOT / 'resources/lib/modules/livetv_guide_search.py')
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


class LiveTVGuideSearchCacheTests(unittest.TestCase):
	def setUp(self):
		(ROOT / 'tmp').mkdir(exist_ok=True)
		self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
		self.addCleanup(self.temp.cleanup)
		self.url = 'https://guide.example/xml?username=private&password=secret'
		self.raw = Path(self.temp.name) / ('livetv-xmltv-' + hashlib.sha256(self.url.encode()).hexdigest() + '.cache')
		self.raw.write_bytes(b'<tv/>')
		self.now = time.time()
		self.data = {'sports': [[self.now - 60, self.now + 60, 'Liverpool vs Chelsea'], [self.now + 60, self.now + 3600, 'Liverpool replay']]}
		self.loader = Mock(return_value=self.data)

	def search(self, term='liverpool', wanted=None, now=None):
		return cache.cached_search(self.temp.name, self.url, self.now if now is None else now, {'sports'} if wanted is None else wanted, term, self.loader)

	def files(self):
		return list(Path(self.temp.name).glob('livetv-guide-search-*.json'))

	def test_repeat_query_reuses_private_cache_and_filters_expired_programme(self):
		self.assertEqual(self.search(), self.data)
		self.assertEqual(self.search(now=self.now + 120), {'sports': [self.data['sports'][1]]})
		self.loader.assert_called_once()
		self.assertEqual(len(self.files()), 1)
		path = self.files()[0]
		self.assertEqual(path.stat().st_mode & 0o777, 0o600)
		self.assertNotIn('secret', path.read_text())
		self.assertNotIn('private', path.name)

	def test_different_query_or_channel_set_does_not_reuse_results(self):
		self.search()
		self.search(term='chelsea')
		self.search(wanted={'sports', 'other'})
		self.assertEqual(self.loader.call_count, 3)

	def test_raw_feed_change_invalidates_result(self):
		self.search()
		self.raw.write_bytes(b'<tv>changed</tv>')
		self.search()
		self.assertEqual(self.loader.call_count, 2)

	def test_missing_or_stale_source_does_not_use_cache(self):
		self.search()
		old = time.time() - 3 * 3600
		os.utime(self.raw, (old, old))
		self.search()
		self.raw.unlink()
		self.search()
		self.assertEqual(self.loader.call_count, 3)

	def test_corrupt_or_expired_result_falls_back_to_loader(self):
		self.search()
		path = self.files()[0]
		path.write_text('{bad')
		self.search()
		data = json.loads(path.read_text())
		data['created'] = time.time() - 301
		path.write_text(json.dumps(data))
		self.search()
		self.assertEqual(self.loader.call_count, 3)

	def test_cache_keeps_only_recent_search_files(self):
		for index in range(12): self.search(term='query%d' % index)
		self.assertLessEqual(len(self.files()), 8)

	def test_truncated_search_is_not_cached(self):
		self.loader.return_value = {'sports': [[self.now - 1, self.now + 60, 'Match']] * 5000}
		self.search()
		self.search(now=self.now + 120)
		self.assertEqual(self.loader.call_count, 2)
		self.assertEqual(self.files(), [])


if __name__ == '__main__': unittest.main()
