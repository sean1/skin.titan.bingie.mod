import json
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_streaming_cache():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.execJSONRPC = Mock()
	kodi_utils.get_infolabel = Mock(return_value='')
	kodi_utils.logger = Mock()
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	return load_module('test_streaming_cache_module', ROOT / 'resources' / 'lib' / 'modules' / 'streaming_cache.py', {'modules': modules, 'modules.kodi_utils': kodi_utils}), kodi_utils


class StreamingCacheTests(unittest.TestCase):
	def setUp(self):
		self.cache, self.ku = load_streaming_cache()

	def test_cache_tiers_respect_free_and_total_memory(self):
		cases = (
			((200, 1024), 32), ((300, 1024), 64), ((500, 1024), 128), ((900, 2048), 256),
			((2000, 4096), 512), ((2000, 2048), 256), ((None, 1024), None),
		)
		for memory, expected in cases:
			with self.subTest(memory=memory): self.assertEqual(self.cache.select_cache_mb(*memory), expected)


	def test_missing_memory_leaves_kodi_unchanged(self):
		with patch.object(self.cache, 'memory_mb', return_value=(None, None)):
			self.assertFalse(self.cache.tune())
		self.ku.execJSONRPC.assert_not_called()


if __name__ == '__main__':
	unittest.main()
