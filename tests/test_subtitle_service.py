import types
import unittest
from pathlib import Path
from types import SimpleNamespace
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

	def test_download_provider_failure_adds_no_item_or_notification(self):
		self.client.download_by_id.return_value = None

		self.service._download(7, {'provider': 'opensubtitles', 'candidate': '123', 'language': 'eng', 'result': '2'})

		self.service.kodi_utils.notification.assert_not_called()
		self.service.kodi_utils.add_item.assert_not_called()
		self.client.save_subtitle.assert_not_called()

	def test_download_without_active_playback_adds_no_item_or_notification(self):
		self.service._client.return_value = None

		self.service._download(7, {'provider': 'opensubtitles', 'candidate': '123'})

		self.service.kodi_utils.logger.assert_called_once()
		self.service.kodi_utils.notification.assert_not_called()
		self.service.kodi_utils.add_item.assert_not_called()

	def test_search_playback_change_adds_no_results_or_notification(self):
		self.client.subtitles_search.return_value = [{'provider': 'opensubtitles', 'id': '123', 'lang': 'eng'}]
		self.client._ensure_current_playback.side_effect = self.service.SubtitleCancelled()

		with self.assertRaises(self.service.SubtitleCancelled): self.service._search(7)

		self.client._set_context.assert_not_called()
		self.service.kodi_utils.add_item.assert_not_called()
		self.service.kodi_utils.notification.assert_not_called()

	def test_manual_search_uses_opaque_provider_ids_without_urls(self):
		listitem = Mock()
		self.service._client.return_value = (self.client, {'subtitles': [{'provider': 'subdl', 'id': 'file:parent:child', 'lang': 'eng', 'release': 'Release.Name', 'rating': 8.5, 'sync': True}]})
		self.service.kodi_utils.make_listitem.return_value = listitem
		self.service.kodi_utils.build_url.side_effect = lambda params: params

		self.service._search(7)

		params = self.service.kodi_utils.add_item.call_args.args[1]
		self.assertEqual(params['provider'], 'subdl')
		self.assertEqual(params['candidate'], 'file:parent:child')
		self.assertNotIn('url', params)
		self.assertNotIn('://', repr(params))
		listitem.setLabel.assert_called_once_with('English')
		listitem.setArt.assert_called_once_with({'thumb': 'eng', 'icon': '4'})
		listitem.setProperty.assert_called_once_with('sync', 'true')
		listitem.setLabel2.assert_called_once_with('SubDL #1 · Release.Name')

	def test_manual_search_reports_safe_config_issue(self):
		self.client.subtitles_search.return_value = []
		self.client.subtitle_diagnostics.return_value = {'config': 'unreadable', 'providers': (), 'path': '/private/path', 'detail': 'secret'}

		self.service._search(7)

		self.service.kodi_utils.ok_dialog.assert_called_once_with('Subtitle search', 'Subtitle search unavailable: provider settings cannot be read.')
		self.assertNotIn('private', self.service.kodi_utils.ok_dialog.call_args.args[1])
		self.assertNotIn('secret', self.service.kodi_utils.ok_dialog.call_args.args[1])
		self.service.kodi_utils.notification.assert_not_called()

	def test_manual_search_reports_failed_provider_names_and_keeps_results(self):
		candidate = {'provider': 'subdl', 'id': 'good', 'lang': 'eng'}
		self.client.subtitles_search.return_value = [candidate]
		self.client.subtitle_diagnostics.return_value = {'config': '', 'providers': ('subsource', 'unknown')}

		self.service._search(7)

		self.service.kodi_utils.notification.assert_called_once_with('Subtitle provider failed: SubSource.')
		self.service.kodi_utils.add_item.assert_called_once()

	def test_manual_search_branding_order_and_numbering_match_download_routes(self):
		items = [Mock(), Mock(), Mock()]
		self.service._client.return_value = (self.client, {'subtitles': [
			{'provider': 'subsource', 'id': 'vie-1', 'lang': 'vie', 'release': 'Vietnamese.Release'},
			{'provider': 'opensubtitles', 'id': 'eng-1', 'lang': 'eng', 'release': 'English.Release.One'},
			{'provider': 'subdl', 'id': 'eng-2', 'lang': 'eng', 'release': 'English.Release.Two'}
		]})
		self.service.kodi_utils.make_listitem.side_effect = items
		self.service.kodi_utils.build_url.side_effect = lambda params: params

		self.service._search(7)

		self.assertEqual([item.setLabel2.call_args.args[0] for item in items], [
			'OpenSubtitles #1 · English.Release.One', 'SubDL #2 · English.Release.Two', 'SubSource #1 · Vietnamese.Release'
		])
		routes = [call.args[1] for call in self.service.kodi_utils.add_item.call_args_list]
		self.assertEqual([(route['language'], route['result']) for route in routes], [('eng', 1), ('eng', 2), ('vie', 1)])

	def test_download_save_failure_adds_no_item_or_notification(self):
		payload = {'content': 'subtitle text', 'extension': 'srt'}
		self.client.download_by_id.return_value = payload
		self.client.save_subtitle.return_value = False

		self.service._download(7, {'provider': 'subsource', 'candidate': '123', 'language': 'vie', 'result': '3'})

		self.client.save_subtitle.assert_called_once_with(payload, 'special://temp/fixture_vie_subsource_3.srt')
		self.service.kodi_utils.notification.assert_not_called()
		self.service.kodi_utils.add_item.assert_not_called()

	def test_download_success_adds_exactly_one_subtitle_item(self):
		payload = {'content': 'subtitle text', 'extension': 'srt'}
		listitem = Mock()
		self.client.download_by_id.return_value = payload
		self.client.save_subtitle.return_value = True
		self.service.kodi_utils.make_listitem.return_value = listitem

		self.service._download(7, {'provider': 'subdl', 'candidate': 'parent:file', 'language': 'eng', 'result': '2'})

		final_path = 'special://temp/fixture_eng_subdl_2.srt'
		self.client.save_subtitle.assert_called_once_with(payload, final_path)
		listitem.setLabel.assert_called_once_with(final_path)
		self.service.kodi_utils.add_item.assert_called_once_with(7, final_path, listitem, False)
		self.service.kodi_utils.notification.assert_not_called()

	def test_run_contains_download_failure_and_always_ends_directory(self):
		self.service.kodi_utils.parsed_query.return_value = {'action': 'download', 'provider': 'subdl', 'candidate': 'parent:file'}
		self.service._download = Mock(side_effect=RuntimeError('download failed'))
		sys_obj = SimpleNamespace(argv=['plugin://skin.titan.bingie.lite', '9', '?action=download'])

		self.service.run(sys_obj)

		self.service.kodi_utils.end_directory.assert_called_once_with(9, False)
		self.service.kodi_utils.logger.assert_called_once()
		self.service.kodi_utils.notification.assert_not_called()

	def test_manual_service_actions_record_override_before_search_or_download(self):
		for action in ('search', 'manualsearch', 'download'):
			with self.subTest(action=action):
				events = []
				self.service.mark_manual_selection = Mock(side_effect=lambda: events.append('manual'))
				self.service._search = Mock(side_effect=lambda handle: events.append('search'))
				self.service._download = Mock(side_effect=lambda handle, params: events.append('download'))
				self.service.kodi_utils.parsed_query.return_value = {'action': action}
				self.service.run(SimpleNamespace(argv=['plugin://skin.titan.bingie.lite', '9', '?action=%s' % action]))
				self.assertEqual(events, ['manual', 'download' if action == 'download' else 'search'])
				self.service.mark_manual_selection.assert_called_once_with()

	def test_run_silently_contains_playback_cancellation(self):
		self.service.kodi_utils.parsed_query.return_value = {'action': 'download', 'provider': 'subdl', 'candidate': 'parent:file'}
		self.service._download = Mock(side_effect=self.service.SubtitleCancelled())
		sys_obj = SimpleNamespace(argv=['plugin://skin.titan.bingie.lite', '9', '?action=download'])

		self.service.run(sys_obj)

		self.service.kodi_utils.end_directory.assert_called_once_with(9, False)
		self.service.kodi_utils.logger.assert_called_once()
		self.service.kodi_utils.notification.assert_not_called()


if __name__ == '__main__':
	unittest.main()
