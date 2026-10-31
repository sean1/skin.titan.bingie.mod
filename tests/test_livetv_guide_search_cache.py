import hashlib
import importlib.util
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


if __name__ == '__main__': unittest.main()
