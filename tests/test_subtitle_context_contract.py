import hashlib
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_subtitles():
	from tests.test_subtitle_providers import load_providers
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.xbmc_player = object
	kodi_utils.set_property = Mock()
	kodi_utils.get_property = Mock(return_value='')
	kodi_utils.player = Mock()
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	indexers = types.ModuleType('indexers')
	indexers.__path__ = []
	return load_module('test_subtitle_context_contract_indexer', ROOT / 'resources/lib/indexers/subtitles.py', {
		'modules': modules, 'modules.kodi_utils': kodi_utils, 'indexers': indexers, 'indexers.subtitle_providers': load_providers()
	})


def load_service(subtitles):
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.player = Mock()
	kodi_utils.logger = Mock()
	kodi_utils.notification = Mock()
	kodi_utils.make_listitem = Mock()
	kodi_utils.add_item = Mock()
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	indexers = types.ModuleType('indexers')
	indexers.__path__ = []
	return load_module('test_subtitle_context_contract_service', ROOT / 'resources/lib/subtitle_service.py', {
		'modules': modules, 'modules.kodi_utils': kodi_utils, 'indexers': indexers, 'indexers.subtitles': subtitles
	})


class SubtitleContextContractTests(unittest.TestCase):
	def setUp(self):
		self.subtitles = load_subtitles()

	def test_context_fingerprints_playing_file_and_never_persists_it(self):
		client = self.subtitles.Subtitles().configure('tt123', expected_playing_file='https://stream.invalid/private?token=secret')
		client.getPlayingFile = Mock(return_value='https://stream.invalid/private?token=secret')

		context = client._set_context()

		payload = self.subtitles.kodi_utils.set_property.call_args.args[1]
		self.assertNotIn('stream.invalid', payload)
		self.assertNotIn('secret', payload)
		self.assertEqual(context['playing_fingerprint'], hashlib.sha256(b'https://stream.invalid/private?token=secret').hexdigest())
		self.assertEqual(context['version'], 2)


if __name__ == '__main__':
	unittest.main()
