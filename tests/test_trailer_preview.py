import sys
import types
import unittest
from threading import Event
from unittest.mock import Mock, patch

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
		self.visibility = {'Player.HasMedia': False, 'Player.HasVideo': False, self.entry.TRAILER_PREVIEW_WINDOW_VISIBILITY: True, 'ControlGroup(77777).HasFocus()': True}
		self.labels = {'System.CurrentControlID': '510', 'Container(510).CurrentItem': '1', 'Container(510).FolderPath': 'plugin://listing'}
		self.window_id, self.dialog_id = 10000, 0
		self.entry.set_property = self.properties.__setitem__
		self.entry.get_property = lambda key: self.properties.get(key, '')
		self.entry.clear_property = lambda key: self.properties.pop(key, None)
		self.entry.kodi_utils.get_visibility = lambda condition: self.visibility.get(condition, False)
		self.entry.kodi_utils.get_infolabel = lambda label: self.labels.get(label, '')
		self.entry.kodi_utils.execute_builtin = self.commands.append
		self.entry.kodi_utils.xbmc = types.SimpleNamespace(getSkinDir=lambda: 'skin.titan.bingie.lite')
		self.entry.kodi_utils.xbmcgui = types.SimpleNamespace(getCurrentWindowId=lambda: self.window_id, getCurrentWindowDialogId=lambda: self.dialog_id)
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

	def _lookup_harness(self, results):
		started = {key: Event() for key in results}
		released = {key: Event() for key in results}
		finished = {key: Event() for key in results}
		calls = []

		def lookup(identity, media_type, tmdb_id, focused, result_queue, result_gate):
			key = identity, focused
			calls.append(key)
			started[key].set()
			try:
				released[key].wait()
				result_gate.publish(result_queue, (identity, focused, results[key]))
			finally: finished[key].set()

		def cleanup():
			for event in released.values(): event.set()
			for key, event in started.items():
				if event.is_set(): finished[key].wait(2.0)

		self.preview._lookup_media = lookup
		self.addCleanup(cleanup)
		return started, released, finished, calls

	def _wait(self, event):
		self.assertTrue(event.wait(2.0))

	def _activate(self):
		self.entry.monotonic = Mock(return_value=20.0)
		self.preview.identity = 'movie|1'
		self.preview._candidate = Mock(return_value=self._candidate())
		self.preview._preview_window_active = Mock(return_value=True)
		self.preview.focus_token = self.preview._eligible_candidate()[1]
		self.preview._launch_preview('preview-url', None)
		self.preview.preview_player.onAVStarted()
		self.preview._publish_preview_ready()
		self.visibility.update({'Player.HasMedia': True, 'Player.HasVideo': True})
		self.preview._preview_window_active = Mock(return_value=True)
		self.preview._owns_preview = Mock(return_value=True)

	def test_autoplay_stops_on_screen_or_card_change_even_for_same_title(self):
		for change in ('screen', 'row', 'position', 'folder'):
			with self.subTest(change=change):
				self.setUp()
				self._activate()
				self.assertTrue(self.preview.tick())
				self.assertEqual(self.commands, [])
				if change == 'screen': self.window_id = 11111
				elif change == 'row':
					self.labels.update({'System.CurrentControlID': '520', 'Container(520).CurrentItem': '1', 'Container(520).FolderPath': 'plugin://listing'})
				elif change == 'position': self.labels['Container(510).CurrentItem'] = '2'
				else: self.labels['Container(510).FolderPath'] = 'plugin://other-listing'

				self.assertTrue(self.preview.tick())
				self.assertEqual(self.commands, ['PlayerControl(Stop)'])
				self.assertFalse(self.preview.active)


	def _queue_prepared_preview(self):
		candidate, token = self.preview._eligible_candidate()
		self.preview._track_candidate(candidate, 18.0, token)
		self.preview._start_pending_preparation = Mock()
		self.preview._start_preview_preparation(candidate[0], candidate[1])
		self.preview.prepare_results.put((self.preview.prepare_generation, candidate[0], candidate[1], ('prepared-url', None), None, 20.0))

	def test_prepared_autoplay_cannot_launch_after_same_title_screen_or_card_change(self):
		for change_during_commit in (False, True):
			with self.subTest(change_during_commit=change_during_commit):
				self.setUp()
				self.entry.monotonic = Mock(return_value=20.0)
				self.preview._candidate = Mock(return_value=self._candidate())
				self._queue_prepared_preview()
				generation = self.preview.prepare_generation
				trailers = types.ModuleType('modules.trailers')
				def commit(prepared):
					self.labels['Container(510).CurrentItem'] = '2'
					return 'preview-url', None
				trailers.commit_prepared = Mock(side_effect=commit)
				if not change_during_commit: self.window_id = 11111

				with patch.dict(sys.modules, {'modules.trailers': trailers}): self.preview.tick()

				self.assertFalse(self.preview.active)
				self.assertIsNone(self.preview.preview_player)
				self.assertEqual(self.preview.prepare_identity, '')
				self.assertNotIn(self.entry.TRAILER_PREVIEW_PROPERTY, self.properties)
				if change_during_commit: trailers.commit_prepared.assert_called_once()
				else:
					trailers.commit_prepared.assert_not_called()
					self.assertGreater(self.preview.prepare_generation, generation)

	def test_manual_trailer_requires_request_and_survives_detail_button_changes(self):
		for context, window_id, dialog_id in (('info', 11123, 0), ('dialog', 10000, 12003)):
			with self.subTest(context=context):
				self.setUp()
				self.entry.monotonic = Mock(return_value=20.0)
				self.window_id, self.dialog_id = window_id, dialog_id
				self.preview._preview_context = Mock(return_value=context)
				self.properties.update({'PovInfoType': 'movie', 'PovInfoTmdb': '123', 'PovInfoTrailer': 'trailer-url'})
				self.labels.update({'Window.Property(PovInfoType)': 'movie', 'Window.Property(PovInfoTmdb)': '123', 'ListItem.Trailer': 'trailer-url'})
				if dialog_id:
					self.visibility.update({'System.HasActiveModalDialog': True, 'Window.IsModalDialogTopmost(DialogVideoInfo.xml)': True})
				self.preview._start_pending_preparation = Mock()
				self.preview.tick()
				self.entry.monotonic.return_value = 22.0
				self.preview.tick()
				self.assertEqual(self.preview.prepare_identity, '')

				self.properties[self.entry.TRAILER_PREVIEW_REQUEST_PROPERTY] = 'true'
				self.preview.tick()
				self._queue_prepared_preview()
				trailers = types.ModuleType('modules.trailers')
				trailers.commit_prepared = Mock(return_value=('preview-url', None))
				with patch.dict(sys.modules, {'modules.trailers': trailers}): self.preview.tick()
				self.assertTrue(self.preview.active)

				self.visibility.update({'Player.HasMedia': True, 'Player.HasVideo': True})
				self.labels.update({'Player.FilenameAndPath': 'preview-url', 'System.CurrentControlID': '550'})
				self.preview.tick()
				self.assertTrue(self.preview.active)
				self.assertEqual(self.commands, [])

				self.preview._preview_context.return_value = ''
				self.preview.tick()
				self.assertFalse(self.preview.active)
				self.assertEqual(self.commands, ['PlayerControl(Stop)'])


	def test_new_focused_lookup_overtakes_stale_lookup_without_stale_publication(self):
		old_key, new_key = ('listing|movie|1', True), ('listing|movie|2', True)
		started, released, finished, _ = self._lookup_harness({
			old_key: {'genre': 'Old genre', 'trailer': 'old-trailer'},
			new_key: {'genre': 'New genre', 'trailer': 'new-trailer'},
		})
		self.preview.identity = old_key[0]
		self.preview._start_focused_metadata_lookup(old_key[0], 'movie', '1')
		self._wait(started[old_key])

		self.preview.identity = new_key[0]
		self.preview._start_focused_metadata_lookup(new_key[0], 'movie', '2')
		self._wait(started[new_key])
		released[new_key].set()
		self._wait(finished[new_key])
		self.preview._consume_lookup_results()

		self.assertEqual(self.properties[self.entry.FOCUSED_METADATA_IDENTITY_PROPERTY], new_key[0])
		self.assertEqual(self.properties['PovFocusedGenre'], 'New genre')
		released[old_key].set()
		self._wait(finished[old_key])
		self.preview._consume_lookup_results()

		self.assertEqual(self.properties[self.entry.FOCUSED_METADATA_IDENTITY_PROPERTY], new_key[0])
		self.assertEqual(self.properties['PovFocusedGenre'], 'New genre')
		self.assertEqual(self.preview.resolved_focused_metadata[old_key[0]]['genre'], 'Old genre')
		self.assertEqual(self.preview.resolved_trailers[old_key[0]], 'old-trailer')

	def test_lookup_workers_are_bounded_and_only_latest_pending_lookup_runs(self):
		keys = [('listing|movie|%s' % item_id, False) for item_id in range(1, 5)]
		started, released, finished, calls = self._lookup_harness({key: 'trailer-%s' % key[0][-1] for key in keys})
		for key in keys[:2]:
			self.preview._start_trailer_lookup(key[0], 'movie', key[0][-1])
			self._wait(started[key])
		self.preview._start_trailer_lookup(keys[2][0], 'movie', '3')
		self.preview._start_trailer_lookup(keys[3][0], 'movie', '4')

		self.assertEqual(len(self.preview.lookup_workers), self.entry.TRAILER_LOOKUP_WORKERS)
		self.assertEqual((self.preview.lookup_pending[0], self.preview.lookup_pending[3]), keys[3])
		self.assertFalse(started[keys[2]].is_set())
		self.assertFalse(started[keys[3]].is_set())

		self.preview._start_trailer_lookup(keys[0][0], 'movie', '1')
		self.assertIsNone(self.preview.lookup_pending)
		self.preview._start_trailer_lookup(keys[2][0], 'movie', '3')
		self.preview._start_trailer_lookup(keys[3][0], 'movie', '4')
		released[keys[0]].set()
		self._wait(finished[keys[0]])
		self.preview._consume_lookup_results()
		self.assertFalse(started[keys[3]].is_set())
		self.preview._start_trailer_lookup(keys[3][0], 'movie', '4')
		self._wait(started[keys[3]])

		self.assertFalse(started[keys[2]].is_set())
		self.assertEqual(calls.count(keys[0]), 1)
		self.assertLessEqual(len(self.preview.lookup_workers), self.entry.TRAILER_LOOKUP_WORKERS)
		for key in (keys[1], keys[3]): released[key].set()
		for key in (keys[1], keys[3]): self._wait(finished[key])
		self.preview._consume_lookup_results()


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
