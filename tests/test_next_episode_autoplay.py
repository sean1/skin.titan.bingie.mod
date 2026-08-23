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
		watched_cache = types.ModuleType('caches.watched_cache')
		watched_cache.get_next_episodes = lambda *args: []
		with mock.patch.dict('sys.modules', {'caches.watched_cache': watched_cache}):
			episode_tools.SmartPlay({'tmdb_id': '123', 'autoplay': 'false'})

		episode_tools.Sources.factory.assert_called_once_with({
			'mode': 'play_media', 'mediatype': 'episode', 'tmdb_id': '123', 'season': 1, 'episode': 2, 'autoplay': 'false'
		})

	def test_queued_next_episode_uses_full_screen_progress(self):
		sources_module = load_sources_module()
		source = types.SimpleNamespace(background=False, params={'autoplay_next': 'true'})

		self.assertTrue(sources_module.ConfigLoader()._use_full_screen_progress(source))

	def test_background_preparation_stays_hidden_over_current_episode(self):
		sources_module = load_sources_module()
		source = types.SimpleNamespace(background=True, params={'autoplay_next': 'true'})

		self.assertFalse(sources_module.ConfigLoader()._use_full_screen_progress(source))

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

	def test_resume_save_has_no_forced_cleanup_sleep(self):
		player_module = load_player()
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player_module.ws.set_bookmark = mock.Mock()
		player_module.kodi_utils.sleep = mock.Mock()

		player.exec_task('media_bookmark', 'movie', '101', 100, 200, 'Movie', '', '', 'progress')

		player_module.ws.set_bookmark.assert_called_once_with('movie', '101', 100, 200, 'Movie', '', '', 'progress')
		player_module.kodi_utils.sleep.assert_not_called()

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
		player.stop = mock.Mock()

		player.request_next_episode()

		self.assertTrue(player.next_episode_requested)
		player.stop.assert_called_once_with()


if __name__ == '__main__':
	unittest.main()
