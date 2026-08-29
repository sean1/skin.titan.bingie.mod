from datetime import date
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.module_isolation import load_module
from tests.test_audio_selection import load_player
from tests.test_external_manager import load_sources_module
from tests.test_settings_cleanup import load_kodi_utils


ROOT = Path(__file__).resolve().parents[1]


def load_settings(values):
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.local_string = str
	kodi_utils.translate_path = lambda value: value
	kodi_utils.get_setting = lambda key, fallback=None: values.get(key, fallback)
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	path = ROOT / 'resources' / 'lib' / 'modules' / 'settings.py'
	return load_module('test_next_episode_settings', path, {'modules': modules, 'modules.kodi_utils': kodi_utils})


def load_episode_tools():
	windows = types.ModuleType('windows')
	windows.open_window = lambda *args, **kwargs: None
	metadata = types.ModuleType('indexers.metadata')
	metadata.tvshow_meta = lambda *args: {}
	metadata.season_episodes_meta = lambda *args: [{'episode': 2, 'premiered': '2025-01-01', 'title': 'Second', 'plot': 'Next plot'}]
	metadata.all_episodes_meta = lambda *args: []
	indexers = types.ModuleType('indexers')
	indexers.__path__ = []
	indexers.metadata = metadata
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.local_string = str
	kodi_utils.build_url = lambda params: 'plugin://test'
	settings = types.ModuleType('modules.settings')
	settings.date_offset = lambda: 0
	settings.metadata_user_info = lambda: {}
	utils = types.ModuleType('modules.utils')
	utils.get_next_episode_pointer = lambda *args: (1, 2, False)
	utils.adjust_premiered_date = lambda value, hours: (date(2025, 1, 1), value)
	utils.get_datetime = lambda: date(2025, 1, 2)
	sources = types.ModuleType('modules.sources')
	sources.Sources = type('Sources', (), {})
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	modules.settings = settings
	stubs = {
		'windows': windows, 'indexers': indexers, 'indexers.metadata': metadata, 'modules': modules,
		'modules.kodi_utils': kodi_utils, 'modules.settings': settings, 'modules.utils': utils, 'modules.sources': sources
	}
	path = ROOT / 'resources' / 'lib' / 'modules' / 'episode_tools.py'
	return load_module('test_next_episode_tools', path, stubs)


