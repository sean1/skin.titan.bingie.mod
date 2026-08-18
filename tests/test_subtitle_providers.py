import io
import json
import os
import threading
import types
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_providers():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.addon_path = str(ROOT) + '/'
	kodi_utils.logger = Mock()
	kodi_utils.monitor = Mock()
	kodi_utils.monitor.waitForAbort.return_value = False
	kodi_utils.path_exists = lambda path: Path(path).exists()

	class KodiFile:
		def __init__(self, path): self.path = path
		def __enter__(self): return self
		def __exit__(self, *_): return False
		def readBytes(self): return Path(self.path).read_bytes()

	kodi_utils.open_file = KodiFile
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	requests = types.ModuleType('requests')
	requests.Timeout = type('Timeout', (Exception,), {})
	requests.ConnectionError = type('ConnectionError', (Exception,), {})
	requests.request = Mock()
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils, 'requests': requests}
	return load_module('test_subtitle_provider_module', ROOT / 'resources/lib/indexers/subtitle_providers.py', stubs)


def archive_bytes(files):
	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
		for name, content in files: archive.writestr(name, content)
	return buffer.getvalue()


class SubtitleProviderTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.providers = load_providers()

	def setUp(self):
		self.providers.kodi_utils.logger.reset_mock()

	def test_private_config_loads_without_logging_secrets(self):
		secret = 'sentinel-private-value'
		with TemporaryDirectory() as directory:
			path = Path(directory) / 'providers.json'
			path.write_text(json.dumps({'opensubtitles': {'api_key': secret, 'user_agent': 'Test', 'username': 'user', 'password': secret}, 'subdl': {'api_key': secret}, 'subsource': {'api_key': secret}}))
			path.chmod(0o600)
			config = self.providers.load_provider_config(str(path))

		self.assertEqual(set(config), {'opensubtitles', 'subdl', 'subsource'})
		self.assertNotIn(secret, repr(self.providers.kodi_utils.logger.call_args_list))

	def test_insecure_private_config_is_rejected_without_logging_secrets(self):
		secret = 'sentinel-private-value'
		with TemporaryDirectory() as directory:
			path = Path(directory) / 'providers.json'
			path.write_text(json.dumps({'subdl': {'api_key': secret}}))
			path.chmod(0o644)
			config = self.providers.load_provider_config(str(path))

		self.assertEqual(config, {})
		self.assertNotIn(secret, repr(self.providers.kodi_utils.logger.call_args_list))

	def test_rank_preserves_absolute_english_priority(self):
		media = {'release_name': 'Movie.2024.1080p.WEB-DL-GROUP', 'season': None, 'episode': None}
		candidates = [
			self.providers._candidate('subsource', 'vi', 'vie', ('Movie.2024.1080p.WEB-DL-GROUP',), trusted=True),
			self.providers._candidate('opensubtitles', 'en', 'eng', ('Different.Release',), machine_translated=True)
		]

		ranked = self.providers.rank_candidates(candidates, media)

		self.assertEqual([item['id'] for item in ranked], ['en', 'vi'])

	def test_exact_release_match_wins_within_language(self):
		media = {'release_name': 'Movie.2024.1080p.WEB-DL-GROUP', 'season': None, 'episode': None}
		candidates = [
			self.providers._candidate('opensubtitles', 'partial', 'eng', ('Movie.2024.BluRay-OTHER',), trusted=True),
			self.providers._candidate('subdl', 'exact', 'eng', ('Movie.2024.1080p.WEB-DL-GROUP',))
		]

		ranked = self.providers.rank_candidates(candidates, media)

		self.assertEqual(ranked[0]['id'], 'exact')

	def test_wrong_episode_is_rejected_before_scoring(self):
		media = {'release_name': 'Show.S01E02.WEB-DL', 'season': 1, 'episode': 2}
		candidates = [
			self.providers._candidate('opensubtitles', 'wrong', 'eng', ('Show.S01E03.WEB-DL',), season=1, episode=3, hash_match=True),
			self.providers._candidate('subdl', 'right', 'eng', ('Show.S01E02.WEB-DL',), season=1, episode=2)
		]

		ranked = self.providers.rank_candidates(candidates, media)

		self.assertEqual([item['id'] for item in ranked], ['right'])

	def test_public_candidate_contains_no_locator_or_url(self):
		candidate = self.providers._candidate('subdl', 'parent:file', 'eng', ('Release',), rating=8.5, locator='/private/path')
		candidate['sync'] = True

		public = self.providers.public_candidate(candidate)

		self.assertEqual(set(public), {'provider', 'id', 'lang', 'score', 'release', 'rating', 'sync'})
		self.assertEqual((public['rating'], public['sync']), (8.5, True))
		self.assertNotIn('://', json.dumps(public))

	def test_public_candidate_projection_preserves_an_existing_safe_release_label(self):
		public = {'provider': 'subdl', 'id': 'file:1:2', 'lang': 'eng', 'score': 10, 'release': 'Movie.WEB-DL-GROUP'}

		self.assertEqual(self.providers.public_candidate(public), public)

	def test_all_three_provider_searches_overlap(self):
		barrier = threading.Barrier(3)

		class FakeProvider:
			def __init__(self, config, media, cancelled): self.name = config['name']
			def search(self):
				barrier.wait(timeout=2)
				return [self.providers._candidate(self.name, self.name, 'eng', (self.name,))]

		classes = {}
		config = {}
		for name in ('opensubtitles', 'subdl', 'subsource'):
			classes[name] = type('%sProvider' % name, (FakeProvider,), {'providers': self.providers})
			config[name] = {'name': name}
		manager = self.providers.ProviderManager({'release_name': '', 'season': None, 'episode': None}, config=config, provider_classes=classes)

		results = manager.search()

		self.assertEqual({item['provider'] for item in results}, set(config))

	def test_sync_requires_hash_or_confident_release_match(self):
		media = {'release_name': 'Show.S01E02.1080p.WEB-DL-GROUP', 'season': 1, 'episode': 2}
		candidates = [
			self.providers._candidate('opensubtitles', 'hash', 'eng', ('Different.Release',), season=1, episode=2, hash_match=True),
			self.providers._candidate('subdl', 'strong', 'eng', ('Another.Release',), season=1, episode=2, match_score=0.8),
			self.providers._candidate('subsource', 'release', 'eng', ('Show.S01E02.1080p.WEB-DL-GROUP',), season=1, episode=2),
			self.providers._candidate('subsource', 'weak', 'eng', ('Show.S01E02.HDTV-OTHER',), season=1, episode=2)
		]

		ranked = self.providers.rank_candidates(candidates, media)

		self.assertEqual({item['id']: item['sync'] for item in ranked}, {'hash': True, 'strong': True, 'release': True, 'weak': False})

	def test_one_provider_failure_preserves_other_results(self):
		class Good:
			def __init__(self, config, media, cancelled): pass
			def search(inner): return [self.providers._candidate('subdl', 'good', 'eng', ('Release',))]

		class Bad:
			def __init__(self, config, media, cancelled): pass
			def search(self): raise RuntimeError('secret request details')

		manager = self.providers.ProviderManager({'release_name': '', 'season': None, 'episode': None}, config={'subdl': {}, 'subsource': {}}, provider_classes={'subdl': Good, 'subsource': Bad})

		results = manager.search()

		self.assertEqual([item['id'] for item in results], ['good'])
		self.assertNotIn('secret request details', repr(self.providers.kodi_utils.logger.call_args_list))

	def test_opensubtitles_normalizes_search_results_and_uses_episode_scope(self):
		media = {'imdb_id': 'tt123', 'season': 1, 'episode': 2, 'release_name': 'Show.S01E02.WEB-DL'}
		provider = self.providers.OpenSubtitlesProvider({'api_key': 'key', 'user_agent': 'Test'}, media)
		provider.login = Mock()
		provider.authenticated_json = Mock(return_value={'data': [{
			'id': 'subtitle-record',
			'attributes': {
				'language': 'en', 'release': 'Show.S01E02.WEB-DL', 'fps': 23.976, 'hearing_impaired': False,
				'foreign_parts_only': False, 'machine_translated': False, 'from_trusted': True, 'ratings': 8.5,
				'download_count': 321, 'moviehash_match': False, 'feature_details': {'season_number': 1, 'episode_number': 2},
				'files': [{'file_id': 456, 'file_name': 'Show.S01E02.WEB-DL.srt'}]
			}
		}]})

		results = provider.search()

		provider.authenticated_json.assert_called_once_with('GET', 'subtitles', 'search', params={'languages': 'en,vi', 'parent_imdb_id': '123', 'season_number': 1, 'episode_number': 2})
		self.assertEqual(len(results), 1)
		self.assertEqual((results[0]['provider'], results[0]['id'], results[0]['lang']), ('opensubtitles', '456', 'eng'))
		self.assertEqual((results[0]['season'], results[0]['episode'], results[0]['extension']), (1, 2, 'srt'))
		self.assertTrue(results[0]['trusted'])

	def test_opensubtitles_login_normalizes_full_host_base_url(self):
		provider = self.providers.OpenSubtitlesProvider({'api_key': 'key-one', 'user_agent': 'Test', 'username': 'user-one', 'password': 'secret'}, {})
		provider.request_json = Mock(return_value={'token': 'private-token', 'base_url': 'https://vip-api.opensubtitles.com'})

		provider.login(force=True)

		self.assertEqual(provider.base_url, 'https://vip-api.opensubtitles.com/api/v1')
		self.assertEqual(provider.token, 'private-token')

	def test_opensubtitles_download_requests_native_file_without_format_conversion(self):
		provider = self.providers.OpenSubtitlesProvider({'api_key': 'key', 'user_agent': 'Test'}, {})
		provider.authenticated_json = Mock(return_value={'link': 'https://download.invalid/subtitle'})
		response = Mock(ok=True, content=b'subtitle text')
		response.status_code = 200

		with patch.object(self.providers, '_request', return_value=response): payload = provider.download({'id': '456', 'extension': 'srt'})

		provider.authenticated_json.assert_called_once_with('POST', 'download', 'download', json={'file_id': 456})
		self.assertEqual(payload, {'content': b'subtitle text', 'extension': 'srt'})

	def test_opensubtitles_quota_rejection_disables_repeated_download_attempts(self):
		provider = self.providers.OpenSubtitlesProvider({'api_key': 'key', 'user_agent': 'Test'}, {})
		provider.authenticated_json = Mock(side_effect=self.providers.ProviderError('http_406'))

		self.assertIsNone(provider.download({'id': '456'}))
		self.assertIsNone(provider.download({'id': '789'}))

		provider.authenticated_json.assert_called_once_with('POST', 'download', 'download', json={'file_id': 456})
		self.assertTrue(provider.download_disabled)

	def test_subdl_normalizes_filename_and_exact_episode_file_results(self):
		media = {'imdb_id': 'tt123', 'season': 1, 'episode': 2, 'release_name': 'Show.S01E02.WEB-DL'}
		provider = self.providers.SubDLProvider({'api_key': 'key'}, media)
		provider.request_json = Mock(side_effect=[
			{'match': {'imdb_id': 'tt123', 'degraded': False}, 'subtitles': [
				{'url': '/subtitle/archive.zip', 'lang': 'en', 'release_name': 'Show.S01E02.WEB-DL', 'match_score': 0.95, 'season': 1, 'episode': 2}
			]},
			{'subtitles': [{'lang': 'vi', 'release_name': 'Show.S01E02.WEB-DL', 'unpack_files': [
				{'url': '/subtitle/88/99', 'file_n_id': 99, 'language': 'vi', 'name': 'Show.S01E02.srt', 'format': 'srt', 'season': 1, 'episode': 2},
				{'url': '/subtitle/88/100', 'file_n_id': 100, 'language': 'vi', 'name': 'Show.S01E03.srt', 'format': 'srt', 'season': 1, 'episode': 3}
			]}]}
		])

		results = provider.search()

		self.assertEqual({item['id'] for item in results}, {'archive:archive.zip', 'file:88:99'})
		self.assertEqual({item['lang'] for item in results}, {'eng', 'vie'})
		self.assertEqual(provider.request_json.call_count, 2)

	def test_subsource_searches_both_languages_and_rejects_wrong_episode(self):
		media = {'imdb_id': 'tt123', 'season': 1, 'episode': 2, 'release_name': 'Show.S01E02.WEB-DL'}
		provider = self.providers.SubSourceProvider({'api_key': 'key'}, media)
		provider.request_json = Mock(side_effect=[
			{'data': [{'movieId': 77}]},
			{'data': [
				{'subtitleId': 1, 'language': 'english', 'releaseInfo': ['Show.S01E02.WEB-DL'], 'downloads': 100},
				{'subtitleId': 2, 'language': 'english', 'releaseInfo': ['Show.S01E03.WEB-DL'], 'downloads': 200}
			]},
			{'data': [{'subtitleId': 3, 'language': 'vietnamese', 'releaseInfo': ['Show.S01E02.WEB-DL'], 'rating': {'good': 9, 'bad': 1}}]}
		])

		results = provider.search()

		self.assertEqual({item['id'] for item in results}, {'1', '3'})
		self.assertEqual({item['lang'] for item in results}, {'eng', 'vie'})
		languages = [call.kwargs['params']['language'] for call in provider.request_json.call_args_list[1:]]
		self.assertEqual(languages, ['english', 'vietnamese'])
		self.assertEqual(next(item for item in results if item['id'] == '3')['rating'], 9.0)

	def test_valid_episode_archive_selects_exact_member(self):
		content = archive_bytes([('Show.S01E01.srt', b'wrong'), ('Show.S01E02.ass', b'correct'), ('readme.nfo', b'ignore')])

		payload = self.providers.extract_subtitle_archive(content, 1, 2)

		self.assertEqual(payload, {'content': b'correct', 'extension': 'ass'})

	def test_episode_archive_accepts_member_covering_target_range(self):
		content = archive_bytes([('Show.S01E01-E03.srt', b'episode pack'), ('Show.S01E04.srt', b'wrong')])

		payload = self.providers.extract_subtitle_archive(content, 1, 2)

		self.assertEqual(payload, {'content': b'episode pack', 'extension': 'srt'})

	def test_episode_match_does_not_treat_resolution_as_range_end(self):
		self.assertFalse(self.providers._episode_matches('Show.S01E01.1080p.WEB-DL', 1, 2))

	def test_movie_archive_prefers_member_matching_selected_release(self):
		content = archive_bytes([
			('Movie.2024.BluRay-OTHER.srt', b'larger but wrong release'),
			('Movie.2024.1080p.WEB-DL-GROUP.ass', b'matching release')
		])

		payload = self.providers.extract_subtitle_archive(content, release_name='Movie.2024.1080p.WEB-DL-GROUP')

		self.assertEqual(payload, {'content': b'matching release', 'extension': 'ass'})

	def test_extracted_subtitle_size_limit_is_enforced(self):
		content = archive_bytes([('oversized.srt', b'12345')])

		with patch.object(self.providers, 'MAX_SUBTITLE_BYTES', 4): self.assertIsNone(self.providers.extract_subtitle_archive(content))

	def test_archive_path_traversal_rejects_candidate(self):
		content = archive_bytes([('../escape.srt', b'bad'), ('safe.srt', b'good')])

		self.assertIsNone(self.providers.extract_subtitle_archive(content))

	def test_ambiguous_episode_archive_is_rejected(self):
		content = archive_bytes([('first.srt', b'first'), ('second.srt', b'second')])

		self.assertIsNone(self.providers.extract_subtitle_archive(content, 1, 2))


if __name__ == '__main__':
	unittest.main()
