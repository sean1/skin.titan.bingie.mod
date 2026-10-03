import json
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from tests.module_isolation import load_module, temporary_modules


ROOT = Path(__file__).resolve().parents[1]


def load_subtitles():
	from tests.test_subtitle_providers import load_providers
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.xbmc_player = object
	kodi_utils.logger = Mock()
	kodi_utils.delete_file = Mock()
	kodi_utils.monitor = Mock()
	kodi_utils.list_dirs = Mock()
	kodi_utils.get_property = Mock(return_value='')
	kodi_utils.set_property = Mock()
	kodi_utils.execJSONRPC = Mock(return_value='{"result": {}}')
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	indexers = types.ModuleType('indexers')
	indexers.__path__ = []
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils, 'indexers': indexers, 'indexers.subtitle_providers': load_providers()}
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
		self.subtitles.kodi_utils.monitor.abortRequested.side_effect = None
		self.subtitles.kodi_utils.monitor.waitForAbort.side_effect = None
		self.subtitles.kodi_utils.monitor.abortRequested.return_value = False
		self.subtitles.kodi_utils.monitor.waitForAbort.return_value = False
		self.subtitles.kodi_utils.get_property.reset_mock(side_effect=True)
		self.subtitles.kodi_utils.get_property.return_value = ''
		self.subtitles.kodi_utils.set_property.reset_mock(side_effect=True)
		self.subtitles.kodi_utils.execJSONRPC.reset_mock(side_effect=True)
		self.subtitles.kodi_utils.execJSONRPC.return_value = '{"result": {}}'

	def automatic_fixture(self, enabled=True, current_index=0, streams=None):
		state = {'subtitleenabled': enabled, 'currentsubtitle': None if current_index is None else {'index': current_index}, 'subtitles': streams or []}
		self.client.isPlayingVideo = Mock(return_value=True)
		self.client.getPlayingFile = Mock(return_value='video.mkv')
		self.client.getAvailableSubtitleStreams = Mock(return_value=['English (Full)'])
		self.client.setSubtitleStream = Mock()
		self.client.showSubtitles = Mock()
		self.client.setSubtitles = Mock()
		self.client._subtitle_state = Mock(side_effect=lambda: state)
		manager = Mock()
		manager.search.return_value = self.candidates
		manager.download.return_value = {'content': 'subtitle text', 'extension': 'srt'}
		self.client._manager = Mock(return_value=manager)
		self.subtitles.kodi_utils.list_dirs.return_value = ([], [])
		self.subtitles.kodi_utils.open_file = Mock(return_value=RecordingFile([]))
		return state, manager

	def property_fixture(self):
		properties = {}
		self.subtitles.kodi_utils.get_property.side_effect = lambda key: properties.get(key, '')
		self.subtitles.kodi_utils.set_property.side_effect = properties.__setitem__
		self.subtitles.kodi_utils.player = self.client
		self.client._set_context = self.subtitles.Subtitles._set_context.__get__(self.client)
		return properties

	def playback_started(self, properties):
		modules = types.ModuleType('modules')
		modules.__path__ = []
		meta_lists = types.ModuleType('modules.meta_lists')
		meta_lists.meta_languages = {}
		with temporary_modules({'modules': modules, 'modules.meta_lists': meta_lists}):
			from tests.test_audio_selection import load_player
			player_module = load_player()
		player_module.kodi_utils.clear_property = lambda key: properties.pop(key, None)
		player_module.kodi_utils.hide_busy_dialog = Mock()
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.playback_event = None
		player.onPlayBackStarted()

	def test_remote_subtitle_closes_file_before_immediate_attach(self):
		events = []
		final_path = 'special://temp/fixture_eng_full.srt'
		self.subtitles.kodi_utils.open_file = Mock(return_value=RecordingFile(events))
		self.client.setSubtitles = Mock(side_effect=lambda path: events.append(('attach', path)))

		result = self.client._searched_subs()

		self.assertTrue(result)
		self.client.provider_manager.download.assert_called_once_with({**self.candidates[0], 'full_dialogue_only': True})
		self.assertNotIn('full_dialogue_only', self.candidates[0])
		self.subtitles.kodi_utils.open_file.assert_called_once_with(final_path, 'w')
		self.assertEqual(events, ['open', ('write', 'subtitle text'), 'close', ('attach', final_path)])
		self.subtitles.kodi_utils.sleep.assert_not_called()

	def test_embedded_english_wins_when_vietnamese_appears_first(self):
		self.client.getAvailableSubtitleStreams = Mock(return_value=['vie', 'spa', 'English (Full)'])
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

	def test_full_embedded_dialogue_wins_over_an_earlier_forced_track(self):
		self.client._subtitle_state = Mock(return_value={
			'subtitleenabled': True, 'currentsubtitle': {'index': 0, 'language': 'eng'},
			'subtitles': [{'index': 0, 'language': 'eng', 'name': 'English', 'isforced': True}, {'index': 1, 'language': 'eng', 'name': 'English', 'isforced': False}]
		})
		self.client.getAvailableSubtitleStreams = Mock(return_value=['eng', 'eng'])
		self.client.setSubtitleStream = Mock()
		self.client.showSubtitles = Mock()
		self.assertTrue(self.client._video_file_subs())
		self.client.setSubtitleStream.assert_called_once_with(1)
		self.client.showSubtitles.assert_called_once_with(True)

	def test_native_rpc_reads_track_names_and_forced_flags_from_the_active_video_player(self):
		state = {'subtitleenabled': True, 'currentsubtitle': {'index': 0}, 'subtitles': [{'index': 0, 'language': 'eng', 'name': 'English', 'isforced': True}]}
		requests = []
		def rpc(value):
			request = json.loads(value)
			requests.append(request)
			return json.dumps({'result': [{'playerid': 7, 'type': 'video'}] if request['method'] == 'Player.GetActivePlayers' else state})
		self.subtitles.kodi_utils.execJSONRPC.side_effect = rpc
		self.assertEqual(self.client._subtitle_state(), state)
		self.assertEqual([request['method'] for request in requests], ['Player.GetActivePlayers', 'Player.GetProperties'])
		self.assertEqual(requests[-1]['params'], {'playerid': 7, 'properties': ['subtitles', 'currentsubtitle', 'subtitleenabled']})

	def test_full_embedded_dialogue_wins_over_unknown_coverage_in_the_same_language(self):
		self.client.getAvailableSubtitleStreams = Mock(return_value=['English', 'English (Full)'])
		self.client.setSubtitleStream = Mock()
		self.client.showSubtitles = Mock()
		self.assertTrue(self.client._video_file_subs())
		self.client.setSubtitleStream.assert_called_once_with(1)

	def test_matching_language_tracks_with_unknown_coverage_remain_eligible(self):
		self.client.getAvailableSubtitleStreams = Mock(return_value=['eng'])
		self.client.setSubtitleStream = Mock()
		self.client.showSubtitles = Mock()
		self.assertTrue(self.client._video_file_subs())
		self.client.setSubtitleStream.assert_called_once_with(0)

	def test_forced_english_does_not_prevent_full_vietnamese_selection(self):
		self.client.getAvailableSubtitleStreams = Mock(return_value=['English (Forced)', 'Vietnamese (Full)'])
		self.client.setSubtitleStream = Mock()
		self.client.showSubtitles = Mock()
		self.assertTrue(self.client._video_file_subs())
		self.client.setSubtitleStream.assert_called_once_with(1)

	def test_forced_legacy_current_track_does_not_stop_full_download_fallback(self):
		self.client.getAvailableSubtitleStreams = Mock(side_effect=AttributeError)
		self.client.getSubtitles = Mock(return_value='English (Forced)')
		self.client.showSubtitles = Mock()
		self.assertFalse(self.client._video_file_subs())
		self.client.showSubtitles.assert_not_called()

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
		self.assertEqual(events, ['open', ('write', b'second subtitle'), 'close', ('attach', 'special://temp/fixture_eng_full.ass')])

	def test_automatic_search_skips_known_partial_candidates_without_hiding_manual_results(self):
		partial = {'provider': 'opensubtitles', 'id': 'forced', 'lang': 'eng', 'forced': True, 'release_names': ['Fixture']}
		full = {'provider': 'subdl', 'id': 'full', 'lang': 'eng', 'foreign_parts_only': False, 'release_names': ['Fixture']}
		self.client.provider_manager.search.return_value = [partial, full]
		self.subtitles.kodi_utils.open_file = Mock(return_value=RecordingFile([]))
		self.client.setSubtitles = Mock()
		self.assertTrue(self.client._searched_subs())
		self.assertEqual([invocation.args[0]['id'] for invocation in self.client.provider_manager.download.call_args_list], ['full'])
		self.client._set_context.assert_called_once_with([partial, full])

	def test_automatic_search_never_attaches_known_partial_only_results(self):
		partial = {'provider': 'opensubtitles', 'id': 'forced', 'lang': 'eng', 'release_names': ['Fixture.eng.forced.srt']}
		self.client.provider_manager.search.return_value = [partial]
		self.subtitles.kodi_utils.open_file = Mock()
		self.client.setSubtitles = Mock()
		self.assertFalse(self.client._searched_subs())
		self.client.provider_manager.download.assert_not_called()
		self.subtitles.kodi_utils.open_file.assert_not_called()
		self.client.setSubtitles.assert_not_called()

	def test_write_failure_closes_file_without_attach(self):
		events = []
		self.client.provider_manager.search.return_value = self.candidates[:1]
		self.subtitles.kodi_utils.open_file = Mock(return_value=RecordingFile(events, OSError('write failed')))
		self.client.setSubtitles = Mock()

		result = self.client._searched_subs()

		self.assertFalse(result)
		self.assertEqual(events, ['open', ('write', 'subtitle text'), 'close'])
		self.subtitles.kodi_utils.delete_file.assert_called_once_with('special://temp/fixture_eng_full.srt')
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
		self.subtitles.kodi_utils.delete_file.assert_called_once_with('special://temp/fixture_eng_full.srt')
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

	def test_automatic_cache_ignores_legacy_files_that_may_be_forced(self):
		self.subtitles.kodi_utils.list_dirs.return_value = ([], ['fixture_eng.srt', 'fixture_vie.ass', 'fixture_eng_subdl_1.srt'])
		self.client.setSubtitles = Mock()
		self.assertFalse(self.client._downloaded_subs())
		self.client.setSubtitles.assert_not_called()
		self.subtitles.kodi_utils.notification.assert_not_called()

	def test_late_default_index_is_readiness_and_full_track_still_wins(self):
		for initial_index in (None, -1):
			with self.subTest(initial_index=initial_index):
				state, manager = self.automatic_fixture(current_index=initial_index)
				self.client.getAvailableSubtitleStreams.return_value = ['eng', 'eng']
				def ready(delay):
					state.update({'currentsubtitle': {'index': 0}, 'subtitles': [{'index': 0, 'language': 'eng', 'isforced': True}, {'index': 1, 'language': 'eng', 'isforced': False}]})
					return False
				self.subtitles.kodi_utils.monitor.waitForAbort.side_effect = ready
				self.assertTrue(self.client.run('Fixture', 'tt123', None, None, ''))
				self.client.setSubtitleStream.assert_called_once_with(1)
				self.client.showSubtitles.assert_called_once_with(True)
				manager.search.assert_not_called()

	def test_user_off_during_initial_delay_cancels_before_attachment(self):
		state, manager = self.automatic_fixture()
		self.subtitles.kodi_utils.monitor.waitForAbort.side_effect = lambda delay: state.update({'subtitleenabled': False}) or False
		self.assertFalse(self.client.run('Fixture', 'tt123', None, None, ''))
		self.client.setSubtitleStream.assert_not_called()
		self.client.showSubtitles.assert_not_called()
		self.client.setSubtitles.assert_not_called()
		manager.search.assert_not_called()
		self.subtitles.kodi_utils.notification.assert_not_called()

	def test_user_stream_change_during_initial_delay_cancels_before_attachment(self):
		state, manager = self.automatic_fixture()
		self.subtitles.kodi_utils.monitor.waitForAbort.side_effect = lambda delay: state.update({'currentsubtitle': {'index': 1}}) or False
		self.assertFalse(self.client.run('Fixture', 'tt123', None, None, ''))
		self.client.setSubtitleStream.assert_not_called()
		self.client.showSubtitles.assert_not_called()
		manager.search.assert_not_called()

	def test_only_forced_embedded_tracks_search_and_download_full_dialogue(self):
		state, manager = self.automatic_fixture(streams=[{'index': 0, 'language': 'eng', 'isforced': True}])
		self.client.getAvailableSubtitleStreams.return_value = ['eng']
		self.assertTrue(self.client.run('Fixture', 'tt123', None, None, ''))
		manager.search.assert_called_once_with()
		manager.download.assert_called_once_with({**self.candidates[0], 'full_dialogue_only': True})
		self.client.setSubtitleStream.assert_not_called()
		self.client.setSubtitles.assert_called_once_with('special://temp/POVLiteSubs_tt123_eng_full.srt')

	def test_user_off_during_provider_search_cancels_before_download(self):
		state, manager = self.automatic_fixture(streams=[{'index': 0, 'language': 'eng', 'isforced': True}])
		self.client.getAvailableSubtitleStreams.return_value = ['eng']
		manager.search.side_effect = lambda: state.update({'subtitleenabled': False}) or self.candidates
		self.assertFalse(self.client.run('Fixture', 'tt123', None, None, ''))
		manager.download.assert_not_called()
		self.subtitles.kodi_utils.open_file.assert_not_called()
		self.client.setSubtitles.assert_not_called()
		self.subtitles.kodi_utils.notification.assert_not_called()

	def test_late_first_index_is_latched_so_a_later_manual_change_cancels_search(self):
		for initial_index in (None, -1):
			with self.subTest(initial_index=initial_index):
				state, manager = self.automatic_fixture(current_index=initial_index)
				self.client.getAvailableSubtitleStreams.return_value = ['eng']
				def ready(delay):
					state.update({'currentsubtitle': {'index': 0}, 'subtitles': [{'index': 0, 'language': 'eng', 'isforced': True}]})
					return False
				self.subtitles.kodi_utils.monitor.waitForAbort.side_effect = ready
				manager.search.side_effect = lambda: state.update({'currentsubtitle': {'index': 1}}) or self.candidates
				self.assertFalse(self.client.run('Fixture', 'tt123', None, None, ''))
				manager.download.assert_not_called()
				self.subtitles.kodi_utils.open_file.assert_not_called()
				self.client.setSubtitles.assert_not_called()

	def test_initial_disabled_then_ready_enabled_then_user_off_cancels_search(self):
		state, manager = self.automatic_fixture(enabled=False, current_index=None)
		self.client.getAvailableSubtitleStreams.return_value = ['eng']
		def ready(delay):
			state.update({'subtitleenabled': True, 'currentsubtitle': {'index': 0}, 'subtitles': [{'index': 0, 'language': 'eng', 'isforced': True}]})
			return False
		self.subtitles.kodi_utils.monitor.waitForAbort.side_effect = ready
		manager.search.side_effect = lambda: state.update({'subtitleenabled': False}) or self.candidates
		self.assertFalse(self.client.run('Fixture', 'tt123', None, None, ''))
		manager.download.assert_not_called()
		self.client.setSubtitles.assert_not_called()

	def test_manual_choice_during_search_cancels_automatic_job_for_same_file(self):
		state, manager = self.automatic_fixture(streams=[{'index': 0, 'language': 'eng', 'isforced': True}])
		self.client.getAvailableSubtitleStreams.return_value = ['eng']
		self.property_fixture()
		def manual_search():
			self.assertTrue(self.subtitles.mark_manual_selection())
			return self.candidates
		manager.search.side_effect = manual_search
		self.assertFalse(self.client.run('Fixture', 'tt123', None, None, ''))
		manager.download.assert_not_called()
		self.subtitles.kodi_utils.open_file.assert_not_called()
		self.client.setSubtitles.assert_not_called()

	def test_manual_choice_before_late_rpc_readiness_still_cancels_automatic_job(self):
		state, manager = self.automatic_fixture(enabled=False, current_index=None)
		self.property_fixture()
		def choose_before_readiness(delay):
			self.assertTrue(self.subtitles.mark_manual_selection())
			state.update({'subtitleenabled': True, 'currentsubtitle': {'index': 0}})
			return False
		self.subtitles.kodi_utils.monitor.waitForAbort.side_effect = choose_before_readiness
		self.assertFalse(self.client.run('Fixture', 'tt123', None, None, ''))
		self.client.setSubtitleStream.assert_not_called()
		self.client.showSubtitles.assert_not_called()
		manager.search.assert_not_called()

	def test_pre_run_manual_off_without_context_is_preserved(self):
		state, manager = self.automatic_fixture(enabled=False, current_index=None)
		self.property_fixture()
		self.assertTrue(self.subtitles.mark_manual_selection())
		self.client.configure = Mock(wraps=self.client.configure)
		self.assertFalse(self.client.run('Fixture', 'tt123', None, None, ''))
		self.client.configure.assert_not_called()
		self.client.showSubtitles.assert_not_called()
		manager.search.assert_not_called()

	def test_replaying_same_file_clears_previous_manual_choice_and_allows_automatic_full_subtitles(self):
		state, manager = self.automatic_fixture(enabled=False)
		properties = self.property_fixture()
		old_context = {'playing_fingerprint': self.subtitles.playing_file_fingerprint('video.mkv'), 'generation': 'previous-generation'}
		properties.update({self.subtitles.subtitle_context_property: json.dumps(old_context), self.subtitles.subtitle_manual_override_property: json.dumps(old_context), 'unrelated': 'keep'})
		self.playback_started(properties)
		self.assertNotIn(self.subtitles.subtitle_manual_override_property, properties)
		self.assertNotIn(self.subtitles.subtitle_context_property, properties)
		self.assertEqual(properties['unrelated'], 'keep')
		self.assertTrue(self.client.run('Fixture', 'tt123', None, None, ''))
		self.client.setSubtitleStream.assert_called_once_with(0)
		self.client.showSubtitles.assert_called_once_with(True)
		manager.search.assert_not_called()

	def test_manual_choice_after_new_playback_start_and_before_automatic_task_is_preserved(self):
		state, manager = self.automatic_fixture(enabled=False, current_index=None)
		properties = self.property_fixture()
		self.playback_started(properties)
		self.assertTrue(self.subtitles.mark_manual_selection())
		self.assertFalse(self.client.run('Fixture', 'tt123', None, None, ''))
		self.client.setSubtitleStream.assert_not_called()
		self.client.showSubtitles.assert_not_called()
		manager.search.assert_not_called()

	def test_old_generation_or_other_file_marker_does_not_cancel_active_job(self):
		self.automatic_fixture()
		properties = self.property_fixture()
		self.client.configure('tt123', expected_playing_file='video.mkv', context_generation='current-generation')
		self.client.automatic = True
		self.client.automatic_subtitle_index = 0
		self.client.automatic_subtitle_enabled = True
		for filename, generation in (('video.mkv', 'older-generation'), ('other.mkv', 'current-generation')):
			with self.subTest(filename=filename, generation=generation):
				properties[self.subtitles.subtitle_manual_override_property] = json.dumps({'playing_fingerprint': self.subtitles.playing_file_fingerprint(filename), 'generation': generation})
				self.assertFalse(self.client._cancelled())

	def test_explicit_manual_forced_download_remains_available_despite_override(self):
		state, manager = self.automatic_fixture()
		self.property_fixture()
		self.client.configure('tt123', expected_playing_file='video.mkv')
		self.client._set_context()
		self.assertTrue(self.subtitles.mark_manual_selection())
		partial = {'provider': 'opensubtitles', 'id': 'forced', 'lang': 'eng', 'forced': True}
		manager.find.return_value = partial
		self.assertFalse(self.client._cancelled())
		self.assertEqual(self.client.download_by_id('opensubtitles', 'forced'), manager.download.return_value)
		manager.download.assert_called_once_with(partial)

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
		self.subtitles.kodi_utils.list_dirs.return_value = ([], ['fixture_eng_full.srt'])

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
