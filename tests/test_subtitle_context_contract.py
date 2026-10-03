import hashlib
import json
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_subtitles():
	from tests.test_subtitle_providers import load_providers
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.xbmc_player = object
	kodi_utils.set_property = Mock()
	kodi_utils.get_property = Mock(return_value='')
	kodi_utils.player = Mock()
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	indexers = types.ModuleType('indexers')
	indexers.__path__ = []
	return load_module('test_subtitle_context_contract_indexer', ROOT / 'resources/lib/indexers/subtitles.py', {
		'modules': modules, 'modules.kodi_utils': kodi_utils, 'indexers': indexers, 'indexers.subtitle_providers': load_providers()
	})


def load_service(subtitles):
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.player = Mock()
	kodi_utils.logger = Mock()
	kodi_utils.notification = Mock()
	kodi_utils.make_listitem = Mock()
	kodi_utils.add_item = Mock()
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	indexers = types.ModuleType('indexers')
	indexers.__path__ = []
	return load_module('test_subtitle_context_contract_service', ROOT / 'resources/lib/subtitle_service.py', {
		'modules': modules, 'modules.kodi_utils': kodi_utils, 'indexers': indexers, 'indexers.subtitles': subtitles
	})


class SubtitleContextContractTests(unittest.TestCase):
	def setUp(self):
		self.subtitles = load_subtitles()

	def test_context_fingerprints_playing_file_and_never_persists_it(self):
		client = self.subtitles.Subtitles().configure('tt123', expected_playing_file='https://stream.invalid/private?token=secret')
		client.getPlayingFile = Mock(return_value='https://stream.invalid/private?token=secret')

		context = client._set_context()

		payload = self.subtitles.kodi_utils.set_property.call_args.args[1]
		self.assertNotIn('stream.invalid', payload)
		self.assertNotIn('secret', payload)
		self.assertEqual(context['playing_fingerprint'], hashlib.sha256(b'https://stream.invalid/private?token=secret').hexdigest())
		self.assertEqual(context['version'], 2)

	def test_context_keeps_safe_poster_without_query_credentials(self):
		client = self.subtitles.Subtitles().configure('tt123', poster='https://image.invalid/poster.jpg?token=secret')
		client.getPlayingFile = Mock(return_value='movie.mkv')

		context = client._set_context()

		self.assertEqual(context['poster'], 'https://image.invalid/poster.jpg')
		self.assertNotIn('secret', json.dumps(context))

	def test_manual_override_fingerprints_current_file_and_keeps_matching_generation(self):
		playing_file = 'https://stream.invalid/private?token=secret'
		fingerprint = self.subtitles.playing_file_fingerprint(playing_file)
		self.subtitles.kodi_utils.player.isPlayingVideo.return_value = True
		self.subtitles.kodi_utils.player.getPlayingFile.return_value = playing_file
		self.subtitles.kodi_utils.get_property.return_value = json.dumps({'playing_fingerprint': fingerprint, 'generation': 'current-generation'})
		self.assertTrue(self.subtitles.mark_manual_selection())
		property_name, payload = self.subtitles.kodi_utils.set_property.call_args.args
		self.assertEqual(property_name, self.subtitles.subtitle_manual_override_property)
		self.assertEqual(json.loads(payload), {'playing_fingerprint': fingerprint, 'generation': 'current-generation'})
		self.assertNotIn('stream.invalid', payload)
		self.assertNotIn('secret', payload)

	def test_manual_override_does_not_reuse_stale_or_invalid_context_generation(self):
		self.subtitles.kodi_utils.player.isPlayingVideo.return_value = True
		self.subtitles.kodi_utils.player.getPlayingFile.return_value = 'video.mkv'
		for context in ('broken json', '[]', json.dumps({'playing_fingerprint': self.subtitles.playing_file_fingerprint('other.mkv'), 'generation': 'other-generation'})):
			with self.subTest(context=context):
				self.subtitles.kodi_utils.get_property.return_value = context
				self.assertTrue(self.subtitles.mark_manual_selection())
				self.assertEqual(json.loads(self.subtitles.kodi_utils.set_property.call_args.args[1])['generation'], '')

	def test_manual_override_without_active_or_identifiable_video_is_ignored(self):
		for playing, filename in ((False, 'video.mkv'), (True, '')):
			with self.subTest(playing=playing, filename=filename):
				self.subtitles.kodi_utils.player.isPlayingVideo.return_value = playing
				self.subtitles.kodi_utils.player.getPlayingFile.return_value = filename
				self.assertFalse(self.subtitles.mark_manual_selection())
		self.subtitles.kodi_utils.set_property.assert_not_called()

	def test_candidate_projection_is_bounded_allowlisted_and_download_complete(self):
		client = self.subtitles.Subtitles().configure('tt123')
		client.getPlayingFile = Mock(return_value='movie.mkv')
		candidate = {
			'provider': 'subdl', 'id': 'file:12:34', 'lang': 'eng', 'extension': 'ass', 'release_names': ['Release.Name'],
			'score': 42.0, 'api_key': 'private', 'content': b'subtitle', 'url': 'https://signed.invalid/file?token=secret'
		}

		projected = client._set_context([candidate])['subtitles'][0]

		self.assertEqual({key: projected[key] for key in ('provider', 'id', 'lang', 'extension')}, {'provider': 'subdl', 'id': 'file:12:34', 'lang': 'eng', 'extension': 'ass'})
		self.assertEqual(projected['release'], 'Release.Name')
		self.assertIn('token', projected)
		self.assertNotIn('api_key', projected)
		self.assertNotIn('content', projected)
		self.assertNotIn('url', projected)
		self.assertLessEqual(len(json.dumps(client._set_context([candidate] * 200)).encode('utf-8')), self.subtitles.subtitle_context_max_bytes)

	def test_manual_download_uses_generation_bound_candidate_without_research(self):
		service = load_service(self.subtitles)
		client, payload = Mock(), {'content': b'subtitle', 'extension': 'srt'}
		client.subtitle_path, client.sub_filename = 'special://temp/', 'fixture'
		client._cancelled.return_value = False
		client._safe_extension.return_value = 'srt'
		client.download_candidate.return_value = payload
		client.save_subtitle.return_value = True
		context = {
			'version': 2, 'generation': 'playback-generation',
			'subtitles': [{'token': 'selected-token', 'provider': 'subdl', 'id': 'file:12:34', 'lang': 'eng', 'extension': 'srt'}]
		}
		service._client = Mock(return_value=(client, context))

		service._download(7, {'generation': 'playback-generation', 'candidate': 'selected-token', 'language': 'vie', 'result': '1'})

		client.download_candidate.assert_called_once_with(context['subtitles'][0])
		client.download_by_id.assert_not_called()
		self.assertIn('_eng_', client.save_subtitle.call_args.args[1])

	def test_first_manual_search_without_external_ids_keeps_metadata_identity(self):
		service = load_service(self.subtitles)
		service._context = lambda: {}
		service._video_metadata = lambda: {
			'imdb_id': '', 'tmdb_id': '', 'season': None, 'episode': None, 'is_episode': False,
			'mediatype': 'movie', 'title': 'Local Movie', 'year': 2024
		}
		service.kodi_utils.player.isPlayingVideo.return_value = True
		service.kodi_utils.player.getPlayingFile.return_value = 'smb://server/local-movie.mp4'

		client, context = service._client()

		self.assertEqual(context, {})
		self.assertTrue(client.media_identity.startswith('meta:'))
		self.assertNotIn('session_', client.sub_filename)

	def test_manual_download_rejects_candidate_from_previous_generation(self):
		service = load_service(self.subtitles)
		client = Mock()
		context = {'version': 2, 'generation': 'current', 'subtitles': [{'token': 'selected-token', 'provider': 'subdl', 'id': 'file:12:34', 'lang': 'eng'}]}
		service._client = Mock(return_value=(client, context))

		service._download(7, {'generation': 'previous', 'candidate': 'selected-token', 'language': 'eng', 'result': '1'})

		client.download_candidate.assert_not_called()
		client.download_by_id.assert_not_called()


if __name__ == '__main__':
	unittest.main()
