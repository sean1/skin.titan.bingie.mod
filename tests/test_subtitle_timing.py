import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]


def load_subtitles():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.xbmc_player = object
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	requests = types.ModuleType('requests')
	requests.RequestException = Exception
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
	def __init__(self, events, write_error=None):
		self.events = events
		self.write_error = write_error

	def __enter__(self):
		self.events.append('open')
		return self

	def write(self, payload):
		self.events.append(('write', payload))
		if self.write_error: raise self.write_error

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
		self.client._set_context = Mock()
		self.client.subtitles_search = Mock(return_value=[{'lang': 'vie', 'url': 'memory://vie'}, {'lang': 'eng', 'url': 'memory://eng'}])
		self.client.subtitles_download = Mock(return_value=SimpleNamespace(text='subtitle text'))
		self.subtitles.kodi_utils.sleep = Mock()
		self.subtitles.kodi_utils.notification = Mock()

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

		with self.assertRaises(OSError): self.client._searched_subs()

		self.assertEqual(events, ['open', ('write', 'subtitle text'), 'close'])
		self.client.setSubtitles.assert_not_called()
		self.subtitles.kodi_utils.sleep.assert_not_called()


if __name__ == '__main__':
	unittest.main()
