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

	def test_kodi_memory_labels_are_parsed_as_megabytes(self):
		self.assertEqual(self.cache._memory_label_mb('512MB'), 512)
		self.assertEqual(self.cache._memory_label_mb('1.5 GB'), 1536)
		self.assertIsNone(self.cache._memory_label_mb('unknown'))

	def test_tuner_applies_only_changed_kodi_settings(self):
		current = {'filecache.buffermode': 4, 'filecache.readfactor': 400, 'filecache.chunksize': 131072, 'smb.chunksize': 128, 'filecache.memorysize': 20}
		def rpc(payload):
			request = json.loads(payload)
			if request['method'] == 'Settings.GetSettingValue': return json.dumps({'result': {'value': current[request['params']['setting']]}})
			return json.dumps({'result': 'OK'})
		self.ku.execJSONRPC.side_effect = rpc

		with patch.object(self.cache, 'memory_mb', return_value=(500, 1024)):
			self.assertTrue(self.cache.tune())

		writes = [json.loads(call.args[0]) for call in self.ku.execJSONRPC.call_args_list if json.loads(call.args[0])['method'] == 'Settings.SetSettingValue']
		self.assertEqual([(write['params']['setting'], write['params']['value']) for write in writes], [
			('filecache.readfactor', 0), ('filecache.chunksize', 262144), ('smb.chunksize', 256), ('filecache.memorysize', 128)
		])
		self.assertTrue(all(isinstance(write['params']['value'], int) for write in writes))

	def test_tuner_is_idempotent(self):
		targets = {**self.cache.CACHE_SETTINGS, 'filecache.memorysize': 128}
		self.ku.execJSONRPC.side_effect = lambda payload: json.dumps({'result': {'value': targets[json.loads(payload)['params']['setting']]}})
		with patch.object(self.cache, 'memory_mb', return_value=(500, 1024)):
			self.assertFalse(self.cache.tune())
		self.assertFalse(any(json.loads(call.args[0])['method'] == 'Settings.SetSettingValue' for call in self.ku.execJSONRPC.call_args_list))

	def test_missing_memory_leaves_kodi_unchanged(self):
		with patch.object(self.cache, 'memory_mb', return_value=(None, None)):
			self.assertFalse(self.cache.tune())
		self.ku.execJSONRPC.assert_not_called()


if __name__ == '__main__':
	unittest.main()