class NextEpisodeAutoplayTests(unittest.TestCase):
	def test_feature_is_enabled_by_default(self):
		settings = load_kodi_utils().FIXED_SETTINGS
		self.assertEqual(settings['auto_play_movie'], 'true')
		self.assertEqual(settings['auto_play_episode'], 'true')
		self.assertEqual(settings['autoplay_next_episode'], 'true')

	def test_feature_does_not_require_initial_episode_autoplay(self):
		settings = load_settings({'auto_play_episode': 'false', 'autoplay_next_episode': 'true'})
		self.assertTrue(settings.autoplay_next_episode())

	def test_resolved_next_episode_is_marked_for_autoplay(self):
		episode_tools = load_episode_tools()
		meta = {'title': 'Example', 'rootname': 'Example', 'tmdb_id': '123', 'season': 1, 'episode': 1, 'total_seasons': 1}

		_, params = episode_tools.nextep_playback_info(meta)

		self.assertEqual(params['autoplay'], 'true')
		self.assertEqual(params['autoplay_next'], 'true')
		self.assertEqual((params['season'], params['episode']), (1, 2))

	def test_explicit_manual_smart_play_removes_next_episode_autoplay(self):
		episode_tools = load_episode_tools()
		episode_tools.settings.metadata_user_info = lambda: {}
		episode_tools.settings.watched_indicators = lambda: 'trakt'
		episode_tools.tvshow_meta = lambda *args: {'title': 'Example', 'tmdb_id': '123'}
		episode_tools.nextep_playback_info = mock.Mock(return_value=({}, {
			'mode': 'play_media', 'mediatype': 'episode', 'tmdb_id': '123', 'season': 1, 'episode': 2,
			'autoplay': 'true', 'autoplay_next': 'true'
		}))
		episode_tools.Sources.factory = mock.Mock()
		smartplay_cache = types.ModuleType('caches.smartplay_cache')
		smartplay_cache.lookup = lambda *args: None
		with mock.patch.dict('sys.modules', {'caches.smartplay_cache': smartplay_cache}):
			episode_tools.SmartPlay({'tmdb_id': '123', 'autoplay': 'false'})

		episode_tools.Sources.factory.assert_called_once_with({
			'mode': 'play_media', 'mediatype': 'episode', 'tmdb_id': '123', 'season': 1, 'episode': 2, 'autoplay': 'false'
		})

	def test_smart_play_resolves_after_the_anonymous_completed_episode_cursor(self):
		episode_tools = load_episode_tools()
		episode_tools.tvshow_meta = lambda *args: {'title': 'Example', 'tmdb_id': '123'}
		episode_tools.nextep_playback_info = mock.Mock(return_value=({}, {'mode': 'play_media'}))
		episode_tools.Sources.factory = mock.Mock()
		smartplay_cache = types.ModuleType('caches.smartplay_cache')
		smartplay_cache.lookup = lambda tmdb_id: (2, 3)

		with mock.patch.dict('sys.modules', {'caches.smartplay_cache': smartplay_cache}): episode_tools.SmartPlay({'tmdb_id': '123'})

		cursor_meta = episode_tools.nextep_playback_info.call_args.args[0]
		self.assertEqual((cursor_meta['season'], cursor_meta['episode']), (2, 3))
		episode_tools.Sources.factory.assert_called_once_with({'mode': 'play_media'})

	def test_queued_next_episode_uses_full_screen_progress(self):
		sources_module = load_sources_module()
		source = types.SimpleNamespace(background=False, params={'autoplay_next': 'true'})

		self.assertTrue(sources_module.ConfigLoader()._use_full_screen_progress(source))

	def test_background_preparation_stays_hidden_over_current_episode(self):
		sources_module = load_sources_module()
		source = types.SimpleNamespace(background=True, params={'autoplay_next': 'true'})

		self.assertFalse(sources_module.ConfigLoader()._use_full_screen_progress(source))

	def test_next_episode_queue_keeps_only_latest_request(self):
		sources_module = load_sources_module()
		sources_module.Sources.clear_nextep()
		sources_module.Sources.nextep_callback({'episode': 2})
		sources_module.Sources.nextep_callback({'episode': 3})

		self.assertEqual(sources_module.Sources.nextep_params, [{'episode': 3}])
		self.assertEqual(sources_module.Sources._pop_nextep(), {'episode': 3})
		self.assertEqual(sources_module.Sources.nextep_params, [])

	def test_manual_stop_clears_queued_next_episode(self):
		player_module = load_player()
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.playback_event = True
		player.ignore_startup_stop = False
		player.startup_playback_started = True
		player.next_episode_requested = False
		sources = types.SimpleNamespace(nextep_params=[{'episode': 2}])
		player_module.kodi_utils.clear_property = mock.Mock()

		with mock.patch.dict('sys.modules', {'modules.sources': types.SimpleNamespace(Sources=sources)}):
			player.onPlayBackStopped()

		self.assertEqual(sources.nextep_params, [])
		player_module.kodi_utils.clear_property.assert_called_once_with('pov_lite_total_autoplays')

	def test_natural_end_preserves_queued_next_episode(self):
		player_module = load_player()
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.playback_event = True
		player.ignore_startup_stop = False
		player.startup_playback_started = True
		sources = types.SimpleNamespace(nextep_params=[{'episode': 2}])

		with mock.patch.dict('sys.modules', {'modules.sources': types.SimpleNamespace(Sources=sources)}):
			player.onPlayBackEnded()

		self.assertEqual(sources.nextep_params, [{'episode': 2}])

	def test_playback_error_requests_retry_at_current_position(self):
		player_module = load_player()
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.getTotalTime = lambda: 1000
		player.getTime = lambda: 375
		player.playback_event = True

		player.onPlayBackError()

		self.assertTrue(player.playback_error)
		self.assertFalse(player.playback_event)
		self.assertEqual(player.retry_resume_percent, 37.5)

	def test_startup_playback_error_does_not_capture_stale_position(self):
		player_module = load_player()
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.playback_event = None
		player.playback_error = True
		player.retry_resume_percent = 72
		player.getTotalTime = lambda: 1000
		player.getTime = lambda: 720

		player.onPlayBackError()

		self.assertFalse(player.playback_error)
		self.assertFalse(player.playback_event)
		self.assertEqual(player.retry_resume_percent, 0)

	def test_startup_timeout_stops_pending_play_request(self):
		player_module = load_player()
		player_module.kodi_utils.logger = mock.Mock()
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		listitem = mock.Mock()
		player.art_provider = ()
		player.bookmarkPOV = lambda: 0
		player.make_listitem = lambda: listitem
		player.isPlaying = lambda: False
		player.play = mock.Mock()
		player.stop = mock.Mock()
		player._record_playback_health = mock.Mock()

		with mock.patch.object(player_module, 'PLAYBACK_START_TIMEOUT', 0):
			result = player.run('https://stream.invalid/movie', {'title': 'Movie', 'year': 2025, 'mediatype': 'movie', 'tmdb_id': '1'})

		self.assertFalse(result)
		player.play.assert_called_once()
		player.stop.assert_called_once_with()
		self.assertTrue(player.ignore_startup_stop)
		self.assertFalse(player.startup_playback_started)
		self.assertTrue(player.startup_cancel_requested)
		player._record_playback_health.assert_called_once()

		player.onAVStarted()
		self.assertFalse(player.playback_event)
		self.assertEqual(player.stop.call_count, 2)

	def test_callback_startup_failure_waits_for_teardown(self):
		player_module = load_player()
		player_module.kodi_utils.logger = mock.Mock()
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		listitem = mock.Mock()
		player.art_provider = ()
		player.bookmarkPOV = lambda: 0
		player.make_listitem = lambda: listitem
		player.isPlaying = lambda: False
		player._stop_and_wait = mock.Mock()
		player._record_playback_health = mock.Mock()
		player.play = lambda *args: setattr(player, 'playback_event', False)

		result = player.run('https://stream.invalid/movie', {'title': 'Movie', 'year': 2025, 'mediatype': 'movie', 'tmdb_id': '1'})

		self.assertFalse(result)
		player._stop_and_wait.assert_called_once_with(force=False)

	def test_stop_waits_for_player_release_and_settle(self):
		player_module = load_player()
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		states = iter((True, True, False))
		media_states = iter((True, False))
		player.isPlaying = lambda: next(states)
		player.stop = mock.Mock()
		player_module.kodi_utils.get_visibility = lambda condition: next(media_states)
		player_module.kodi_utils.monitor.waitForAbort = mock.Mock(return_value=False)

		player._stop_and_wait()

		player.stop.assert_called_once_with()
		self.assertEqual([call.args[0] for call in player_module.kodi_utils.monitor.waitForAbort.call_args_list], [0.1, 0.5])

	def test_stop_and_end_do_not_request_error_recovery(self):
		player_module = load_player()
		for callback in ('onPlayBackStopped', 'onPlayBackEnded'):
			with self.subTest(callback=callback):
				player = player_module.POVPlayer.__new__(player_module.POVPlayer)
				player.playback_event = True
				player.playback_error = False
				player.retry_resume_percent = 0
				player.ignore_startup_stop = False
				player.startup_playback_started = True
				player.next_episode_requested = True

				getattr(player, callback)()

				self.assertFalse(player.playback_error)
				self.assertEqual(player.retry_resume_percent, 0)

	def test_implausibly_short_playback_is_rejected(self):
		player_module = load_player()
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.meta_get = {'duration': 7200}.get
		player.getTotalTime = lambda: 177

		self.assertFalse(player._duration_is_plausible())

	def test_short_episode_with_matching_metadata_is_allowed(self):
		player_module = load_player()
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.meta_get = {'duration': 1500}.get
		player.getTotalTime = lambda: 1200

		self.assertTrue(player._duration_is_plausible())

	def test_longer_edition_is_allowed(self):
		player_module = load_player()
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.meta_get = {'duration': 7200}.get
		player.getTotalTime = lambda: 8100

		self.assertTrue(player._duration_is_plausible())

	def test_missing_expected_duration_is_allowed(self):
		player_module = load_player()
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.meta_get = {}.get
		player.getTotalTime = lambda: 177

		self.assertTrue(player._duration_is_plausible())

	def test_resume_save_has_no_forced_cleanup_sleep(self):
		player_module = load_player()
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player_module.ws.set_bookmark = mock.Mock()
		player_module.kodi_utils.sleep = mock.Mock()

		player.exec_task('media_bookmark', 'movie', '101', 100, 200, 'Movie', '', '', 'progress')

		player_module.ws.set_bookmark.assert_called_once_with('movie', '101', 100, 200, 'Movie', '', '', 'progress')
		player_module.kodi_utils.sleep.assert_not_called()

	def test_completed_playback_erases_resume_without_recording_watched(self):
		player_module = load_player()
		player_module.kodi_utils.clear_property = mock.Mock()
		player_module.ws.erase_bookmark = mock.Mock(return_value=True)
		smartplay_cache = types.ModuleType('caches.smartplay_cache')
		smartplay_cache.complete_episode = mock.Mock(return_value=(True, True))
		player_module.ws.mark_as_watched_unwatched_movie = mock.Mock()
		player_module.ws.mark_as_watched_unwatched_episode = mock.Mock()
		for mediatype, season, episode in (('movie', '', ''), ('episode', 2, 3)):
			with self.subTest(mediatype=mediatype):
				player = player_module.POVPlayer.__new__(player_module.POVPlayer)
				player.media_marked = False
				player.current_point = 95
				player.set_watched = 90
				player.mediatype = mediatype
				player.tmdb_id = '101'
				player.season = season
				player.episode = episode
				player.meta = {}

				with mock.patch.dict('sys.modules', {'caches.smartplay_cache': smartplay_cache}): player.media_watched_marker()

				self.assertTrue(player.media_marked)
				if mediatype == 'movie': player_module.ws.erase_bookmark.assert_called_with(mediatype, '101', season, episode, 'progress')
				else: smartplay_cache.complete_episode.assert_called_once_with('101', 2, 3)
		player_module.ws.mark_as_watched_unwatched_movie.assert_not_called()
		player_module.ws.mark_as_watched_unwatched_episode.assert_not_called()

	def test_completed_random_episode_does_not_advance_linear_smartplay_cursor(self):
		player_module = load_player()
		player_module.kodi_utils.clear_property = mock.Mock()
		player_module.ws.erase_bookmark = mock.Mock(return_value=True)
		smartplay_cache = types.ModuleType('caches.smartplay_cache')
		smartplay_cache.complete_episode = mock.Mock()
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.media_marked, player.current_point, player.set_watched = False, 95, 90
		player.mediatype, player.tmdb_id, player.season, player.episode = 'episode', '101', 5, 10
		player.meta = {'random': 'true'}

		with mock.patch.dict('sys.modules', {'caches.smartplay_cache': smartplay_cache}): player.media_watched_marker()

		player_module.ws.erase_bookmark.assert_called_once_with('episode', '101', 5, 10, 'progress')
		smartplay_cache.complete_episode.assert_not_called()

	def test_completed_special_uses_completion_cache_to_clear_progress_without_advancing_cursor(self):
		player_module = load_player()
		player_module.kodi_utils.clear_property = mock.Mock()
		smartplay_cache = types.ModuleType('caches.smartplay_cache')
		smartplay_cache.complete_episode = mock.Mock(return_value=(False, True))
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.media_marked, player.current_point, player.set_watched = False, 95, 90
		player.mediatype, player.tmdb_id, player.season, player.episode = 'episode', '101', 0, 4
		player.meta = {}

		with mock.patch.dict('sys.modules', {'caches.smartplay_cache': smartplay_cache}): player.media_watched_marker()

		smartplay_cache.complete_episode.assert_called_once_with('101', 0, 4)

	def test_credit_marker_controls_popup_timing(self):
		player_module = load_player()
		player_module.settings.autoplay_next_settings = lambda: {
			'scraper_time': 40, 'run_popup': True, 'timer_method': 'time', 'window_time': 21, 'window_percentage': 5, 'autoscrape_next_window_time': 20
		}
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.credits = 900
		player.getTotalTime = lambda: 1000

		player.info_next_ep()

		self.assertEqual(player.nextep_settings['window_time'], 105)
		self.assertEqual(player.start_prep, 166)

	def test_missing_credit_marker_uses_final_21_seconds(self):
		player_module = load_player()
		player_module.settings.autoplay_next_settings = lambda: {
			'scraper_time': 40, 'run_popup': True, 'timer_method': 'time', 'window_time': 21, 'window_percentage': 5, 'autoscrape_next_window_time': 20
		}
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.credits = None
		player.getTotalTime = lambda: 1000

		player.info_next_ep()

		self.assertEqual(player.nextep_settings['window_time'], 21)
		self.assertEqual(player.start_prep, 82)

	def test_play_now_preserves_queue_when_stopping_current_episode(self):
		player_module = load_player()
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.next_episode_requested = False
		player._stop_and_wait = mock.Mock()

		player.request_next_episode()

		self.assertTrue(player.next_episode_requested)
		player._stop_and_wait.assert_called_once_with(force=True)


if __name__ == '__main__':
	unittest.main()
