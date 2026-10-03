import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'resources' / 'lib'))

from modules.source_search import RequestCoalescer, external_worker_count


class SourceSearchTests(unittest.TestCase):

	def test_worker_count_is_bounded(self):
		self.assertEqual(external_worker_count(0), 1)
		self.assertEqual(external_worker_count(4), 4)
		self.assertEqual(external_worker_count(22), 8)
		self.assertEqual(external_worker_count(22, exhaustive=True), 22)

	def test_request_coalescer_retries_after_grace(self):
		coalescer = RequestCoalescer(grace_seconds=0.01)
		calls = []

		def fetch():
			calls.append(True)
			return [len(calls)]

		self.assertEqual(coalescer.get('key', fetch, 1), [1])
		self.assertEqual(coalescer.get('key', fetch, 1), [1])
		time.sleep(0.02)
		self.assertEqual(coalescer.get('key', fetch, 1), [2])

	def test_request_coalescer_releases_waiters_on_failure(self):
		coalescer = RequestCoalescer()
		self.assertEqual(coalescer.get('key', lambda: 1 / 0, 1), [])
		self.assertEqual(coalescer.get('key', lambda: ['unused'], 1), [])


if __name__ == '__main__':
	unittest.main()
