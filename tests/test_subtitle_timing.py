import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]


def load_subtitles():
	class RequestException(Exception):
		pass

	class Timeout(RequestException):
		pass

	class ConnectionError(RequestException):
		pass

	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.xbmc_player = object
	kodi_utils.logger = Mock()
	kodi_utils.delete_file = Mock()
	kodi_utils.monitor = Mock()
	kodi_utils.list_dirs = Mock()
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	requests = types.ModuleType('requests')
	requests.RequestException = RequestException
	requests.Timeout = Timeout
	requests.ConnectionError = ConnectionError
	requests.get = Mock()
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils, 'requests': requests}
	previous = {name: sys.modules.get(name) for name in stubs}
	sys.modules.update(stubs)
	try:
		path = ROOT / 'resources' / 'lib' / 'indexers' / 'subtitles.py'
		spec = importlib.util.spec_from_file_location('test_subtitle_timing_indexer', path)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
	finally:
		for name, old_module in previous.items():
			if old_module is None: sys.modules.pop(name, None)
			else: sys.modules[name] = old_module
	return module


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
		self.client.manifest = 'https://example.test/manifest.json'
		self.client.imdb_id = 'tt123'
		self.client.season = None
		self.client.episode = None
		self.client._set_context = Mock()
		self.client.subtitles_search = Mock(return_value=[{'lang': 'vie', 'url': 'memory://vie'}, {'lang': 'eng', 'url': 'memory://eng'}])
		self.client.subtitles_download = Mock(return_value=SimpleNamespace(text='subtitle text'))
		self.subtitles.kodi_utils.sleep = Mock()
		self.subtitles.kodi_utils.notification = Mock()
		self.subtitles.kodi_utils.logger.reset_mock()
		self.subtitles.kodi_utils.delete_file.reset_mock()
		self.subtitles.kodi_utils.list_dirs.reset_mock()
		self.subtitles.kodi_utils.monitor.reset_mock()
		self.subtitles.kodi_utils.monitor.abortRequested.return_value = False
		self.subtitles.kodi_utils.monitor.waitForAbort.return_value = False
		self.subtitles.requests.get.reset_mock(return_value=True, side_effect=True)

	def test_remote_subtitle_closes_file_before_immediate_attach(self):
		events = []
		final_path = 'special://temp/fixture_eng.srt'
		self.subtitles.kodi_utils.open_file = Mock(return_value=RecordingFile(events))
		self.client.setSubtitles = Mock(side_effect=lambda path: events.append(('attach', path)))

		result = self.client._searched_subs()

		self.assertTrue(result)
		self.client.subtitles_download.assert_called_once_with('memory://eng')
		self.subtitles.kodi_utils.open_file.assert_called_once_with(final_path, 'w')
		self.assertEqual(events, ['open', ('write', 'subtitle text'), 'close', ('attach', final_path)])
		self.subtitles.kodi_utils.sleep.assert_not_called()

	def test_binary_response_payload_is_written_before_attach(self):
		class BinaryResponse:
			content = b'subtitle bytes'

			@property
			def text(self):
				raise AttributeError

		events = []
		self.client.subtitles_download.return_value = BinaryResponse()
		self.subtitles.kodi_utils.open_file = Mock(return_value=RecordingFile(events))
		self.client.setSubtitles = Mock(side_effect=lambda path: events.append(('attach', path)))

		self.client._searched_subs()

		self.assertEqual(events, ['open', ('write', b'subtitle bytes'), 'close', ('attach', 'special://temp/fixture_eng.srt')])
		self.subtitles.kodi_utils.sleep.assert_not_called()

	def test_write_failure_closes_file_without_attach(self):
		events = []
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
		self.subtitles.kodi_utils.open_file = Mock(return_value=RecordingFile(events, write_result=False))
		self.client.setSubtitles = Mock()

		result = self.client._searched_subs()

		self.assertFalse(result)
		self.assertEqual(events, ['open', ('write', 'subtitle text'), 'close'])
		self.subtitles.kodi_utils.delete_file.assert_called_once_with('special://temp/fixture_eng.srt')
		self.client.setSubtitles.assert_not_called()

	def test_empty_download_is_not_written_or_attached(self):
		self.client.subtitles_download.return_value = SimpleNamespace(text='')
		self.subtitles.kodi_utils.open_file = Mock()
		self.client.setSubtitles = Mock()

		result = self.client._searched_subs()

		self.assertFalse(result)
		self.subtitles.kodi_utils.open_file.assert_not_called()
		self.subtitles.kodi_utils.delete_file.assert_not_called()
		self.client.setSubtitles.assert_not_called()

	def test_download_timeout_retries_once_then_returns_logged_failure(self):
		timeout = self.subtitles.requests.Timeout('timed out')
		self.subtitles.requests.get.side_effect = (timeout, timeout)

		result = self.subtitles.Subtitles().subtitles_download('https://example.test/subtitle')

		self.assertIsInstance(result, str)
		self.assertEqual(self.subtitles.requests.get.call_count, 2)
		self.subtitles.kodi_utils.monitor.waitForAbort.assert_called_once_with(1)
		self.assertEqual(self.subtitles.kodi_utils.logger.call_count, 2)

	def test_download_nonretryable_http_failure_is_logged_without_retry(self):
		response = Mock(ok=False, status_code=404, reason='Not Found', headers={})
		self.subtitles.requests.get.return_value = response

		result = self.subtitles.Subtitles().subtitles_download('https://example.test/subtitle')

		self.assertIsInstance(result, str)
		self.subtitles.requests.get.assert_called_once()
		self.subtitles.kodi_utils.sleep.assert_not_called()
		self.subtitles.kodi_utils.logger.assert_called_once()
		response.close.assert_called_once_with()

	def test_search_invalid_json_returns_logged_failure(self):
		response = Mock(ok=True, status_code=200, headers={})
		response.json.side_effect = ValueError('invalid json')
		self.subtitles.requests.get.return_value = response
		client = self.subtitles.Subtitles()
		client.manifest = 'https://example.test/manifest.json'
		client.imdb_id = 'tt123'
		client.season = None
		client.episode = None

		result = client.subtitles_search()

		self.assertIsInstance(result, str)
		self.subtitles.kodi_utils.logger.assert_called_once()
		response.close.assert_called_once_with()

	def test_retryable_http_failure_retries_once(self):
		first = Mock(ok=False, status_code=503, reason='Unavailable', headers={})
		second = SimpleNamespace(ok=True, status_code=200, reason='OK', headers={})
		self.subtitles.requests.get.side_effect = (first, second)

		result = self.subtitles.Subtitles().subtitles_download('https://example.test/subtitle')

		self.assertIs(result, second)
		self.assertEqual(self.subtitles.requests.get.call_count, 2)
		first.close.assert_called_once_with()
		self.subtitles.kodi_utils.monitor.waitForAbort.assert_called_once_with(1)

	def test_open_failure_does_not_delete_an_existing_destination(self):
		self.subtitles.kodi_utils.open_file = Mock(side_effect=OSError('open failed'))

		result = self.client.save_subtitle(SimpleNamespace(text='subtitle text'), 'special://temp/fixture_eng.srt')

		self.assertFalse(result)
		self.subtitles.kodi_utils.delete_file.assert_not_called()

	def test_playback_change_after_download_does_not_write_attach_or_notify(self):
		self.client.expected_playing_file = 'old-video'
		self.client.isPlayingVideo = Mock(return_value=True)
		self.client.getPlayingFile = Mock(side_effect=('old-video', 'new-video'))
		self.client.subtitles_download.return_value = SimpleNamespace(text='subtitle text')
		self.subtitles.kodi_utils.open_file = Mock()
		self.client.setSubtitles = Mock()

		result = self.client._searched_subs()

		self.assertFalse(result)
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
		self.client.subtitles_download.assert_not_called()
		self.subtitles.kodi_utils.notification.assert_not_called()


if __name__ == '__main__':
	unittest.main()
