import sys
import types
import unittest
from queue import Empty
from threading import Event
from unittest.mock import Mock, call

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

	def _lookup_harness(self, results):
		started = {key: Event() for key in results}
		released = {key: Event() for key in results}
		finished = {key: Event() for key in results}
		calls = []

		def lookup(identity, media_type, tmdb_id, focused):
			key = identity, focused
			calls.append(key)
			started[key].set()
			try:
				released[key].wait()
				if not self.preview.closed: self.preview.lookup_results.put((identity, focused, results[key]))
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
		self.preview._launch_preview('preview-url', None)
		self.preview.preview_player.onAVStarted()
		self.preview._publish_preview_ready()
		self.visibility.update({'Player.HasMedia': True, 'Player.HasVideo': True})
		self.preview._preview_window_active = Mock(return_value=True)
		self.preview._owns_preview = Mock(return_value=True)

	def test_stable_focus_waits_exactly_one_second(self):
		candidate = self._candidate()
		self.preview._candidate = Mock(return_value=candidate)
		self.preview._start_preview_preparation = Mock()
		self.entry.monotonic = Mock(side_effect=(10.0, 10.99, 11.0))

		self.assertTrue(self.preview.tick())
		self.assertTrue(self.preview.tick())
		self.preview._start_preview_preparation.assert_not_called()
		self.assertTrue(self.preview.tick())
		self.preview._start_preview_preparation.assert_called_once_with('movie|1', 'trailer-url')

	def test_cached_summary_metadata_publishes_on_identity_change_tick(self):
		identity = 'listing|movie|2'
		self.preview.identity = 'listing|movie|1'
		self.preview.resolved_focused_metadata[identity] = {'genre': 'Cached genre'}
		self.preview._candidate = Mock(return_value=(identity, 'trailer-url', 'movie', '2', False, True))
		self.preview._start_focused_metadata_lookup = Mock()
		self.entry.monotonic = Mock(return_value=10.0)

		self.assertTrue(self.preview.tick())
		self.assertEqual(self.properties[self.entry.FOCUSED_METADATA_IDENTITY_PROPERTY], identity)
		self.assertEqual(self.properties['PovFocusedGenre'], 'Cached genre')
		self.assertIn(identity, self.preview.resolved_focused_metadata)
		self.preview._start_focused_metadata_lookup.assert_not_called()

	def test_uncached_summary_metadata_keeps_identity_change_debounce(self):
		identity = 'listing|movie|2'
		self.preview.identity = 'listing|movie|1'
		self.preview._candidate = Mock(return_value=(identity, 'trailer-url', 'movie', '2', False, True))
		self.preview._start_focused_metadata_lookup = Mock()
		self.entry.monotonic = Mock(return_value=10.0)

		self.assertTrue(self.preview.tick())
		self.assertNotIn(self.entry.FOCUSED_METADATA_IDENTITY_PROPERTY, self.properties)
		self.preview._start_focused_metadata_lookup.assert_not_called()

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

	def test_preview_context_resolves_special_windows_once_with_existing_precedence(self):
		window = self.entry.TRAILER_PREVIEW_WINDOW_VISIBILITY
		special = self.entry.TRAILER_PREVIEW_SPECIAL_CONTEXTS
		info_card_focus = self.entry.TRAILER_PREVIEW_INFO_CARD_FOCUS
		info_cast_focus = self.entry.TRAILER_PREVIEW_INFO_CAST_FOCUS
		actor_context = self.entry.TRAILER_PREVIEW_ACTOR_CONTEXT
		non_listing = self.entry.TRAILER_PREVIEW_NON_LISTING_CONTEXTS
		osd = 'Window.IsActive(VideoOSD)'
		actor_focus = 'Control.HasFocus(610) | Control.HasFocus(620) | Control.HasFocus(630)'
		for expected, values, expected_calls in (
			('info', {window: True, osd: False, special: True, 'Window.IsActive(1123)': True, info_card_focus: False, info_cast_focus: False},
				(window, osd, special, 'Window.IsActive(1123)', info_card_focus, info_cast_focus)),
			('info_card', {window: True, osd: False, special: True, 'Window.IsActive(1123)': True, info_card_focus: True},
				(window, osd, special, 'Window.IsActive(1123)', info_card_focus)),
			('', {window: True, osd: False, special: True, 'Window.IsActive(1123)': True, info_card_focus: False, info_cast_focus: True},
				(window, osd, special, 'Window.IsActive(1123)', info_card_focus, info_cast_focus)),
			('dialog', {window: True, osd: False, special: True, 'Window.IsActive(1123)': False, 'Window.IsActive(DialogVideoInfo.xml)': True},
				(window, osd, special, 'Window.IsActive(1123)', 'Window.IsActive(DialogVideoInfo.xml)')),
			('actor', {window: True, osd: False, special: True, 'Window.IsActive(1123)': False, 'Window.IsActive(DialogVideoInfo.xml)': False,
				'Window.IsActive(1122)': True, actor_focus: True, actor_context: True},
				(window, osd, special, 'Window.IsActive(1123)', 'Window.IsActive(DialogVideoInfo.xml)', 'Window.IsActive(1122)', actor_focus, actor_context)),
			('listing', {window: True, osd: False, special: True, 'Window.IsActive(1123)': False, 'Window.IsActive(DialogVideoInfo.xml)': False,
				'Window.IsActive(1122)': False, 'Window.IsActive(Videos)': True, 'Control.HasFocus(523)': True, non_listing: False},
				(window, osd, special, 'Window.IsActive(1123)', 'Window.IsActive(DialogVideoInfo.xml)', 'Window.IsActive(1122)', 'Window.IsActive(Videos)',
					'Control.HasFocus(523)', non_listing)),
		):
			with self.subTest(expected=expected):
				self.entry.kodi_utils.get_visibility = Mock(side_effect=lambda condition: values[condition])
				self.entry.kodi_utils.xbmc.getSkinDir = Mock(return_value='skin.titan.bingie.lite')
				self.entry.get_property = Mock(return_value='')

				self.assertEqual(self.preview._preview_context(), expected)
				self.assertEqual(self.entry.kodi_utils.get_visibility.call_args_list, [call(condition) for condition in expected_calls])

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
			('opening', (True, False, False, True, True, True, False, False), 'info',
				(window, osd, special, 'ControlGroup(77777).HasFocus()', special, 'Window.IsActive(1123)', self.entry.TRAILER_PREVIEW_INFO_CARD_FOCUS, self.entry.TRAILER_PREVIEW_INFO_CAST_FOCUS)),
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
			'Control.HasFocus(523)', non_listing, 'Window.IsActive(1123)', self.entry.TRAILER_PREVIEW_INFO_CARD_FOCUS, self.entry.TRAILER_PREVIEW_INFO_CAST_FOCUS
		)
		self.entry.kodi_utils.get_visibility = Mock(side_effect=(True, False, True, False, False, False, True, True, True, True, False, False))
		self.entry.kodi_utils.xbmc.getSkinDir = Mock(return_value='skin.titan.bingie.lite')
		self.entry.get_property = Mock(return_value='')

		self.assertEqual(self.preview._preview_context(), 'info')
		self.assertEqual(self.entry.kodi_utils.get_visibility.call_args_list, [call(condition) for condition in conditions])

	def test_preview_context_retries_when_actor_window_closes_after_focus(self):
		window = self.entry.TRAILER_PREVIEW_WINDOW_VISIBILITY
		special = self.entry.TRAILER_PREVIEW_SPECIAL_CONTEXTS
		actor_context = self.entry.TRAILER_PREVIEW_ACTOR_CONTEXT
		osd = 'Window.IsActive(VideoOSD)'
		actor_focus = 'Control.HasFocus(610) | Control.HasFocus(620) | Control.HasFocus(630)'
		conditions = (
			window, osd, special, 'Window.IsActive(1123)', 'Window.IsActive(DialogVideoInfo.xml)', 'Window.IsActive(1122)', actor_focus, actor_context,
			'Window.IsActive(1123)', 'Window.IsActive(DialogVideoInfo.xml)', 'Window.IsActive(1122)', 'Window.IsActive(Videos)', 'ControlGroup(77777).HasFocus()'
		)
		self.entry.kodi_utils.get_visibility = Mock(side_effect=(True, False, True, False, False, True, True, False, False, False, False, False, True))
		self.entry.kodi_utils.xbmc.getSkinDir = Mock(return_value='skin.titan.bingie.lite')
		self.entry.get_property = Mock(return_value='')

		self.assertEqual(self.preview._preview_context(), 'listing')
		self.assertEqual(self.entry.kodi_utils.get_visibility.call_args_list, [call(condition) for condition in conditions])

	def test_home_candidate_uses_single_context_resolution(self):
		window = self.entry.TRAILER_PREVIEW_WINDOW_VISIBILITY
		special = self.entry.TRAILER_PREVIEW_SPECIAL_CONTEXTS
		visibility = {window: True, 'Window.IsActive(VideoOSD)': False, special: False, 'ControlGroup(77777).HasFocus()': True}
		labels = {
			'Container.ListItem.DBType': 'movie',
			'Container.ListItem.Trailer': 'trailer-url',
			'Container.ListItem.Property(PovLiteSummary)': 'true',
			'Container.ListItem.Label': 'Movie',
			'Container.ListItem.UniqueID(tmdb)': '123',
			'Container.ListItem.Property(PovFocusIdentity)': 'listing|movie|123',
		}
		self.entry.kodi_utils.get_visibility = Mock(side_effect=lambda condition: visibility[condition])
		self.entry.kodi_utils.get_infolabel = Mock(side_effect=lambda label: labels[label])
		self.entry.get_property = Mock(return_value='')

		self.assertEqual(self.preview._candidate(), ('listing|movie|123', 'trailer-url', 'movie', '123', False, True))
		self.assertEqual(self.entry.kodi_utils.get_visibility.call_args_list, [
			call(window), call('Window.IsActive(VideoOSD)'), call(special), call('ControlGroup(77777).HasFocus()'), call(special)
		])

	def test_info_candidate_stages_property_reads_until_prerequisites_are_valid(self):
		self.preview._preview_context = Mock(return_value='info')
		for values, expected, expected_keys in (
			(('episode',), None, ('PovInfoType',)),
			(('movie', '', ''), None, ('PovInfoType', 'PovInfoTmdb', 'PovInfoPendingTmdb')),
			(('movie', '', '123', ''), ('info|movie|123', '', 'movie', '123', True, False), ('PovInfoType', 'PovInfoTmdb', 'PovInfoPendingTmdb', 'PovInfoTrailer')),
			(('movie', '123', 'trailer-url'), ('info|movie|123', 'trailer-url', 'movie', '123', True, False), ('PovInfoType', 'PovInfoTmdb', 'PovInfoTrailer')),
		):
			with self.subTest(values=values):
				self.entry.get_property = Mock(side_effect=values)

				self.assertEqual(self.preview._candidate(), expected)
				self.assertEqual(self.entry.get_property.call_args_list, [call(key) for key in expected_keys])

	def test_detail_card_candidate_uses_its_own_trailer(self):
		self.preview._preview_context = Mock(return_value='info_card')
		values = {
			'Container.ListItem.Property(DBTYPE)': 'movie', 'Container.ListItem.Property(tmdb_id)': '456',
			'Container.ListItem.Label': 'Related movie', 'Container.ListItem.Property(trailer)': 'card-trailer-url'
		}
		self.entry.kodi_utils.get_infolabel = Mock(side_effect=lambda label: values[label])

		self.assertEqual(self.preview._candidate(), ('info-card|movie|456', 'card-trailer-url', 'movie', '456', False, True))
		self.assertEqual(self.entry.kodi_utils.get_infolabel.call_args_list, [call(label) for label in values])

	def test_detail_card_focus_stops_active_page_trailer_in_same_tick(self):
		self._activate()
		self.preview._candidate = Mock(return_value=('info-card|movie|456', 'card-trailer-url', 'movie', '456', False, True))
		self.preview._preview_navigation_away = Mock(return_value=False)

		self.assertTrue(self.preview.tick())
		self.assertEqual(self.commands, ['PlayerControl(Stop)'])
		self.assertFalse(self.preview.active)
		self.assertEqual(self.preview.identity, 'info-card|movie|456')

	def test_dialog_candidate_stages_label_reads_until_prerequisites_are_valid(self):
		self.preview._preview_context = Mock(return_value='dialog')
		for values, expected, expected_labels in (
			(('episode',), None, ('Window.Property(PovInfoType)',)),
			(('tvshow', ''), None, ('Window.Property(PovInfoType)', 'Window.Property(PovInfoTmdb)')),
			(('tvshow', '456', 'trailer-url'), ('info|tvshow|456', 'trailer-url', 'tvshow', '456', False, False), ('Window.Property(PovInfoType)', 'Window.Property(PovInfoTmdb)', 'ListItem.Trailer')),
		):
			with self.subTest(values=values):
				self.entry.kodi_utils.get_infolabel = Mock(side_effect=values)

				self.assertEqual(self.preview._candidate(), expected)
				self.assertEqual(self.entry.kodi_utils.get_infolabel.call_args_list, [call(label) for label in expected_labels])

	def test_actor_candidate_skips_id_and_label_reads_after_rejection(self):
		self.preview._preview_context = Mock(return_value='actor')
		self.entry.kodi_utils.get_infolabel = Mock(return_value='episode')

		self.assertIsNone(self.preview._candidate())
		self.entry.kodi_utils.get_infolabel.assert_called_once_with('Container.ListItem.Property(PovCreditType)')

		values = {
			'Container.ListItem.Property(PovCreditType)': 'movie',
			'Container.ListItem.UniqueID(tmdb)': '', 'ListItem.UniqueID(tmdb)': '',
			'Container.ListItem.Property(tmdb_id)': '', 'ListItem.Property(tmdb_id)': '',
		}
		self.entry.kodi_utils.get_infolabel = Mock(side_effect=lambda label: values[label])

		self.assertIsNone(self.preview._candidate())
		self.assertEqual(self.entry.kodi_utils.get_infolabel.call_args_list, [call(label) for label in values])

		values = {
			'Container.ListItem.Property(PovCreditType)': 'movie',
			'Container.ListItem.UniqueID(tmdb)': '789',
			'Container.ListItem.Label': 'Actor credit',
		}
		self.entry.kodi_utils.get_infolabel = Mock(side_effect=lambda label: values[label])

		self.assertEqual(self.preview._candidate(), ('actor|movie|789', '', 'movie', '789', False, False))
		self.assertEqual(self.entry.kodi_utils.get_infolabel.call_args_list, [call(label) for label in values])

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

	def test_preview_ownership_reads_resolved_fallback_only_after_primary_mismatch(self):
		self.preview.trailer = 'preview-url'
		self.entry.get_property = Mock(return_value='resolved-url')
		self.entry.kodi_utils.get_infolabel = Mock(return_value='preview-url')

		self.assertTrue(self.preview._owns_preview())
		self.entry.get_property.assert_not_called()

		self.preview.trailer = 'http://127.0.0.1:1234/preview.m3u8?token=old'
		self.entry.kodi_utils.get_infolabel.return_value = 'http://127.0.0.1:1234/preview.m3u8?token=new'
		self.assertTrue(self.preview._owns_preview())
		self.entry.get_property.assert_not_called()

		self.preview.trailer = 'preview-url'
		self.entry.kodi_utils.get_infolabel.return_value = 'resolved-url'
		self.assertTrue(self.preview._owns_preview())
		self.entry.get_property.assert_called_once_with(self.entry.TRAILER_RESOLVED_PROPERTY)

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

	def test_focused_lookup_supersedes_pending_trailer_and_subsumes_duplicates(self):
		blockers = [('listing|movie|1', False), ('listing|movie|2', False)]
		trailer_key, focused_key = ('listing|movie|3', False), ('listing|movie|3', True)
		results = {blockers[0]: 'trailer-1', blockers[1]: 'trailer-2', trailer_key: 'trailer-3', focused_key: {'genre': 'Genre', 'trailer': 'trailer-3'}}
		started, released, finished, calls = self._lookup_harness(results)
		for key in blockers:
			self.preview._start_trailer_lookup(key[0], 'movie', key[0][-1])
			self._wait(started[key])
		self.preview._start_trailer_lookup(trailer_key[0], 'movie', '3')
		self.preview._start_focused_metadata_lookup(focused_key[0], 'movie', '3')
		self.preview._start_focused_metadata_lookup(focused_key[0], 'movie', '3')
		self.preview._start_trailer_lookup(trailer_key[0], 'movie', '3')

		self.assertEqual((self.preview.lookup_pending[0], self.preview.lookup_pending[3]), focused_key)
		released[blockers[0]].set()
		self._wait(finished[blockers[0]])
		self.preview._consume_lookup_results()
		self.assertFalse(started[focused_key].is_set())
		self.preview._start_focused_metadata_lookup(focused_key[0], 'movie', '3')
		self._wait(started[focused_key])
		self.preview._start_trailer_lookup(trailer_key[0], 'movie', '3')

		self.assertIsNone(self.preview.lookup_pending)
		self.assertFalse(started[trailer_key].is_set())
		self.assertEqual(calls.count(focused_key), 1)
		for key in (blockers[1], focused_key): released[key].set()
		for key in (blockers[1], focused_key): self._wait(finished[key])
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

	def test_tick_starts_unchanged_pending_lookup_as_soon_as_slot_is_free(self):
		keys = [('listing|movie|%s' % item_id, False) for item_id in range(1, 4)]
		started, released, finished, _ = self._lookup_harness({key: 'trailer-%s' % key[0][-1] for key in keys})
		for key in keys[:2]:
			self.preview._start_trailer_lookup(key[0], 'movie', key[0][-1])
			self._wait(started[key])
		self.preview._start_trailer_lookup(keys[2][0], 'movie', '3')
		self.preview.identity = keys[2][0]
		self.preview.focused_at = 7.0
		self.preview._candidate = Mock(return_value=(keys[2][0], '', 'movie', '3', False, False))
		self.entry.monotonic = Mock(return_value=10.0)
		released[keys[0]].set()
		self._wait(finished[keys[0]])

		self.assertTrue(self.preview.tick())
		self._wait(started[keys[2]])
		self.assertIsNone(self.preview.lookup_pending)
		self.assertEqual(set(self.preview.lookup_workers), {keys[1], keys[2]})
		for key in (keys[1], keys[2]): released[key].set()
		for key in (keys[1], keys[2]): self._wait(finished[key])

	def test_empty_candidate_clears_pending_lookup_when_identity_is_already_empty(self):
		self.preview.lookup_pending = 'listing|movie|1', 'movie', '1', False

		self.preview._track_candidate(None, 10.0)

		self.assertIsNone(self.preview.lookup_pending)

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

	def test_pause_clears_pending_without_cancelling_running_lookups(self):
		keys = [('listing|movie|%s' % item_id, False) for item_id in range(1, 4)]
		started, released, finished, _ = self._lookup_harness({key: 'trailer-%s' % key[0][-1] for key in keys})
		for key in keys[:2]:
			self.preview._start_trailer_lookup(key[0], 'movie', key[0][-1])
			self._wait(started[key])
		self.preview._start_trailer_lookup(keys[2][0], 'movie', '3')

		self.assertFalse(self.preview.pause())
		self.assertIsNone(self.preview.lookup_pending)
		self.assertEqual(set(self.preview.lookup_workers), set(keys[:2]))
		released[keys[0]].set()
		self._wait(finished[keys[0]])
		self.preview._consume_lookup_results()
		self.assertFalse(started[keys[2]].is_set())
		released[keys[1]].set()
		self._wait(finished[keys[1]])
		self.preview._consume_lookup_results()

	def test_close_discards_pending_and_prevents_results_or_new_workers(self):
		keys = [('listing|movie|%s' % item_id, False) for item_id in range(1, 4)]
		started, released, finished, calls = self._lookup_harness({key: 'trailer-%s' % key[0][-1] for key in keys})
		for key in keys[:2]:
			self.preview._start_trailer_lookup(key[0], 'movie', key[0][-1])
			self._wait(started[key])
		self.preview._start_trailer_lookup(keys[2][0], 'movie', '3')
		trailers = types.ModuleType('modules.trailers')
		trailers.stop_manifest_server = Mock()
		old_trailers = sys.modules.get('modules.trailers')
		sys.modules['modules.trailers'] = trailers
		try: self.preview.close()
		finally:
			if old_trailers is None: sys.modules.pop('modules.trailers', None)
			else: sys.modules['modules.trailers'] = old_trailers

		self.assertIsNone(self.preview.lookup_pending)
		self.assertEqual(set(self.preview.lookup_workers), set(keys[:2]))
		self.preview._start_trailer_lookup(keys[2][0], 'movie', '3')
		self.assertEqual(calls, keys[:2])
		for key in keys[:2]: released[key].set()
		for key in keys[:2]: self._wait(finished[key])
		with self.assertRaises(Empty): self.preview.lookup_results.get_nowait()

	def test_lookup_thread_start_failure_uses_normal_failure_result(self):
		class FailingThread:
			def __init__(self, **kwargs): pass

			def start(self): raise RuntimeError('cannot start')

		old_thread = self.entry.Thread
		self.entry.Thread = FailingThread
		self.addCleanup(setattr, self.entry, 'Thread', old_thread)
		self.entry.monotonic = Mock(return_value=50.0)
		identity = 'listing|movie|1'
		self.preview._start_focused_metadata_lookup(identity, 'movie', '1')

		self.assertEqual(self.preview.lookup_workers, {})
		self.preview._consume_lookup_results()
		self.assertEqual(self.preview.focused_metadata_retries, {identity: 52.0})

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
