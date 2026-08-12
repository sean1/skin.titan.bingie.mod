import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
LIB_PATH = ROOT / 'resources' / 'lib'


def load_subtitle_service():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.player = Mock()
	kodi_utils.get_property = Mock(return_value='')
	kodi_utils.get_infolabel = Mock(return_value='')
	kodi_utils.notification = Mock()
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
	subtitles.subtitle_manifest = 'https://example.test/manifest.json'
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils, 'indexers': indexers, 'indexers.subtitles': subtitles}
	previous = {name: sys.modules.get(name) for name in stubs}
	path_was_present = str(LIB_PATH) in sys.path
	sys.modules.update(stubs)
	try:
		path = LIB_PATH / 'subtitle_service.py'
		spec = importlib.util.spec_from_file_location('test_subtitle_service_module', path)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
	finally:
		for name, old_module in previous.items():
			if old_module is None: sys.modules.pop(name, None)
			else: sys.modules[name] = old_module
		if not path_was_present and str(LIB_PATH) in sys.path: sys.path.remove(str(LIB_PATH))
	return module


class SubtitleServiceTests(unittest.TestCase):
	def setUp(self):
		self.service = load_subtitle_service()
		self.client = Mock()
		self.client.subtitle_path = 'special://temp/'
		self.client.sub_filename = 'fixture'
		self.client._cancelled.return_value = False
		self.service._client = Mock(return_value=(self.client, {}))
		self.service.kodi_utils.notification.reset_mock()
		self.service.kodi_utils.add_item.reset_mock()

	def test_download_provider_failure_adds_no_item_or_notification(self):
		self.client.subtitles_download.return_value = 'Service Unavailable'

		self.service._download(7, {'url': 'https://example.test/subtitle', 'language': 'eng', 'result': '2'})

		self.service.kodi_utils.notification.assert_not_called()
		self.service.kodi_utils.add_item.assert_not_called()
		self.client.save_subtitle.assert_not_called()

	def test_download_without_active_playback_adds_no_item_or_notification(self):
		self.service._client.return_value = None

		self.service._download(7, {'url': 'https://example.test/subtitle'})

		self.service.kodi_utils.logger.assert_called_once()
		self.service.kodi_utils.notification.assert_not_called()
		self.service.kodi_utils.add_item.assert_not_called()

	def test_search_playback_change_adds_no_results_or_notification(self):
		self.client.subtitles_search.return_value = [{'lang': 'eng', 'url': 'https://example.test/subtitle'}]
		self.client._ensure_current_playback.side_effect = self.service.SubtitleCancelled()

		with self.assertRaises(self.service.SubtitleCancelled): self.service._search(7)

		self.client._set_context.assert_not_called()
		self.service.kodi_utils.add_item.assert_not_called()
		self.service.kodi_utils.notification.assert_not_called()

	def test_download_save_failure_adds_no_item_or_notification(self):
		response = SimpleNamespace(text='subtitle text')
		self.client.subtitles_download.return_value = response
		self.client.save_subtitle.return_value = False

		self.service._download(7, {'url': 'https://example.test/subtitle', 'language': 'vie', 'result': '3'})

		self.client.save_subtitle.assert_called_once_with(response, 'special://temp/fixture_vie_3.srt')
		self.service.kodi_utils.notification.assert_not_called()
		self.service.kodi_utils.add_item.assert_not_called()

	def test_download_success_adds_exactly_one_subtitle_item(self):
		response = SimpleNamespace(text='subtitle text')
		listitem = Mock()
		self.client.subtitles_download.return_value = response
		self.client.save_subtitle.return_value = True
		self.service.kodi_utils.make_listitem.return_value = listitem

		self.service._download(7, {'url': 'https://example.test/subtitle', 'language': 'eng', 'result': '2'})

		final_path = 'special://temp/fixture_eng_2.srt'
		self.client.save_subtitle.assert_called_once_with(response, final_path)
		listitem.setLabel.assert_called_once_with(final_path)
		self.service.kodi_utils.add_item.assert_called_once_with(7, final_path, listitem, False)
		self.service.kodi_utils.notification.assert_not_called()

	def test_run_contains_download_failure_and_always_ends_directory(self):
		self.service.kodi_utils.parsed_query.return_value = {'action': 'download', 'url': 'https://example.test/subtitle'}
		self.service._download = Mock(side_effect=RuntimeError('download failed'))
		sys_obj = SimpleNamespace(argv=['plugin://skin.titan.bingie.lite', '9', '?action=download'])

		self.service.run(sys_obj)

		self.service.kodi_utils.end_directory.assert_called_once_with(9, False)
		self.service.kodi_utils.logger.assert_called_once()
		self.service.kodi_utils.notification.assert_not_called()

	def test_run_silently_contains_playback_cancellation(self):
		self.service.kodi_utils.parsed_query.return_value = {'action': 'download', 'url': 'https://example.test/subtitle'}
		self.service._download = Mock(side_effect=self.service.SubtitleCancelled())
		sys_obj = SimpleNamespace(argv=['plugin://skin.titan.bingie.lite', '9', '?action=download'])

		self.service.run(sys_obj)

		self.service.kodi_utils.end_directory.assert_called_once_with(9, False)
		self.service.kodi_utils.logger.assert_called_once()
		self.service.kodi_utils.notification.assert_not_called()


if __name__ == '__main__':
	unittest.main()
