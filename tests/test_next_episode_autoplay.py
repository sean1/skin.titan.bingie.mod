from datetime import date
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.module_isolation import load_module
from tests.test_audio_selection import load_player


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
	kodi_utils.get_property = mock.Mock(return_value='')
	kodi_utils.set_property = mock.Mock()
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


	def test_smart_play_uses_manual_sources_unless_autoplay_is_explicit(self):
		for autoplay in (None, 'false', 'true'):
			with self.subTest(autoplay=autoplay):
				episode_tools = load_episode_tools()
				episode_tools.tvshow_meta = lambda *args: {'title': 'Example', 'tmdb_id': '123'}
				episode_tools.nextep_playback_info = mock.Mock(return_value=({}, {
					'mode': 'play_media', 'mediatype': 'episode', 'tmdb_id': '123', 'season': 1, 'episode': 2,
					'autoplay': 'true', 'autoplay_next': 'true'
				}))
				episode_tools.Sources.factory = mock.Mock()
				smartplay_cache = types.ModuleType('caches.smartplay_cache')
				smartplay_cache.lookup = lambda *args: None
				params = {'tmdb_id': '123'}
				if autoplay is not None: params['autoplay'] = autoplay
				with mock.patch.dict('sys.modules', {'caches.smartplay_cache': smartplay_cache}): episode_tools.SmartPlay(params)

				expected = {'mode': 'play_media', 'mediatype': 'episode', 'tmdb_id': '123', 'season': 1, 'episode': 2, 'autoplay': 'false'}
				if autoplay == 'true': expected.update({'autoplay': 'true', 'autoplay_next': 'true'})
				episode_tools.Sources.factory.assert_called_once_with(expected)


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


if __name__ == '__main__':
	unittest.main()
