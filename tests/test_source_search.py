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



if __name__ == '__main__':
	unittest.main()
