import sys
import types
import unittest
from unittest.mock import Mock

from tests.test_focused_fanart import load_entry


class FakePreviewPlayer:
	def __init__(self, owner, generation):
		self.owner = owner
		self.generation = generation
		self.play_calls = []

	def play(self, *args, **kwargs):
		self.play_calls.append((args, kwargs))

	def onAVStarted(self):
		self.owner._on_av_started(self.generation)


class TrailerPreviewTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.entry = load_entry()
		cls.entry.TrailerPreviewPlayer = FakePreviewPlayer

	def setUp(self):
		self.properties = {}
		self.commands = []
		self.visibility = {'Player.HasMedia': False, 'Player.HasVideo': False}
		self.entry.set_property = self.properties.__setitem__
		self.entry.get_property = lambda key: self.properties.get(key, '')
		self.entry.clear_property = lambda key: self.properties.pop(key, None)
		self.entry.kodi_utils.get_visibility = lambda condition: self.visibility.get(condition, False)
		self.entry.kodi_utils.get_infolabel = lambda label: ''
		self.entry.kodi_utils.execute_builtin = self.commands.append
		self.entry.kodi_utils.xbmc = types.SimpleNamespace(getSkinDir=lambda: 'skin.titan.bingie.lite')
		self.preview = self._new_preview()

	def _new_preview(self):
		trailers = types.ModuleType('modules.trailers')
		trailers.start_manifest_server = lambda: None
		old_trailers = sys.modules.get('modules.trailers')
		sys.modules['modules.trailers'] = trailers
		try: return self.entry.TrailerPreview()
		finally:
			if old_trailers is None: sys.modules.pop('modules.trailers', None)
			else: sys.modules['modules.trailers'] = old_trailers

	def _candidate(self, identity='movie|1'):
		return identity, 'trailer-url', 'movie', '1', False, False

	def _activate(self):
		self.entry.monotonic = Mock(return_value=20.0)
		self.preview.identity = 'movie|1'
		self.preview._launch_preview('preview-url', None)
		self.preview.preview_player.onAVStarted()
		self.preview._publish_preview_ready()
		self.visibility.update({'Player.HasMedia': True, 'Player.HasVideo': True})
		self.preview._preview_window_active = Mock(return_value=True)
		self.preview._owns_preview = Mock(return_value=True)

	def test_stable_focus_waits_exactly_three_seconds(self):
		candidate = self._candidate()
		self.preview._candidate = Mock(return_value=candidate)
		self.preview._start_preview_preparation = Mock()
		self.entry.monotonic = Mock(side_effect=(10.0, 12.99, 13.0))

		self.assertTrue(self.preview.tick())
		self.assertTrue(self.preview.tick())
		self.preview._start_preview_preparation.assert_not_called()
		self.assertTrue(self.preview.tick())
		self.preview._start_preview_preparation.assert_called_once_with('movie|1', 'trailer-url')

	def test_first_frame_callback_publishes_ready_only_after_ownership_check(self):
		self.entry.monotonic = Mock(return_value=20.0)
		self.preview.identity = 'movie|1'
		self.preview._candidate = Mock(return_value=self._candidate())
		self.preview._preview_window_active = Mock(return_value=True)
		self.preview._preview_navigation_away = Mock(return_value=False)
		self.preview._owns_preview = Mock(return_value=True)
		self.preview._launch_preview('preview-url', None)
		self.visibility.update({'Player.HasMedia': True, 'Player.HasVideo': True})

		self.assertEqual(self.properties[self.entry.TRAILER_PREVIEW_PROPERTY], 'true')
		self.assertNotIn(self.entry.TRAILER_PREVIEW_READY_PROPERTY, self.properties)
		self.preview.preview_player.onAVStarted()
		self.assertNotIn(self.entry.TRAILER_PREVIEW_READY_PROPERTY, self.properties)
		self.assertTrue(self.preview.tick())
		self.assertEqual(self.properties[self.entry.TRAILER_PREVIEW_READY_PROPERTY], 'true')

	def test_stale_first_frame_callback_cannot_ready_a_new_preview(self):
		self.entry.monotonic = Mock(return_value=20.0)
		self.preview._launch_preview('first-preview', None)
		stale_player = self.preview.preview_player
		self.preview._finish_preview()
		self.preview._launch_preview('second-preview', None)

		stale_player.onAVStarted()
		self.assertEqual(self.preview.av_started_generation, -1)
		self.assertNotIn(self.entry.TRAILER_PREVIEW_READY_PROPERTY, self.properties)
		self.preview.preview_player.onAVStarted()
		self.assertEqual(self.preview.av_started_generation, self.preview.preview_generation)

	def test_item_menu_and_empty_row_navigation_request_stop_in_same_tick(self):
		for label, candidate, navigating in (
			('item', self._candidate('movie|2'), False),
			('menu', self._candidate(), True),
			('row', None, False),
		):
			with self.subTest(label=label):
				self.properties.clear()
				self.commands.clear()
				self.preview = self._new_preview()
				self._activate()
				self.preview._candidate = Mock(return_value=candidate)
				self.preview._preview_navigation_away = Mock(return_value=navigating)

				self.assertTrue(self.preview.tick())
				self.assertEqual(self.commands, ['PlayerControl(Stop)'])
				self.assertFalse(self.preview.active)
				self.assertTrue(self.preview.pending_stop_requested)
				self.assertEqual(self.properties[self.entry.TRAILER_PREVIEW_PROPERTY], 'true')
				self.assertNotIn(self.entry.TRAILER_PREVIEW_READY_PROPERTY, self.properties)

	def test_row_cancel_request_stops_immediately_and_invalidates_callback(self):
		self._activate()
		stale_player = self.preview.preview_player
		self.properties[self.entry.TRAILER_PREVIEW_CANCEL_PROPERTY] = 'true'

		self.assertTrue(self.preview.tick())
		self.assertEqual(self.commands, ['PlayerControl(Stop)'])
		self.assertFalse(self.preview.active)
		self.assertNotIn(self.entry.TRAILER_PREVIEW_READY_PROPERTY, self.properties)
		stale_player.onAVStarted()
		self.assertEqual(self.preview.av_started_generation, -1)

	def test_unrelated_playback_is_never_stopped(self):
		self._activate()
		self.preview._candidate = Mock(return_value=self._candidate('movie|2'))
		self.preview._preview_navigation_away = Mock(return_value=False)
		self.preview._owns_preview = Mock(return_value=False)

		self.assertTrue(self.preview.tick())
		self.assertEqual(self.commands, [])
		self.assertFalse(self.preview.pending_stop_trailer)
		self.assertNotIn(self.entry.TRAILER_PREVIEW_PROPERTY, self.properties)


if __name__ == '__main__':
	unittest.main()
