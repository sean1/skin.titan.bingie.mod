import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'resources' / 'lib'))


def load_player():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.xbmc_player = object
	kodi_utils.get_kodi_version = lambda: 21
	kodi_utils.make_cast_list = lambda value: value
	kodi_utils.local_string = str
	kodi_utils.get_setting = lambda key, default=None: default
	kodi_utils.get_addoninfo = lambda key: ''
	kodi_utils.media_path = lambda value: value
	kodi_utils.monitor = types.SimpleNamespace(waitForAbort=lambda timeout: False)
	kodi_utils.xbmc = types.SimpleNamespace(ISO_639_2=2, convertLanguage=lambda language, language_format: {'ca': 'cat'}.get(language, language))

	settings = types.ModuleType('modules.settings')
	settings.get_art_provider = lambda: ()
	settings.metadata_user_info = lambda: {}
	settings.autoplay_next_episode = lambda: False
	settings.autoscrape_next_episode = lambda: False
	tmdb_api = types.ModuleType('indexers.tmdb_api')
	tmdb_api.media_original_language = lambda mediatype, tmdb_id: ''
	indexers = types.ModuleType('indexers')
	indexers.__path__ = []
	indexers.tmdb_api = tmdb_api

	stubs = {
		'caches': types.SimpleNamespace(watched_cache=types.SimpleNamespace()),
		'windows': types.SimpleNamespace(open_window=lambda *args: None),
		'indexers': indexers,
		'indexers.segments': types.SimpleNamespace(SegmentScraper=object),
		'indexers.tmdb_api': tmdb_api,
		'indexers.metadata': types.SimpleNamespace(
			art_infodict=lambda *args: {}, movie_show_infodict=lambda *args: {}, episode_infodict=lambda *args, **kwargs: {},
			info_tagger=lambda *args: None, resized_cast=lambda value: value
		),
		'modules.kodi_utils': kodi_utils,
		'modules.settings': settings,
		'modules.utils': types.SimpleNamespace(sec2time=lambda value: value),
	}
	path = ROOT / 'resources' / 'lib' / 'modules' / 'player.py'
	return load_module('test_audio_player_module', path, stubs)


class AudioSelectionTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.player_module = load_player()
		cls.player_class = cls.player_module.POVPlayer

	def setUp(self):
		self.player_module.tmdb_api.media_original_language = lambda mediatype, tmdb_id: ''

	def test_english_wins_over_original_language_and_default(self):
		streams = [
			{'index': 0, 'language': 'cze', 'isdefault': True},
			{'index': 1, 'language': 'chi', 'isoriginal': True},
			{'index': 2, 'language': 'eng'},
		]
		self.assertEqual(self.player_class._preferred_audio_stream(streams, 'zh')['index'], 2)

	def test_original_language_alias_selects_live_movie_track(self):
		streams = [
			{'index': 0, 'language': 'cze', 'isdefault': True, 'name': 'AC3 0 channels'},
			{'index': 1, 'language': 'chi', 'name': 'Encode TrueHD 7.1 Atmos'},
			{'index': 2, 'language': 'chi', 'name': 'Encode DD+ 5.1 Atmos'},
			{'index': 3, 'language': 'chi', 'name': 'Original DTS-HD MA 5.1'},
		]
		self.assertEqual(self.player_class._preferred_audio_stream(streams, 'zh')['index'], 3)

	def test_normal_original_language_track_beats_commentary(self):
		streams = [
			{'index': 0, 'language': 'jpn', 'name': 'Director Commentary'},
			{'index': 1, 'language': 'jpn', 'name': 'Main Audio'},
		]
		self.assertEqual(self.player_class._preferred_audio_stream(streams, 'ja')['index'], 1)

	def test_english_commentary_does_not_beat_normal_original_language(self):
		streams = [
			{'index': 0, 'language': 'eng', 'name': 'Director Commentary'},
			{'index': 1, 'language': 'jpn', 'name': 'Main Audio'},
		]
		self.assertEqual(self.player_class._preferred_audio_stream(streams, 'ja')['index'], 1)

	def test_iso_639_aliases_match_original_language(self):
		for original_language, stream_language in (('zh', 'zho'), ('ka', 'kat'), ('sq', 'sqi'), ('ca', 'cat')):
			with self.subTest(original_language=original_language, stream_language=stream_language):
				streams = [{'index': 0, 'language': 'cze'}, {'index': 1, 'language': stream_language}]
				self.assertEqual(self.player_class._preferred_audio_stream(streams, original_language)['index'], 1)

	def test_original_marker_fallback_works_without_metadata(self):
		for stream in (
			{'index': 1, 'language': 'kor', 'isoriginal': True},
			{'index': 2, 'language': 'chi', 'name': 'Mandarin (ORIGINAL)'},
		):
			with self.subTest(stream=stream):
				self.assertEqual(self.player_class._preferred_audio_stream([{'index': 0, 'language': 'cze'}, stream])['index'], stream['index'])

	def test_no_original_language_evidence_leaves_kodi_selection_unchanged(self):
		streams = [{'index': 0, 'language': 'cze', 'isdefault': True}, {'index': 1, 'language': 'spa'}]
		self.assertIsNone(self.player_class._preferred_audio_stream(streams))

	def test_live_selection_switches_once_and_skips_already_selected_track(self):
		streams = [{'index': 0, 'language': 'cze', 'isdefault': True}, {'index': 1, 'language': 'chi'}]
		player = self.player_class.__new__(self.player_class)
		player.meta_get = {'original_language': 'zh'}.get
		player.meta = {'original_language': 'zh'}
		player.mediatype = 'movie'
		player.tmdb_id = '1419406'
		player.isPlayingVideo = lambda: True
		player.getPlayingFile = lambda: 'movie.mkv'
		selected = []
		player.setAudioStream = selected.append

		def response(current_index):
			return json.dumps({'result': {'audiostreams': streams, 'currentaudiostream': {'index': current_index}}})

		self.player_module.kodi_utils.execJSONRPC = lambda request: response(0)
		player._select_preferred_audio()
		self.assertEqual(selected, [1])
		self.player_module.kodi_utils.execJSONRPC = lambda request: response(1)
		player._select_preferred_audio()
		self.assertEqual(selected, [1])

	def test_legacy_metadata_looks_up_original_language_only_when_needed(self):
		streams = [{'index': 0, 'language': 'cze', 'isdefault': True}, {'index': 1, 'language': 'jpn'}]
		player = self.player_class.__new__(self.player_class)
		player.meta = {}
		player.meta_get = player.meta.get
		player.mediatype = 'movie'
		player.tmdb_id = 'legacy-id'
		player.isPlayingVideo = lambda: True
		player.getPlayingFile = lambda: 'legacy.mkv'
		player.setAudioStream = mock.Mock()
		self.player_module.kodi_utils.execJSONRPC = lambda request: json.dumps({'result': {'audiostreams': streams, 'currentaudiostream': {'index': 0}}})
		self.player_module.tmdb_api.media_original_language = mock.Mock(return_value='ja')

		player._select_preferred_audio('legacy.mkv')
		player.setAudioStream.assert_called_once_with(1)
		self.assertEqual(player.meta['original_language'], 'ja')

	def test_stale_audio_selection_does_not_touch_new_playback(self):
		player = self.player_class.__new__(self.player_class)
		player.isPlayingVideo = lambda: True
		player.getPlayingFile = lambda: 'new.mkv'
		self.player_module.kodi_utils.execJSONRPC = mock.Mock()

		player._select_preferred_audio('old.mkv')
		self.player_module.kodi_utils.execJSONRPC.assert_not_called()


if __name__ == '__main__':
	unittest.main()
