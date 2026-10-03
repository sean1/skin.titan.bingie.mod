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


	def test_exact_release_match_wins_within_language(self):
		media = {'release_name': 'Movie.2024.1080p.WEB-DL-GROUP', 'season': None, 'episode': None}
		candidates = [
			self.providers._candidate('opensubtitles', 'partial', 'eng', ('Movie.2024.BluRay-OTHER',), trusted=True),
			self.providers._candidate('subdl', 'exact', 'eng', ('Movie.2024.1080p.WEB-DL-GROUP',))
		]

		ranked = self.providers.rank_candidates(candidates, media)

		self.assertEqual(ranked[0]['id'], 'exact')


	def test_subtitle_coverage_uses_bounded_names_when_flags_are_missing(self):
		cases = (
			('English (Forced)', 'partial'), ('Foreign parts only', 'partial'), ('English signs only', 'partial'),
			('Movie.2025.eng.forced.srt', 'partial'), ('English (Full)', 'full'), ('English', 'unknown'),
			('English Unforced', 'unknown'), ('UnforcedPerspective.2025.srt', 'unknown')
		)
		for value, expected in cases:
			with self.subTest(value=value): self.assertEqual(self.providers.subtitle_coverage(value), expected)
		for key in ('name', 'filename', 'caption'):
			with self.subTest(key=key): self.assertEqual(self.providers.subtitle_coverage({key: 'English (Forced)'}), 'partial')
		self.assertEqual(self.providers.subtitle_coverage({'release_names': ['Movie.2025.eng.forced.srt']}), 'partial')
		self.assertEqual(self.providers.subtitle_coverage({'forced': False, 'name': 'English (Forced)'}), 'partial')
		self.assertNotEqual(self.providers.subtitle_coverage('English (SDH)'), 'partial')
		self.assertEqual(self.providers.subtitle_coverage({}), 'unknown')

	def test_full_candidates_precede_unknown_within_the_existing_language_order(self):
		media = {'release_name': 'Movie.2025.WEB-DL', 'season': None, 'episode': None}
		candidates = [
			self.providers._candidate('opensubtitles', 'unknown', 'eng', ('Movie.2025.WEB-DL',), hash_match=True),
			self.providers._candidate('subdl', 'full', 'eng', ('Other.Release',), foreign_parts_only=False),
			self.providers._candidate('subsource', 'partial', 'eng', ('Movie.2025.WEB-DL',), forced=True, hash_match=True),
			self.providers._candidate('subdl', 'vietnamese', 'vie', ('Movie.2025.WEB-DL',), foreign_parts_only=False)
		]
		ranked = self.providers.rank_candidates(candidates, media)
		positions = {item['id']: index for index, item in enumerate(ranked)}
		self.assertLess(positions['full'], positions['unknown'])
		self.assertLess(positions['unknown'], positions['vietnamese'])
		self.assertIn('partial', positions)

	def test_opensubtitles_preserves_partial_flags_and_filename_hints(self):
		provider = self.providers.OpenSubtitlesProvider({'api_key': 'key', 'user_agent': 'Test'}, {'imdb_id': 'tt123'})
		provider.authenticated_json = Mock(return_value={'data': [
			{'attributes': {'language': 'en', 'foreign_parts_only': 'true', 'files': [{'file_id': 1, 'file_name': 'Movie.srt'}]}},
			{'attributes': {'language': 'en', 'files': [{'file_id': 2, 'file_name': 'Movie.eng.forced.srt'}]}}
		]})
		candidates = provider.search()
		self.assertEqual([self.providers.subtitle_coverage(item) for item in candidates], ['partial', 'partial'])

	def test_subdl_preserves_partial_flags_on_the_exact_episode_file(self):
		media = {'imdb_id': 'tt123', 'season': 1, 'episode': 2, 'release_name': ''}
		provider = self.providers.SubDLProvider({'api_key': 'key'}, media)
		provider.request_json = Mock(return_value={'subtitles': [{'lang': 'en', 'unpack_files': [
			{'url': '/subtitle/88/99', 'file_n_id': 99, 'language': 'en', 'name': 'Show.S01E02.srt', 'season': 1, 'episode': 2, 'forced': 'true'}
		]}]})
		candidates = provider.search()
		self.assertEqual(len(candidates), 1)
		self.assertEqual(self.providers.subtitle_coverage(candidates[0]), 'partial')

	def test_subsource_preserves_partial_production_types_and_missing_flag_names(self):
		provider = self.providers.SubSourceProvider({'api_key': 'key'}, {'imdb_id': 'tt123', 'season': None, 'episode': None})
		provider.request_json = Mock(side_effect=[
			{'data': [{'movieId': 77}]},
			{'data': [{'subtitleId': 1, 'language': 'english', 'releaseInfo': ['Movie'], 'productionType': 'Forced'}, {'subtitleId': 2, 'language': 'english', 'releaseInfo': ['Movie.eng.forced.srt']}]},
			{'data': []}
		])
		candidates = provider.search()
		self.assertEqual([self.providers.subtitle_coverage(item) for item in candidates], ['partial', 'partial'])

	def test_wrong_episode_is_rejected_before_scoring(self):
		media = {'release_name': 'Show.S01E02.WEB-DL', 'season': 1, 'episode': 2}
		candidates = [
			self.providers._candidate('opensubtitles', 'wrong', 'eng', ('Show.S01E03.WEB-DL',), season=1, episode=3, hash_match=True),
			self.providers._candidate('subdl', 'right', 'eng', ('Show.S01E02.WEB-DL',), season=1, episode=2)
		]

		ranked = self.providers.rank_candidates(candidates, media)

		self.assertEqual([item['id'] for item in ranked], ['right'])

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
		self.assertEqual(manager.diagnostics(), {'config': '', 'providers': ('subsource',)})


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

	def test_automatic_archive_selection_prefers_full_dialogue_over_partial_members(self):
		content = archive_bytes([('Movie.2025.eng.forced.srt', b'partial'), ('Movie.2025.eng.full.srt', b'full dialogue')])
		payload = self.providers.extract_subtitle_archive(content, release_name='Movie.2025.eng.forced', full_dialogue_only=True)
		self.assertEqual(payload, {'content': b'full dialogue', 'extension': 'srt'})

	def test_partial_only_archive_is_rejected_automatically_but_remains_manually_selectable(self):
		content = archive_bytes([('Movie.eng.forced.srt', b'partial')])
		self.assertIsNone(self.providers.extract_subtitle_archive(content, full_dialogue_only=True))
		self.assertEqual(self.providers.extract_subtitle_archive(content), {'content': b'partial', 'extension': 'srt'})

	def test_removing_forced_members_never_attaches_full_subtitles_for_the_wrong_episode(self):
		content = archive_bytes([('Show.S01E02.forced.srt', b'partial for target'), ('Show.S01E03.full.srt', b'wrong episode')])
		self.assertIsNone(self.providers.extract_subtitle_archive(content, 1, 2, full_dialogue_only=True))


	def test_episode_match_does_not_treat_resolution_as_range_end(self):
		self.assertFalse(self.providers._episode_matches('Show.S01E01.1080p.WEB-DL', 1, 2))


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
