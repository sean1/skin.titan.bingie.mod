import sys
import types
import unittest
from queue import Empty
from threading import Event
from unittest.mock import Mock, call, patch

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

	def test_autoplay_stops_for_modal_or_busy_dialog(self):
		for condition in ('System.HasActiveModalDialog', 'Window.IsVisible(busydialog)', 'Window.IsVisible(busydialognocancel)'):
			with self.subTest(condition=condition):
				self.setUp()
				self._activate()
				self.visibility[condition] = True

				self.preview.tick()
				self.assertEqual(self.commands, ['PlayerControl(Stop)'])
				self.assertFalse(self.preview.active)

	def test_background_refresh_keeps_preview_on_the_same_card(self):
		self._activate()
		self.visibility['Container.IsUpdating'] = True

		self.preview.tick()

		self.assertTrue(self.preview.active)
		self.assertEqual(self.commands, [])
		self.labels['Container(510).CurrentItem'] = '2'
		self.preview.tick()
		self.assertFalse(self.preview.active)
		self.assertEqual(self.commands, ['PlayerControl(Stop)'])

	def test_autoplay_card_change_restarts_the_dwell_delay_for_same_title(self):
		self.entry.monotonic = Mock(return_value=20.0)
		self.preview._candidate = Mock(return_value=self._candidate())
		self.preview._start_preview_preparation = Mock()
		self.preview.tick()
		self.entry.monotonic.return_value = 20.75
		self.labels['Container(510).CurrentItem'] = '2'
		self.preview.tick()
		self.entry.monotonic.return_value = 21.0
		self.preview.tick()
		self.preview._start_preview_preparation.assert_not_called()

		self.entry.monotonic.return_value = 22.0
		self.preview.tick()
		self.preview._start_preview_preparation.assert_called_once_with('movie|1', 'trailer-url')

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

	def test_movie_details_remain_manual_when_cast_or_shelf_has_focus(self):
		self.properties['PovInfoType'] = 'movie'
		self.visibility.update({self.entry.TRAILER_PREVIEW_SPECIAL_CONTEXTS: True, 'Window.IsActive(1123)': True, 'Control.HasFocus(550)': True})
		self.assertEqual(self.preview._preview_context(), 'info')

	def test_manual_request_on_new_screen_survives_outgoing_autoplay_stop(self):
		self._activate()
		self.window_id = 11123
		self.preview._candidate.return_value = ('info|movie|1', 'trailer-url', 'movie', '1', True, False)
		self.properties[self.entry.TRAILER_PREVIEW_REQUEST_PROPERTY] = 'true'
		self.preview._start_preview_preparation = Mock()

		self.preview.tick()
		self.assertEqual(self.commands, ['PlayerControl(Stop)'])
		self.assertEqual(self.preview.manual_identity, 'info|movie|1')
		self.visibility.update({'Player.HasMedia': False, 'Player.HasVideo': False})
		self.entry.monotonic.return_value = 20.25
		self.preview.tick()
		self.preview._start_preview_preparation.assert_called_once_with('info|movie|1', 'trailer-url')

	def test_pending_stop_retains_playing_trailer_and_retries_after_deadline(self):
		self._activate()
		self.preview._candidate.return_value = None
		player = self.preview.preview_player
		self.preview.tick()
		self.entry.monotonic.return_value = 31.0
		self.preview.tick()

		self.assertIs(self.preview.pending_stop_player, player)
		self.assertEqual(self.commands, ['PlayerControl(Stop)', 'PlayerControl(Stop)'])
		self.visibility.update({'Player.HasMedia': False, 'Player.HasVideo': False})
		self.preview.tick()
		self.assertFalse(self.preview.pending_stop_trailer)

	def test_pending_stop_retains_media_with_unknown_path_beyond_deadline(self):
		self._activate()
		self.preview._owns_preview.return_value = None
		self.preview._candidate.return_value = None
		self.preview.tick()
		self.entry.monotonic.return_value = 31.0
		self.preview.tick()
		self.assertEqual(self.preview.pending_stop_trailer, 'preview-url')
		self.assertEqual(self.commands, [])

		self.preview._owns_preview.return_value = True
		self.preview.tick()
		self.assertEqual(self.commands, ['PlayerControl(Stop)'])

	def test_pause_requires_fresh_autoplay_dwell_after_resume(self):
		self.entry.monotonic = Mock(return_value=20.0)
		self.preview._candidate = Mock(return_value=self._candidate())
		self.preview._start_preview_preparation = Mock()
		self.preview.tick()
		self.entry.monotonic.return_value = 25.0
		self.preview.pause()
		self.preview.tick()
		self.preview._start_preview_preparation.assert_not_called()
		self.entry.monotonic.return_value = 26.0
		self.preview.tick()
		self.preview._start_preview_preparation.assert_called_once_with('movie|1', 'trailer-url')

	def test_inactive_preview_window_skips_skin_and_transition_reads(self):
		self.entry.kodi_utils.get_visibility = Mock(return_value=False)
		self.entry.kodi_utils.xbmc.getSkinDir = Mock(return_value='skin.titan.bingie.lite')
		self.entry.get_property = Mock(return_value='')

		self.assertEqual(self.preview._preview_context(), '')
		self.entry.kodi_utils.get_visibility.assert_called_once_with(self.entry.TRAILER_PREVIEW_WINDOW_VISIBILITY)
		self.entry.kodi_utils.xbmc.getSkinDir.assert_not_called()
		self.entry.get_property.assert_not_called()

	def test_active_preview_context_preserves_skin_and_transition_guards(self):
		visibility = {
			self.entry.TRAILER_PREVIEW_WINDOW_VISIBILITY: True,
			'Window.IsActive(VideoOSD)': False,
			self.entry.TRAILER_PREVIEW_SPECIAL_CONTEXTS: False,
			'ControlGroup(77777).HasFocus()': True,
		}
		self.entry.kodi_utils.get_visibility = Mock(side_effect=lambda condition: visibility[condition])
		self.entry.kodi_utils.xbmc.getSkinDir = Mock(return_value='skin.titan.bingie.lite')
		self.entry.get_property = Mock(return_value='')

		self.assertEqual(self.preview._preview_context(), 'listing')
		self.entry.kodi_utils.xbmc.getSkinDir.assert_called_once_with()
		self.entry.get_property.assert_called_once_with('PovInfoTransition')

		self.entry.kodi_utils.xbmc.getSkinDir.reset_mock()
		self.entry.get_property.reset_mock()
		self.entry.kodi_utils.xbmc.getSkinDir.return_value = 'skin.other'

		self.assertEqual(self.preview._preview_context(), '')
		self.entry.kodi_utils.xbmc.getSkinDir.assert_called_once_with()
		self.entry.get_property.assert_not_called()

		self.entry.kodi_utils.xbmc.getSkinDir.return_value = 'skin.titan.bingie.lite'
		self.entry.get_property.return_value = 'true'
		self.entry.kodi_utils.get_visibility.reset_mock()

		self.assertEqual(self.preview._preview_context(), '')
		self.assertEqual(self.entry.kodi_utils.get_visibility.call_args_list, [
			call(self.entry.TRAILER_PREVIEW_WINDOW_VISIBILITY), call('Window.IsActive(VideoOSD)')
		])

	def test_preview_context_preserves_focus_rejections(self):
		window = self.entry.TRAILER_PREVIEW_WINDOW_VISIBILITY
		special = self.entry.TRAILER_PREVIEW_SPECIAL_CONTEXTS
		osd = 'Window.IsActive(VideoOSD)'
		actor_focus = 'Control.HasFocus(610) | Control.HasFocus(620) | Control.HasFocus(630)'
		for label, values in (
			('actor', {window: True, osd: False, special: True, 'Window.IsActive(1123)': False, 'Window.IsActive(DialogVideoInfo.xml)': False,
				'Window.IsActive(1122)': True, actor_focus: False}),
			('videos', {window: True, osd: False, special: True, 'Window.IsActive(1123)': False, 'Window.IsActive(DialogVideoInfo.xml)': False,
				'Window.IsActive(1122)': False, 'Window.IsActive(Videos)': True, 'Control.HasFocus(523)': False}),
			('default', {window: True, osd: False, special: False, 'ControlGroup(77777).HasFocus()': False}),
		):
			with self.subTest(label=label):
				self.entry.kodi_utils.get_visibility = Mock(side_effect=lambda condition: values[condition])
				self.entry.kodi_utils.xbmc.getSkinDir = Mock(return_value='skin.titan.bingie.lite')
				self.entry.get_property = Mock(return_value='')

				self.assertEqual(self.preview._preview_context(), '')

	def test_preview_context_rechecks_window_transitions_before_selecting_data_source(self):
		window = self.entry.TRAILER_PREVIEW_WINDOW_VISIBILITY
		special = self.entry.TRAILER_PREVIEW_SPECIAL_CONTEXTS
		osd = 'Window.IsActive(VideoOSD)'
		for label, results, expected, expected_calls in (
			('opening', (True, False, False, True, True, True), 'info',
				(window, osd, special, 'ControlGroup(77777).HasFocus()', special, 'Window.IsActive(1123)')),
			('closing', (True, False, True, False, False, False, False, True), 'listing',
				(window, osd, special, 'Window.IsActive(1123)', 'Window.IsActive(DialogVideoInfo.xml)', 'Window.IsActive(1122)', 'Window.IsActive(Videos)',
					'ControlGroup(77777).HasFocus()')),
		):
			with self.subTest(label=label):
				self.entry.kodi_utils.get_visibility = Mock(side_effect=results)
				self.entry.kodi_utils.xbmc.getSkinDir = Mock(return_value='skin.titan.bingie.lite')
				self.entry.get_property = Mock(return_value='')

				self.assertEqual(self.preview._preview_context(), expected)
				self.assertEqual(self.entry.kodi_utils.get_visibility.call_args_list, [call(condition) for condition in expected_calls])

	def test_preview_context_rechecks_higher_priority_windows_after_focus(self):
		window = self.entry.TRAILER_PREVIEW_WINDOW_VISIBILITY
		special = self.entry.TRAILER_PREVIEW_SPECIAL_CONTEXTS
		non_listing = self.entry.TRAILER_PREVIEW_NON_LISTING_CONTEXTS
		osd = 'Window.IsActive(VideoOSD)'
		conditions = (
			window, osd, special, 'Window.IsActive(1123)', 'Window.IsActive(DialogVideoInfo.xml)', 'Window.IsActive(1122)', 'Window.IsActive(Videos)',
			'Control.HasFocus(523)', non_listing, 'Window.IsActive(1123)'
		)
		self.entry.kodi_utils.get_visibility = Mock(side_effect=(True, False, True, False, False, False, True, True, True, True))
		self.entry.kodi_utils.xbmc.getSkinDir = Mock(return_value='skin.titan.bingie.lite')
		self.entry.get_property = Mock(return_value='')

		self.assertEqual(self.preview._preview_context(), 'info')
		self.assertEqual(self.entry.kodi_utils.get_visibility.call_args_list, [call(condition) for condition in conditions])

	def test_detail_waits_for_explicit_trailer_request(self):
		for context, media_type in ((context, media_type) for context in ('info', 'dialog') for media_type in ('movie', 'tvshow')):
			with self.subTest(context=context, media_type=media_type):
				self.properties.clear()
				self.preview = self._new_preview()
				self.properties.update({'PovInfoType': media_type, 'PovInfoTmdb': '123', 'PovInfoTrailer': 'trailer-url'})
				labels = {'Window.Property(PovInfoType)': media_type, 'Window.Property(PovInfoTmdb)': '123', 'ListItem.Trailer': 'trailer-url'}
				self.entry.kodi_utils.get_infolabel = lambda label: labels.get(label, '')
				self.preview._preview_context = Mock(return_value=context)
				self.preview._start_preview_preparation = Mock()
				self.entry.monotonic = Mock(return_value=10.0)

				self.preview.tick()
				self.entry.monotonic.return_value = 20.0
				self.preview.tick()
				self.preview._start_preview_preparation.assert_not_called()
				self.properties[self.entry.TRAILER_PREVIEW_REQUEST_PROPERTY] = 'true'
				self.preview.tick()

				self.preview._start_preview_preparation.assert_called_once_with('info|%s|123' % media_type, 'trailer-url')
				self.assertNotIn(self.entry.TRAILER_PREVIEW_REQUEST_PROPERTY, self.properties)

	def test_detail_shelves_never_autoplay_without_valid_type(self):
		for media_type in ('', 'unknown'):
			for control in ('563', '560'):
				with self.subTest(media_type=media_type, control=control):
					self.setUp()
					self.window_id = 11123
					self.properties.update({'PovInfoType': media_type, 'PovInfoTmdb': '123', 'PovInfoTrailer': 'detail-trailer-url'})
					self.visibility.update({self.entry.TRAILER_PREVIEW_SPECIAL_CONTEXTS: True, 'Window.IsActive(1123)': True,
						'Control.HasFocus(563) | Control.HasFocus(560)': True})
					self.labels.update({'System.CurrentControlID': control, 'Container(%s).CurrentItem' % control: '1', 'Container(%s).FolderPath' % control: 'plugin://related',
						'Container.ListItem.Property(DBTYPE)': 'movie', 'Container.ListItem.Property(tmdb_id)': '456', 'Container.ListItem.Label': 'Related title',
						'Container.ListItem.Property(trailer)': 'related-trailer-url'})
					self.preview._ensure_focused_metadata = Mock()
					self.preview._start_pending_preparation = Mock()
					self.entry.monotonic = Mock(return_value=10.0)

					self.preview.tick()
					self.entry.monotonic.return_value = 12.0
					self.preview.tick()

					self.assertEqual(self.preview.prepare_identity, '')
					self.assertIsNone(self.preview.prepare_pending)
					self.assertFalse(self.preview.active)

	def test_prepared_listing_trailer_cannot_launch_on_detail_without_type(self):
		self.entry.monotonic = Mock(return_value=20.0)
		self.labels.update({'Container.ListItem.DBType': 'movie', 'Container.ListItem.UniqueID(tmdb)': '456', 'Container.ListItem.Label': 'Related title',
			'Container.ListItem.Trailer': 'trailer-url', 'Container.ListItem.Property(DBTYPE)': 'movie', 'Container.ListItem.Property(tmdb_id)': '456',
			'Container.ListItem.Property(trailer)': 'trailer-url'})
		self._queue_prepared_preview()
		self.window_id = 11123
		self.visibility.update({self.entry.TRAILER_PREVIEW_SPECIAL_CONTEXTS: True, 'Window.IsActive(1123)': True,
			'Control.HasFocus(563) | Control.HasFocus(560)': True})
		self.labels.update({'System.CurrentControlID': '563', 'Container(563).CurrentItem': '1', 'Container(563).FolderPath': 'plugin://related'})
		trailers = types.ModuleType('modules.trailers')
		trailers.commit_prepared = Mock()

		with patch.dict(sys.modules, {'modules.trailers': trailers}): self.preview.tick()

		trailers.commit_prepared.assert_not_called()
		self.assertEqual(self.preview.prepare_identity, '')
		self.assertFalse(self.preview.active)
		self.assertIsNone(self.preview.preview_player)

	def test_listing_candidate_rejects_invalid_media_before_dependent_reads(self):
		self.preview._preview_context = Mock(return_value='listing')
		self.entry.kodi_utils.get_infolabel = Mock(return_value='episode')

		self.assertIsNone(self.preview._candidate())
		self.entry.kodi_utils.get_infolabel.assert_called_once_with('Container.ListItem.DBType')

		values = {
			'Container.ListItem.DBType': 'movie',
			'Container.ListItem.Trailer': 'trailer-url',
			'Container.ListItem.Property(PovLiteSummary)': 'true',
			'Container.ListItem.Label': 'Movie',
			'Container.ListItem.UniqueID(tmdb)': '123',
			'Container.ListItem.Property(PovFocusIdentity)': 'listing|movie|123',
		}
		self.entry.kodi_utils.get_infolabel = Mock(side_effect=lambda label: values[label])

		self.assertEqual(self.preview._candidate(), ('listing|movie|123', 'trailer-url', 'movie', '123', False, True))
		self.assertEqual(self.entry.kodi_utils.get_infolabel.call_args_list, [call(label) for label in values])

	def test_preview_ownership_requires_the_exact_request_url(self):
		self.preview.trailer = 'preview-url'
		self.entry.get_property = Mock(return_value='resolved-url')
		self.entry.kodi_utils.get_infolabel = Mock(return_value='preview-url')

		self.assertTrue(self.preview._owns_preview())
		self.entry.get_property.assert_not_called()

		self.preview.trailer = 'http://127.0.0.1:1234/preview.m3u8?token=old'
		self.entry.kodi_utils.get_infolabel.return_value = 'http://127.0.0.1:1234/preview.m3u8?token=new'
		self.assertFalse(self.preview._owns_preview())
		self.entry.get_property.assert_not_called()

		self.preview.trailer = 'preview-url'
		self.entry.kodi_utils.get_infolabel.return_value = 'resolved-url'
		self.assertFalse(self.preview._owns_preview())
		self.entry.get_property.assert_not_called()

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

	def test_tick_drops_stale_pending_lookup_before_scheduling_new_focus(self):
		keys = [('listing|movie|%s' % item_id, False) for item_id in range(1, 5)]
		started, released, finished, _ = self._lookup_harness({key: 'trailer-%s' % key[0][-1] for key in keys})
		for key in keys[:2]:
			self.preview._start_trailer_lookup(key[0], 'movie', key[0][-1])
			self._wait(started[key])
		self.preview._start_trailer_lookup(keys[2][0], 'movie', '3')
		self.preview.identity = keys[2][0]
		self.preview._candidate = Mock(return_value=(keys[3][0], '', 'movie', '4', False, False))
		self.entry.monotonic = Mock(side_effect=(10.0, 13.0))
		released[keys[0]].set()
		self._wait(finished[keys[0]])

		self.assertTrue(self.preview.tick())
		self.assertIsNone(self.preview.lookup_pending)
		self.assertFalse(started[keys[2]].is_set())
		self.assertFalse(started[keys[3]].is_set())
		self.assertTrue(self.preview.tick())
		self._wait(started[keys[3]])

		self.assertFalse(started[keys[2]].is_set())
		self.assertEqual(set(self.preview.lookup_workers), {keys[1], keys[3]})
		for key in (keys[1], keys[3]): released[key].set()
		for key in (keys[1], keys[3]): self._wait(finished[key])

	def test_stale_focused_failure_records_only_its_retry(self):
		old_key, new_key = ('listing|movie|1', True), ('listing|movie|2', True)
		started, released, finished, _ = self._lookup_harness({old_key: None, new_key: {'genre': 'New genre', 'trailer': 'new-trailer'}})
		self.preview.identity = old_key[0]
		self.preview._start_focused_metadata_lookup(old_key[0], 'movie', '1')
		self._wait(started[old_key])
		self.preview.identity = new_key[0]
		self.preview._start_focused_metadata_lookup(new_key[0], 'movie', '2')
		self._wait(started[new_key])
		released[new_key].set()
		self._wait(finished[new_key])
		self.preview._consume_lookup_results()
		self.entry.monotonic = Mock(return_value=40.0)
		released[old_key].set()
		self._wait(finished[old_key])
		self.preview._consume_lookup_results()

		self.assertEqual(self.preview.focused_metadata_retries, {old_key[0]: 42.0})
		self.assertEqual(self.properties[self.entry.FOCUSED_METADATA_IDENTITY_PROPERTY], new_key[0])
		self.assertEqual(self.properties['PovFocusedGenre'], 'New genre')


	def test_close_discards_pending_and_prevents_results_or_new_workers(self):
		keys = [('listing|movie|%s' % item_id, False) for item_id in range(1, 4)]
		started, released, finished, calls = self._lookup_harness({key: 'trailer-%s' % key[0][-1] for key in keys})
		for key in keys[:2]:
			self.preview._start_trailer_lookup(key[0], 'movie', key[0][-1])
			self._wait(started[key])
		self.preview._start_trailer_lookup(keys[2][0], 'movie', '3')
		self.preview.resolved_trailers['old'] = 'trailer'
		self.preview.resolved_focused_metadata['old'] = {'genre': 'Drama'}
		self.preview.focused_metadata_retries['old'] = 10.0
		self.preview.prepared_trailers['old'] = (0.0, ('url', 'manifest'))
		self.preview.prepare_results.put((1, 'old', 'trailer', None, None, 0.0))
		self.preview.prepare_workers[1] = (None, 'old', 'trailer')
		trailers = types.ModuleType('modules.trailers')
		trailers.stop_manifest_server = Mock()
		old_trailers = sys.modules.get('modules.trailers')
		sys.modules['modules.trailers'] = trailers
		try: self.preview.close()
		finally:
			if old_trailers is None: sys.modules.pop('modules.trailers', None)
			else: sys.modules['modules.trailers'] = old_trailers

		self.assertIsNone(self.preview.lookup_pending)
		self.assertEqual(self.preview.lookup_workers, {})
		self.assertEqual(self.preview.prepare_workers, {})
		self.assertEqual(self.preview.resolved_trailers, {})
		self.assertEqual(self.preview.resolved_focused_metadata, {})
		self.assertEqual(self.preview.focused_metadata_retries, {})
		self.assertEqual(self.preview.prepared_trailers, {})
		with self.assertRaises(Empty): self.preview.prepare_results.get_nowait()
		self.preview._start_trailer_lookup(keys[2][0], 'movie', '3')
		self.assertEqual(calls, keys[:2])
		for key in keys[:2]: released[key].set()
		for key in keys[:2]: self._wait(finished[key])
		with self.assertRaises(Empty): self.preview.lookup_results.get_nowait()



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

	def test_player_wrapper_is_retained_until_starting_playback_has_stopped(self):
		self.entry.monotonic = Mock(return_value=20.0)
		self.preview._launch_preview('preview-url', None)
		player = self.preview.preview_player
		self.visibility['Player.HasMedia'] = True
		self.preview._owns_preview = Mock(return_value=True)

		self.preview._begin_preview_stop(21.0)
		self.preview._stop_pending_preview(21.0)

		self.assertIsNone(self.preview.preview_player)
		self.assertIs(self.preview.pending_stop_player, player)
		self.assertEqual(self.commands, ['PlayerControl(Stop)'])
		self.visibility['Player.HasMedia'] = False
		self.preview._stop_pending_preview(21.1)
		self.assertIsNone(self.preview.pending_stop_player)
		self.assertFalse(self.preview.pending_stop_trailer)

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

	def test_screen_exit_retains_preview_until_playing_path_returns_and_playback_stops(self):
		for playback_started in (False, True):
			with self.subTest(playback_started=playback_started):
				self.setUp()
				self._activate()
				player = self.preview.preview_player
				del self.preview._owns_preview
				self.preview.playback_started = playback_started
				self.preview._preview_window_active.return_value = False
				self.preview._candidate = Mock(return_value=None)

				self.assertTrue(self.preview.tick())
				self.assertEqual(self.commands, [])
				self.assertFalse(self.preview.active)
				self.assertEqual(self.preview.pending_stop_trailer, 'preview-url')
				self.assertIs(self.preview.pending_stop_player, player)
				self.assertNotIn(self.entry.TRAILER_PREVIEW_READY_PROPERTY, self.properties)
				player.onAVStarted()
				self.assertEqual(self.preview.av_started_generation, -1)

				self.entry.monotonic.return_value = 20.25
				self.entry.kodi_utils.get_infolabel = lambda label: 'preview-url' if label == 'Player.FilenameAndPath' else ''
				self.assertTrue(self.preview.tick())
				self.assertEqual(self.commands, ['PlayerControl(Stop)'])
				self.assertEqual(self.properties[self.entry.TRAILER_PREVIEW_PROPERTY], 'true')
				self.assertIs(self.preview.pending_stop_player, player)

				self.entry.monotonic.return_value = 20.5
				self.visibility.update({'Player.HasMedia': False, 'Player.HasVideo': False})
				self.entry.kodi_utils.get_infolabel = lambda label: ''
				self.assertFalse(self.preview.tick())
				self.assertEqual(self.commands, ['PlayerControl(Stop)'])
				self.assertIsNone(self.preview.pending_stop_player)
				self.assertFalse(self.preview.pending_stop_trailer)
				self.assertNotIn(self.entry.TRAILER_PREVIEW_PROPERTY, self.properties)

	def test_screen_exit_does_not_stop_other_video_after_playing_path_returns(self):
		self._activate()
		del self.preview._owns_preview
		self.preview.playback_started = True
		self.preview._preview_window_active.return_value = False
		self.preview._candidate = Mock(return_value=None)

		self.assertTrue(self.preview.tick())
		self.entry.monotonic.return_value = 20.25
		self.entry.kodi_utils.get_infolabel = lambda label: 'other-video' if label == 'Player.FilenameAndPath' else ''
		self.assertFalse(self.preview.tick())

		self.assertEqual(self.commands, [])
		self.assertFalse(self.preview.pending_stop_trailer)
		self.assertIsNone(self.preview.pending_stop_player)
		self.assertNotIn(self.entry.TRAILER_PREVIEW_PROPERTY, self.properties)

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
