import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]
LIB_PATH = ROOT / 'resources' / 'lib'


def load_subtitle_service():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.player = Mock()
	kodi_utils.get_property = Mock(return_value='')
	kodi_utils.get_infolabel = Mock(return_value='')
	kodi_utils.notification = Mock()
	kodi_utils.ok_dialog = Mock()
	kodi_utils.logger = Mock()
	kodi_utils.make_listitem = Mock()
	kodi_utils.build_url = Mock()
	kodi_utils.add_item = Mock()
	kodi_utils.parsed_query = Mock()
	kodi_utils.end_directory = Mock()
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	indexers = types.ModuleType('indexers')
	indexers.__path__ = []
	subtitles = types.ModuleType('indexers.subtitles')
	subtitles.SubtitleCancelled = type('SubtitleCancelled', (Exception,), {})
	subtitles.Subtitles = Mock
	subtitles.subtitle_context_property = 'subtitle_context'
	subtitles.subtitle_file_prefix = 'POVLiteSubs_'
	subtitles.subtitle_languages = ('eng', 'vie')
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils, 'indexers': indexers, 'indexers.subtitles': subtitles}
	return load_module('test_subtitle_service_module', LIB_PATH / 'subtitle_service.py', stubs)


class SubtitleServiceTests(unittest.TestCase):
	def setUp(self):
		self.service = load_subtitle_service()
		self.real_client = self.service._client
		self.client = Mock()
		self.client.subtitle_path = 'special://temp/'
		self.client.sub_filename = 'fixture'
		self.client._cancelled.return_value = False
		self.client._safe_extension.return_value = 'srt'
		self.service._client = Mock(return_value=(self.client, {}))
		self.service.kodi_utils.notification.reset_mock()
		self.service.kodi_utils.add_item.reset_mock()


	def test_search_playback_change_adds_no_results_or_notification(self):
		self.client.subtitles_search.return_value = [{'provider': 'opensubtitles', 'id': '123', 'lang': 'eng'}]
		self.client._ensure_current_playback.side_effect = self.service.SubtitleCancelled()

		with self.assertRaises(self.service.SubtitleCancelled): self.service._search(7)

		self.client._set_context.assert_not_called()
		self.service.kodi_utils.add_item.assert_not_called()
		self.service.kodi_utils.notification.assert_not_called()


if __name__ == '__main__':
	unittest.main()
