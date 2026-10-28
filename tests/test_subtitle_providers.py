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


	def test_valid_episode_archive_selects_exact_member(self):
		content = archive_bytes([('Show.S01E01.srt', b'wrong'), ('Show.S01E02.ass', b'correct'), ('readme.nfo', b'ignore')])

		payload = self.providers.extract_subtitle_archive(content, 1, 2)

		self.assertEqual(payload, {'content': b'correct', 'extension': 'ass'})


	def test_partial_only_archive_is_rejected_automatically_but_remains_manually_selectable(self):
		content = archive_bytes([('Movie.eng.forced.srt', b'partial')])
		self.assertIsNone(self.providers.extract_subtitle_archive(content, full_dialogue_only=True))
		self.assertEqual(self.providers.extract_subtitle_archive(content), {'content': b'partial', 'extension': 'srt'})

	def test_removing_forced_members_never_attaches_full_subtitles_for_the_wrong_episode(self):
		content = archive_bytes([('Show.S01E02.forced.srt', b'partial for target'), ('Show.S01E03.full.srt', b'wrong episode')])
		self.assertIsNone(self.providers.extract_subtitle_archive(content, 1, 2, full_dialogue_only=True))


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
