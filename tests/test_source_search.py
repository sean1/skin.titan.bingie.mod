import sys
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'resources' / 'lib'))

from modules.source_search import RequestCoalescer, external_worker_count, split_external_providers


class SourceSearchTests(unittest.TestCase):
	def test_split_external_providers_preserves_phase_order(self):
		sources = [('bitsearch', object()), ('torrentio', object()), ('dmm', object()), ('comet', object()), ('zilean', object())]
		core, fallback = split_external_providers(sources)
		self.assertEqual([item[0] for item in core], ['torrentio', 'comet', 'zilean'])
		self.assertEqual([item[0] for item in fallback], ['bitsearch', 'dmm'])

	def test_worker_count_is_bounded(self):
		self.assertEqual(external_worker_count(0), 1)
		self.assertEqual(external_worker_count(4), 4)
		self.assertEqual(external_worker_count(22), 8)
		self.assertEqual(external_worker_count(22, exhaustive=True), 22)

	def test_request_coalescer_shares_inflight_work(self):
		coalescer = RequestCoalescer()
		started = threading.Event()
		release = threading.Event()
		calls = []
		results = []

		def fetch():
			calls.append(True)
			started.set()
			release.wait(1)
			return ['shared']

		threads = [threading.Thread(target=lambda: results.append(coalescer.get('same', fetch, 1))) for _ in range(3)]
		for thread in threads: thread.start()
		self.assertTrue(started.wait(1))
		release.set()
		for thread in threads: thread.join(1)
		self.assertEqual(len(calls), 1)
		self.assertEqual(results, [['shared']] * 3)

	def test_request_coalescer_does_not_mix_keys(self):
		coalescer = RequestCoalescer()
		self.assertEqual(coalescer.get('one', lambda: ['one'], 1), ['one'])
		self.assertEqual(coalescer.get('two', lambda: ['two'], 1), ['two'])

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
