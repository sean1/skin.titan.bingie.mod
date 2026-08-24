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

	def test_client_delegates_season_zero_configuration(self):
		self.service._client = self.real_client
		self.service.kodi_utils.player.isPlayingVideo.return_value = True
		self.service.kodi_utils.player.getPlayingFile.return_value = 'video.mkv'
		self.service._context = Mock(return_value={'imdb_id': 'tt123', 'season': 0, 'episode': 4, 'poster': 'poster.jpg'})
		self.service._video_metadata = Mock(return_value={'imdb_id': 'tt123', 'season': 0, 'episode': 4, 'is_episode': True, 'year': 2020})
		configured_client = Mock()
		self.service.Subtitles = Mock()
		self.service.Subtitles.return_value.configure.return_value = configured_client

		result = self.service._client()

		self.service.Subtitles.return_value.configure.assert_called_once_with('tt123', 0, 4, 'poster.jpg', 'video.mkv', '', '', '', 2020, '', '', '', '')
		self.assertEqual(result, (configured_client, {'imdb_id': 'tt123', 'season': 0, 'episode': 4, 'poster': 'poster.jpg'}))

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

	def test_fresh_manual_search_uses_raw_candidate_release_names(self):
		listitem = Mock()
		candidate = {'provider': 'subdl', 'id': 'file:parent:child', 'lang': 'eng', 'release_names': ['', 'Release.Name']}
		self.service._client.return_value = (self.client, {})
		self.client.subtitles_search.return_value = [candidate]
		self.service.kodi_utils.make_listitem.return_value = listitem

		self.service._search(7)

		self.client._set_context.assert_called_once_with([candidate])
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

	def test_release_label_prefers_valid_projected_and_raw_values(self):
		cases = (
			({'release': ' Public.Release ', 'release_names': ['Raw.Release']}, 'Public.Release'),
			({'release': ' ', 'release_names': [' ', None, {}, 'Raw.Release', 'Later.Release']}, 'Raw.Release'),
			({'release_names': 'Whole.Release.Name'}, 'Whole.Release.Name'),
			({'release': {}, 'release_names': None}, 'Release name unavailable'),
			({'release_names': ['x' * 121]}, 'x' * 120)
		)

		for subtitle, expected in cases: self.assertEqual(self.service._release_label(subtitle), expected)

	def test_manual_search_omits_unknown_rating_and_unsynced_property(self):
		listitem = Mock()
		self.service._client.return_value = (self.client, {'subtitles': [{'provider': 'opensubtitles', 'id': '123', 'lang': 'vie', 'release': 'Release.Name'}]})
		self.service.kodi_utils.make_listitem.return_value = listitem

		self.service._search(7)

		listitem.setArt.assert_called_once_with({'thumb': 'vie'})
		listitem.setProperty.assert_not_called()

	def test_rating_icon_clamps_and_rounds_to_available_textures(self):
		self.assertEqual([self.service._rating_icon(value) for value in (-1, 0, 1, 8.5, 9, 10, 11)], ['0', '0', '1', '4', '5', '5', '5'])
		self.assertEqual(self.service._rating_icon(None), '')

	def test_manual_search_uses_clean_fallback_when_release_name_is_missing(self):
		listitem = Mock()
		self.service._client.return_value = (self.client, {'subtitles': [{'provider': 'opensubtitles', 'id': '123', 'lang': 'vie', 'release': '   '}]})
		self.service.kodi_utils.make_listitem.return_value = listitem

		self.service._search(7)

		listitem.setLabel.assert_called_once_with('Vietnamese')
		listitem.setLabel2.assert_called_once_with('OpenSubtitles #1 · Release name unavailable')

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
