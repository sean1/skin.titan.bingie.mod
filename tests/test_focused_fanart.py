import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]


def load_entry():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.logger = lambda *args: None
	kodi_utils.path_exists = lambda *args: False
	kodi_utils.translate_path = lambda path: path
	kodi_utils.monitor = object()
	kodi_utils.get_property = lambda *args: ''
	kodi_utils.set_property = lambda *args: None
	kodi_utils.clear_property = lambda *args: None
	kodi_utils.get_setting = lambda *args: ''
	kodi_utils.set_setting = lambda *args: None
	kodi_utils.make_settings_dict = lambda: None
	kodi_utils.xbmc_monitor = object
	kodi_utils.xbmc_player = object
	settings = types.ModuleType('modules.settings')
	prefetch = types.ModuleType('modules.prefetch')
	prefetch.NextPagePrefetch = object
	stubs = {'modules.kodi_utils': kodi_utils, 'modules.settings': settings, 'modules.prefetch': prefetch}
	previous = {name: sys.modules.get(name) for name in stubs}
	sys.path.insert(0, str(ROOT / 'resources' / 'lib'))
	sys.modules.update(stubs)
	try:
		path = ROOT / 'resources' / 'lib' / 'entry.py'
		spec = importlib.util.spec_from_file_location('test_focused_fanart_entry', path)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
	finally:
		sys.path.pop(0)
		for name, old_module in previous.items():
			if old_module is None: sys.modules.pop(name, None)
			else: sys.modules[name] = old_module
	return module


class FocusedFanartTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.entry = load_entry()

	def setUp(self):
		self.properties = {}
		self.entry.set_property = self.properties.__setitem__
		self.entry.clear_property = lambda key: self.properties.pop(key, None)
		self.entry.kodi_utils.get_visibility = lambda condition: condition != 'Window.IsActive(Videos)'
		self.fanart = self.entry.FocusedFanart()

	def test_pending_focus_becomes_stable_after_publication(self):
		self.fanart._focused_identity = Mock(return_value='listing|movie|1')
		self.fanart._focused_art = Mock(return_value='fanart.jpg')
		self.entry.monotonic = Mock(side_effect=(10.0, 10.1, 10.25))

		self.assertTrue(self.fanart.tick())
		self.assertFalse(self.fanart.stable)
		self.assertTrue(self.fanart.tick())
		self.assertFalse(self.fanart.stable)
		self.assertFalse(self.fanart.tick())
		self.assertTrue(self.fanart.stable)
		self.assertEqual(self.properties[self.entry.FOCUSED_FANART_PROPERTY], 'fanart.jpg')
		self.assertEqual(self.properties[self.entry.FOCUSED_FANART_IDENTITY_PROPERTY], 'listing|movie|1')

		self.entry.monotonic = Mock(return_value=11.0)
		self.assertFalse(self.fanart.tick())
		self.assertTrue(self.fanart.stable)
		self.assertEqual(self.fanart._focused_art.call_count, 1)

	def test_empty_identity_does_not_keep_fast_polling_active(self):
		self.fanart._focused_identity = Mock(return_value='')
		self.assertFalse(self.fanart.tick())
		self.assertFalse(self.fanart.stable)

	def test_losing_content_focus_is_idle(self):
		self.entry.kodi_utils.get_visibility = lambda condition: condition == self.entry.FOCUSED_FANART_WINDOW_VISIBILITY
		self.assertFalse(self.fanart.tick())
		self.assertFalse(self.fanart.stable)

	def test_poll_interval_prioritizes_pending_and_preview_work(self):
		poll = self.entry._service_poll_interval
		self.assertEqual(poll(True, False, False), 0.25)
		self.assertEqual(poll(False, True, True), 0.25)
		self.assertEqual(poll(False, True, False), 0.5)
		self.assertEqual(poll(False, False, False), 1.0)


if __name__ == '__main__':
	unittest.main()
