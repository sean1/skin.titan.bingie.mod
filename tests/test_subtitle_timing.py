import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_subtitles():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.xbmc_player = object
	kodi_utils.logger = Mock()
	kodi_utils.delete_file = Mock()
	kodi_utils.monitor = Mock()
	kodi_utils.list_dirs = Mock()
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils}
	path = ROOT / 'resources' / 'lib' / 'indexers' / 'subtitles.py'
	return load_module('test_subtitle_timing_indexer', path, stubs)


class RecordingFile:
	def __init__(self, events, write_error=None, write_result=None):
		self.events = events
		self.write_error = write_error
		self.write_result = write_result

	def __enter__(self):
		self.events.append('open')
		return self

	def write(self, payload):
		self.events.append(('write', payload))
		if self.write_error: raise self.write_error
		return self.write_result

	def __exit__(self, exc_type, exc, traceback):
		self.events.append('close')
		return False


class SubtitleTimingTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.subtitles = load_subtitles()

	def setUp(self):
		self.client = self.subtitles.Subtitles()
		self.client.languages = ('eng', 'vie')
		self.client.subtitle_path = 'special://temp/'
		self.client.sub_filename = 'fixture'
		self.client.poster = ''
		self.client.imdb_id = 'tt123'
		self.client.season = None
		self.client.episode = None
		self.client.media = {'imdb_id': 'tt123', 'season': None, 'episode': None, 'release_name': 'Fixture.1080p.WEB-DL-GROUP'}
		self.candidates = [
			{'provider': 'opensubtitles', 'id': 'eng-1', 'lang': 'eng', 'release_names': ['Fixture'], 'score': 20},
			{'provider': 'subsource', 'id': 'vie-1', 'lang': 'vie', 'release_names': ['Fixture'], 'score': 20}
		]
		self.client.provider_manager = Mock()
		self.client.provider_manager.search.return_value = self.candidates
		self.client.provider_manager.download.return_value = {'content': 'subtitle text', 'extension': 'srt'}
		self.client._set_context = Mock()
		self.subtitles.kodi_utils.sleep = Mock()
		self.subtitles.kodi_utils.notification = Mock()
		self.subtitles.kodi_utils.logger.reset_mock()
		self.subtitles.kodi_utils.delete_file.reset_mock()
		self.subtitles.kodi_utils.list_dirs.reset_mock()
		self.subtitles.kodi_utils.monitor.reset_mock()
		self.subtitles.kodi_utils.monitor.abortRequested.return_value = False
		self.subtitles.kodi_utils.monitor.waitForAbort.return_value = False

	def test_remote_subtitle_closes_file_before_immediate_attach(self):
		events = []
		final_path = 'special://temp/fixture_eng.srt'
		self.subtitles.kodi_utils.open_file = Mock(return_value=RecordingFile(events))
		self.client.setSubtitles = Mock(side_effect=lambda path: events.append(('attach', path)))

		result = self.client._searched_subs()

		self.assertTrue(result)
		self.client.provider_manager.download.assert_called_once_with(self.candidates[0])
		self.subtitles.kodi_utils.open_file.assert_called_once_with(final_path, 'w')
		self.assertEqual(events, ['open', ('write', 'subtitle text'), 'close', ('attach', final_path)])
		self.subtitles.kodi_utils.sleep.assert_not_called()

	def test_embedded_english_wins_when_vietnamese_appears_first(self):
		self.client.getAvailableSubtitleStreams = Mock(return_value=['vie', 'spa', 'English (Forced)'])
		self.client.setSubtitleStream = Mock()
		self.client.showSubtitles = Mock()

		result = self.client._video_file_subs()

		self.assertTrue(result)
		self.client.setSubtitleStream.assert_called_once_with(2)
		self.client.showSubtitles.assert_called_once_with(True)
		self.subtitles.kodi_utils.notification.assert_called_once_with(32852, icon='')

	def test_embedded_vietnamese_is_selected_when_english_is_unavailable(self):
		self.client.getAvailableSubtitleStreams = Mock(return_value=['spa', 'Vietnamese'])
		self.client.setSubtitleStream = Mock()
		self.client.showSubtitles = Mock()

		result = self.client._video_file_subs()

		self.assertTrue(result)
		self.client.setSubtitleStream.assert_called_once_with(1)
		self.client.showSubtitles.assert_called_once_with(True)

	def test_embedded_unpreferred_languages_continue_to_download_fallback(self):
		self.client.getAvailableSubtitleStreams = Mock(return_value=['spa', 'fre'])
		self.client.setSubtitleStream = Mock()
		self.client.showSubtitles = Mock()

		result = self.client._video_file_subs()

		self.assertFalse(result)
		self.client.setSubtitleStream.assert_not_called()
		self.client.showSubtitles.assert_not_called()
		self.subtitles.kodi_utils.notification.assert_not_called()

	def test_legacy_player_api_uses_current_preferred_subtitle(self):
		self.client.getAvailableSubtitleStreams = Mock(side_effect=AttributeError)
		self.client.getSubtitles = Mock(return_value='en')
		self.client.showSubtitles = Mock()

		result = self.client._video_file_subs()

		self.assertTrue(result)
		self.client.showSubtitles.assert_called_once_with(True)
		self.subtitles.kodi_utils.notification.assert_called_once_with(32852, icon='')

	def test_binary_response_payload_is_written_before_attach(self):
		class BinaryResponse:
			content = b'subtitle bytes'

			@property
			def text(self):
				raise AttributeError

		events = []
		self.client.provider_manager.download.return_value = {'content': b'subtitle bytes', 'extension': 'srt'}
		self.subtitles.kodi_utils.open_file = Mock(return_value=RecordingFile(events))
		self.client.setSubtitles = Mock(side_effect=lambda path: events.append(('attach', path)))

		self.client._searched_subs()

		self.assertEqual(events, ['open', ('write', b'subtitle bytes'), 'close', ('attach', 'special://temp/fixture_eng.srt')])
		self.subtitles.kodi_utils.sleep.assert_not_called()

	def test_failed_top_download_falls_through_to_next_ranked_candidate(self):
		second_candidate = {'provider': 'subdl', 'id': 'eng-2', 'lang': 'eng', 'release_names': ['Fixture'], 'score': 10}
		self.client.provider_manager.search.return_value = [self.candidates[0], second_candidate]
		self.client.provider_manager.download.side_effect = [None, {'content': b'second subtitle', 'extension': 'ass'}]
		events = []
		self.subtitles.kodi_utils.open_file = Mock(return_value=RecordingFile(events))
		self.client.setSubtitles = Mock(side_effect=lambda path: events.append(('attach', path)))

		result = self.client._searched_subs()

		self.assertTrue(result)
		self.assertEqual([call.args[0]['id'] for call in self.client.provider_manager.download.call_args_list], ['eng-1', 'eng-2'])
		self.assertEqual(events, ['open', ('write', b'second subtitle'), 'close', ('attach', 'special://temp/fixture_eng.ass')])

	def test_write_failure_closes_file_without_attach(self):
		events = []
		self.client.provider_manager.search.return_value = self.candidates[:1]
		self.subtitles.kodi_utils.open_file = Mock(return_value=RecordingFile(events, OSError('write failed')))
		self.client.setSubtitles = Mock()

		result = self.client._searched_subs()

		self.assertFalse(result)
		self.assertEqual(events, ['open', ('write', 'subtitle text'), 'close'])
		self.subtitles.kodi_utils.delete_file.assert_called_once_with('special://temp/fixture_eng.srt')
		self.subtitles.kodi_utils.notification.assert_called_once_with(32856, icon='')
		self.client.setSubtitles.assert_not_called()
		self.subtitles.kodi_utils.sleep.assert_not_called()

	def test_false_write_result_removes_partial_file_without_attach(self):
		events = []
		self.client.provider_manager.search.return_value = self.candidates[:1]
		self.subtitles.kodi_utils.open_file = Mock(return_value=RecordingFile(events, write_result=False))
		self.client.setSubtitles = Mock()

		result = self.client._searched_subs()

		self.assertFalse(result)
		self.assertEqual(events, ['open', ('write', 'subtitle text'), 'close'])
		self.subtitles.kodi_utils.delete_file.assert_called_once_with('special://temp/fixture_eng.srt')
		self.client.setSubtitles.assert_not_called()

	def test_empty_download_is_not_written_or_attached(self):
		self.client.provider_manager.download.return_value = {'content': b'', 'extension': 'srt'}
		self.subtitles.kodi_utils.open_file = Mock()
		self.client.setSubtitles = Mock()

		result = self.client._searched_subs()

		self.assertFalse(result)
		self.subtitles.kodi_utils.open_file.assert_not_called()
		self.subtitles.kodi_utils.delete_file.assert_not_called()
		self.client.setSubtitles.assert_not_called()

	def test_configure_uses_series_filename_for_season_zero(self):
		client = self.subtitles.Subtitles().configure('tt123', 0, 4, 'poster.jpg', 'video.mkv')

		self.assertEqual(client.languages, self.subtitles.subtitle_languages)
		self.assertEqual((client.imdb_id, client.season, client.episode), ('tt123', 0, 4))
		self.assertEqual((client.poster, client.subtitle_path, client.expected_playing_file), ('poster.jpg', 'special://temp/', 'video.mkv'))
		self.assertEqual(client.sub_filename, 'POVLiteSubs_tt123_0_4')

	def test_open_failure_does_not_delete_an_existing_destination(self):
		self.subtitles.kodi_utils.open_file = Mock(side_effect=OSError('open failed'))

		result = self.client.save_subtitle(SimpleNamespace(text='subtitle text'), 'special://temp/fixture_eng.srt')

		self.assertFalse(result)
		self.subtitles.kodi_utils.delete_file.assert_not_called()

	def test_playback_change_after_download_does_not_write_attach_or_notify(self):
		self.client.expected_playing_file = 'old-video'
		self.client.isPlayingVideo = Mock(return_value=True)
		self.client.getPlayingFile = Mock(side_effect=('old-video', 'new-video'))
		self.client.provider_manager.download.return_value = {'content': 'subtitle text', 'extension': 'srt'}
		self.subtitles.kodi_utils.open_file = Mock()
		self.client.setSubtitles = Mock()

		with self.assertRaises(self.subtitles.SubtitleCancelled): self.client._searched_subs()
		self.subtitles.kodi_utils.open_file.assert_not_called()
		self.client.setSubtitles.assert_not_called()
		self.subtitles.kodi_utils.notification.assert_not_called()

	def test_playback_change_does_not_attach_cached_subtitle(self):
		self.client.expected_playing_file = 'old-video'
		self.client.isPlayingVideo = Mock(return_value=True)
		self.client.getPlayingFile = Mock(side_effect=('old-video', 'new-video'))
		self.client.setSubtitles = Mock()
		self.subtitles.kodi_utils.list_dirs.return_value = ([], ['fixture_eng.srt'])

		with self.assertRaises(self.subtitles.SubtitleCancelled): self.client._downloaded_subs()

		self.client.setSubtitles.assert_not_called()
		self.subtitles.kodi_utils.notification.assert_not_called()

	def test_playback_change_after_search_does_not_store_context_or_notify(self):
		self.client.expected_playing_file = 'old-video'
		self.client.isPlayingVideo = Mock(return_value=True)
		self.client.getPlayingFile = Mock(return_value='new-video')

		with self.assertRaises(self.subtitles.SubtitleCancelled): self.client._searched_subs()

		self.client._set_context.assert_not_called()
		self.client.provider_manager.download.assert_not_called()
		self.subtitles.kodi_utils.notification.assert_not_called()


if __name__ == '__main__':
	unittest.main()
