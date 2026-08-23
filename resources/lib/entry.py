from threading import Thread
from datetime import datetime
from time import monotonic
from collections import OrderedDict
from queue import Empty, SimpleQueue
from modules import kodi_utils
from modules.prefetch import NextPagePrefetch

logger = kodi_utils.logger
monitor = kodi_utils.monitor
get_property, set_property, clear_property = kodi_utils.get_property, kodi_utils.set_property, kodi_utils.clear_property
get_setting, set_setting = kodi_utils.get_setting, kodi_utils.set_setting

TRAILER_PREVIEW_PROPERTY = 'BingieTrailerPreview'
TRAILER_PREVIEW_READY_PROPERTY = 'BingieTrailerPreviewReady'
TRAILER_PREVIEW_CANCEL_PROPERTY = 'BingieTrailerPreviewCancel'
TRAILER_PREVIEW_REQUEST_PROPERTY = 'BingieTrailerPreviewRequest'
TRAILER_RESOLVED_PROPERTY = 'BingieTrailerResolved'
TRAILER_PREVIEW_DELAY = 1.0
TRAILER_PREVIEW_STOP_TIMEOUT = 10.0
TRAILER_PREVIEW_CLOSE_TIMEOUT = 2.0
TRAILER_PREVIEW_ACTIVE_POLL = 0.25
TRAILER_PREVIEW_IDLE_POLL = 1.0
FOCUSED_FANART_STABLE_POLL = 0.5
MAIN_MENU_WINDOWS = ('Home', '1110', '1111', '1112', '1119')
MAIN_MENU_FOCUS = '[%s] + [ControlGroup(9001).HasFocus() | Control.HasFocus(4444)]' % ' | '.join('Window.IsActive(%s)' % window for window in MAIN_MENU_WINDOWS)
TRAILER_PREVIEW_CACHE_LIMIT = 128
TRAILER_LOOKUP_WORKERS = 2
TRAILER_PREPARE_WORKERS = 2
TRAILER_PREPARED_CACHE_LIMIT = 32
TRAILER_PREPARED_CACHE_TTL = 120.0
TRAILER_PREVIEW_WINDOWS = ('Home', 'Videos', '1110', '1111', '1112', '1122', '1123', 'DialogVideoInfo.xml')
TRAILER_PREVIEW_WINDOW_VISIBILITY = ' | '.join('Window.IsActive(%s)' % window for window in TRAILER_PREVIEW_WINDOWS)
TRAILER_PREVIEW_INFO_CONTEXTS = 'Window.IsActive(1123) | Window.IsActive(DialogVideoInfo.xml)'
TRAILER_PREVIEW_INFO_CARD_FOCUS = 'Control.HasFocus(563) | Control.HasFocus(560)'
TRAILER_PREVIEW_INFO_CAST_FOCUS = 'Control.HasFocus(550)'
TRAILER_PREVIEW_ACTOR_CONTEXT = 'Window.IsActive(1122) + ![%s]' % TRAILER_PREVIEW_INFO_CONTEXTS
TRAILER_PREVIEW_NON_LISTING_CONTEXTS = TRAILER_PREVIEW_INFO_CONTEXTS + ' | Window.IsActive(1122)'
TRAILER_PREVIEW_SPECIAL_CONTEXTS = 'Window.IsActive(1123) | Window.IsActive(DialogVideoInfo.xml) | Window.IsActive(1122) | Window.IsActive(Videos)'
FOCUSED_FANART_PROPERTY = 'PovFocusedFanart'
FOCUSED_FANART_IDENTITY_PROPERTY = 'PovFocusedFanartIdentity'
FOCUSED_FANART_SETTLE_DELAY = 0.25
FOCUSED_FANART_WINDOWS = ('Home', 'Videos', '1110', '1111', '1112', '1118')
FOCUSED_FANART_WINDOW_VISIBILITY = ' | '.join('Window.IsActive(%s)' % window for window in FOCUSED_FANART_WINDOWS)
FOCUSED_FANART_VIDEO_FOCUS = 'Control.HasFocus(50) | Control.HasFocus(523) | Control.HasFocus(525) | Control.HasFocus(5250) | Control.HasFocus(527) | Control.HasFocus(5027)'
FOCUSED_METADATA_DELAY = 0.25
FOCUSED_METADATA_RETRY_DELAY = 2.0
FOCUSED_METADATA_IDENTITY_PROPERTY = 'PovFocusedMetaIdentity'
FOCUSED_METADATA_FIELDS = (
	('PovFocusedMainActors', 'main_actors'), ('PovFocusedGenre', 'genre'), ('PovFocusedClearLogo', 'clearlogo'), ('PovFocusedMpaa', 'mpaa'),
	('PovFocusedDuration', 'duration'), ('PovFocusedSeasons', 'seasons'), ('PovFocusedYearRange', 'year_range')
)
DATABASE_MAINTENANCE_DELAY = 60.0
DATABASE_MAINTENANCE_RETRY = 5.0
DATABASE_MAINTENANCE_IDLE_SECONDS = 10
STREAMING_CACHE_RETRY = 2.0

class FocusedFanart:
	def __init__(self):
		self.identity = ''
		self.focused_at = 0.0
		self.published_identity = ''
		self.stable = False
		self._clear(force=True)

	def close(self):
		self._reset()

	def pause(self):
		self._reset()

	def tick(self):
		self.stable = False
		if not kodi_utils.get_visibility(FOCUSED_FANART_WINDOW_VISIBILITY):
			self._reset()
			return False
		if kodi_utils.get_visibility('Window.IsActive(Videos)'):
			content_focused = kodi_utils.get_visibility(FOCUSED_FANART_VIDEO_FOCUS)
		else: content_focused = kodi_utils.get_visibility('ControlGroup(77777).HasFocus()')
		if not content_focused: return False
		identity = self._focused_identity()
		if not identity: return False
		now = monotonic()
		if identity != self.identity:
			self.identity = identity
			self.focused_at = now
			return True
		if self.published_identity != identity and now - self.focused_at >= FOCUSED_FANART_SETTLE_DELAY:
			fanart = self._focused_art()
			confirmed_identity = self._focused_identity()
			if confirmed_identity != identity:
				self.identity = confirmed_identity
				self.focused_at = monotonic()
				return True
			set_property(FOCUSED_FANART_PROPERTY, fanart)
			set_property(FOCUSED_FANART_IDENTITY_PROPERTY, identity)
			self.published_identity = identity
		self.stable = self.published_identity == identity
		return not self.stable

	def _focused_identity(self):
		identity = self._item_label('Property(PovFocusIdentity)')
		if identity: return identity
		media_type = self._item_label('DBType').lower() or self._item_label('Property(DBTYPE)').lower() or self._item_label('Property(mediatype)').lower()
		if media_type in ('category', 'categorie', 'genre'): return ''
		item_id = (
			self._item_label('UniqueID(tmdb)') or self._item_label('DBID') or self._item_label('FileNameAndPath') or
			self._item_label('Path') or self._item_label('Label')
		)
		return '|'.join(('focused', media_type, item_id)) if item_id else ''

	def _focused_art(self):
		return self._item_label('Art(fanart)') or self._item_label('Art(season.fanart)') or self._item_label('Art(tvshow.fanart)')

	def _item_label(self, label):
		return kodi_utils.get_infolabel('Container.ListItem.%s' % label).strip() or kodi_utils.get_infolabel('ListItem.%s' % label).strip()

	def _reset(self):
		self.identity = ''
		self.focused_at = 0.0
		self.stable = False
		self._clear()

	def _clear(self, force=False):
		if not force and not self.published_identity: return
		clear_property(FOCUSED_FANART_IDENTITY_PROPERTY)
		clear_property(FOCUSED_FANART_PROPERTY)
		self.published_identity = ''

def _service_poll_interval(fanart_pending, fanart_stable, preview_active):
	if fanart_pending or preview_active: return TRAILER_PREVIEW_ACTIVE_POLL
	if fanart_stable: return FOCUSED_FANART_STABLE_POLL
	return TRAILER_PREVIEW_IDLE_POLL

def _wait_for_service_tick(monitor, poll_interval):
	if poll_interval != TRAILER_PREVIEW_IDLE_POLL or not kodi_utils.get_visibility(MAIN_MENU_FOCUS): return monitor.waitForAbort(poll_interval)
	remaining = poll_interval
	while remaining > 0.0:
		delay = min(TRAILER_PREVIEW_ACTIVE_POLL, remaining)
		if monitor.waitForAbort(delay): return True
		remaining -= delay
		if remaining and not kodi_utils.get_visibility(MAIN_MENU_FOCUS): return False
	return False

class TrailerPreviewPlayer(kodi_utils.xbmc_player):
	def __init__(self, owner, generation):
		kodi_utils.xbmc_player.__init__(self)
		self.owner = owner
		self.generation = generation

	def onAVStarted(self):
		self.owner._on_av_started(self.generation)

class TrailerPreview:
	def __init__(self):
		self.identity = ''
		self.focused_at = 0.0
		self.played = False
		self.active = False
		self.playback_started = False
		self.preview_ready = False
		self.preview_generation = 0
		self.av_started_generation = -1
		self.preview_player = None
		self.launched_at = 0.0
		self.trailer = ''
		self.cancelled = False
		self.suppressed_identity = ''
		self.manual_identity = ''
		self.pending_stop_trailer = ''
		self.pending_stop_player = None
		self.pending_stop_deadline = 0.0
		self.pending_stop_requested = False
		self.fullscreen_exit_at = 0.0
		self.resolved_trailers = OrderedDict()
		self.resolved_focused_metadata = OrderedDict()
		self.focused_metadata_retries = OrderedDict()
		self.lookup_workers = {}
		self.lookup_results = SimpleQueue()
		self.lookup_pending = None
		self.prepare_workers = {}
		self.prepare_results = SimpleQueue()
		self.prepare_pending = None
		self.prepared_trailers = OrderedDict()
		self.prepare_generation = 0
		self.prepare_identity = ''
		self.prepare_trailer = ''
		self.closed = False
		self.focused_metadata_published = False
		self.manifest_server = None
		clear_property(TRAILER_PREVIEW_PROPERTY)
		clear_property(TRAILER_PREVIEW_READY_PROPERTY)
		clear_property(TRAILER_PREVIEW_CANCEL_PROPERTY)
		clear_property(TRAILER_PREVIEW_REQUEST_PROPERTY)
		clear_property(TRAILER_RESOLVED_PROPERTY)
		clear_property('PovInfoTransition')
		self._clear_focused_metadata(force=True)
		try:
			from modules.trailers import start_manifest_server
			self.manifest_server = start_manifest_server()
		except Exception as exc: logger('BINGIE trailer manifest service', str(exc))

	def close(self):
		self.closed = True
		self.lookup_pending = None
		self._discard_lookup_results()
		self._invalidate_preparation()
		self.manual_identity = ''
		self._clear_focused_metadata()
		clear_property(TRAILER_PREVIEW_REQUEST_PROPERTY)
		if self.active: self._begin_preview_stop(monotonic())
		close_deadline = monotonic() + TRAILER_PREVIEW_CLOSE_TIMEOUT
		while self.pending_stop_trailer and monotonic() < close_deadline:
			self._stop_pending_preview(monotonic())
			if self.pending_stop_trailer: kodi_utils.sleep(50)
		self._clear_pending_stop()
		from modules.trailers import stop_manifest_server
		stop_manifest_server(self.manifest_server)

	def pause(self):
		self.lookup_pending = None
		self._invalidate_preparation()
		self._clear_focused_metadata()
		self._stop_pending_preview(monotonic())
		if not self.active: return bool(self.pending_stop_trailer)
		self.cancelled = True
		self._stop_preview()
		return self.active or bool(self.pending_stop_trailer)

	def tick(self):
		now = monotonic()
		self._consume_lookup_results()
		cancel_request = get_property(TRAILER_PREVIEW_CANCEL_PROPERTY)
		if cancel_request:
			clear_property(TRAILER_PREVIEW_CANCEL_PROPERTY)
			self.manual_identity = ''
			if cancel_request != 'true':
				self.suppressed_identity = cancel_request
				if self.identity == cancel_request: self.played = True
			if self.active: self._begin_preview_stop(now)
			self._invalidate_preparation()
			self._stop_pending_preview(now)
			return self.active or bool(self.pending_stop_trailer) or self._preparing()
		self._stop_pending_preview(now)
		if self._restore_preview_window(now): return True
		self._consume_prepare_results()
		candidate = self._candidate()
		manual_required = bool(candidate and candidate[4])
		if get_property(TRAILER_PREVIEW_REQUEST_PROPERTY):
			clear_property(TRAILER_PREVIEW_REQUEST_PROPERTY)
			if manual_required and (not self.active or candidate[0] != self.identity):
				self.manual_identity = candidate[0]
				self.played = False
		manual_requested = bool(manual_required and self.manual_identity == candidate[0])
		if self.pending_stop_trailer:
			if not candidate:
				self.manual_identity = ''
				self._track_candidate(None, now)
			elif candidate[0] != self.identity: self._track_candidate(candidate, now)
			return True
		if self.active:
			if self.cancelled or not self._preview_window_active():
				self._stop_preview()
				self._track_candidate(candidate, now)
				return self.active or bool(candidate) or bool(self.pending_stop_trailer)
			if self._preview_navigation_away() or not candidate or candidate[0] != self.identity:
				self._begin_preview_stop(now)
				self._stop_pending_preview(now)
				self._track_candidate(candidate, now)
				return bool(candidate) or bool(self.pending_stop_trailer)
			if kodi_utils.get_visibility('Player.HasVideo'):
				owns_preview = self._owns_preview()
				if owns_preview is None and now - self.launched_at < 10.0: return True
				if not owns_preview:
					self._finish_preview()
					return False
				self.playback_started = True
				self._publish_preview_ready()
			elif not self.playback_started and now - self.launched_at < 10.0: return True
			else:
				if kodi_utils.get_visibility('Player.HasMedia'):
					if self._owns_preview() is not False: kodi_utils.execute_builtin('PlayerControl(Stop)')
				elif not self.playback_started: kodi_utils.execute_builtin('PlayerControl(Stop)')
				self._finish_preview()
				return False
			return True
		if not candidate:
			self.manual_identity = ''
			self._track_candidate(None, now)
			return bool(self.pending_stop_trailer) or self._preparing()
		if candidate[0] != self.identity:
			self._track_candidate(candidate, now)
			if candidate[5] and candidate[0] in self.resolved_focused_metadata:
				self._ensure_focused_metadata(candidate[0], candidate[2], candidate[3])
			if not manual_requested: return True
		if candidate[5] and now - self.focused_at >= FOCUSED_METADATA_DELAY:
			self._ensure_focused_metadata(candidate[0], candidate[2], candidate[3])
		if manual_required and not manual_requested: return bool(self.pending_stop_trailer) or self._preparing()
		if self.played: return bool(self.pending_stop_trailer) or self._preparing()
		if kodi_utils.get_visibility('Player.HasMedia'):
			if manual_required and not self.pending_stop_trailer: self.manual_identity = ''
			return bool(self.pending_stop_trailer) or self._preparing()
		if not manual_required and now - self.focused_at < TRAILER_PREVIEW_DELAY: return True
		trailer = candidate[1]
		if not trailer:
			found, trailer = self._cached_trailer(candidate[0])
			if not found:
				self._start_trailer_lookup(candidate[0], candidate[2], candidate[3])
				return True
			if not trailer:
				self.played = True
				self.manual_identity = ''
				return False
		self._start_preview_preparation(candidate[0], trailer)
		return True

	def _candidate(self):
		context = self._preview_context()
		if not context: return None
		if context == 'info':
			media_type = get_property('PovInfoType').strip().lower()
			if media_type not in ('movie', 'tvshow'): return None
			item_id = get_property('PovInfoTmdb').strip() or get_property('PovInfoPendingTmdb').strip()
			if not item_id: return None
			trailer = get_property('PovInfoTrailer').strip()
			identity = '|'.join(('info', media_type, item_id))
			return identity, trailer, media_type, item_id, True, False
		if context == 'info_card':
			media_type = self._item_label('Property(DBTYPE)').lower()
			if media_type not in ('movie', 'tvshow'): return None
			item_id = self._item_label('Property(tmdb_id)')
			if not item_id: return None
			label = self._item_label('Label')
			if not label: return None
			trailer = self._item_label('Property(trailer)')
			identity = '|'.join(('info-card', media_type, item_id))
			return identity, trailer, media_type, item_id, False, True
		if context == 'dialog':
			media_type = kodi_utils.get_infolabel('Window.Property(PovInfoType)').strip().lower()
			if media_type not in ('movie', 'tvshow'): return None
			item_id = kodi_utils.get_infolabel('Window.Property(PovInfoTmdb)').strip()
			if not item_id: return None
			trailer = kodi_utils.get_infolabel('ListItem.Trailer').strip()
			identity = '|'.join(('info', media_type, item_id))
			return identity, trailer, media_type, item_id, False, False
		if context == 'actor':
			media_type = self._item_label('Property(PovCreditType)').lower() or self._item_label('Property(mediatype)').lower() or self._item_label('DBType').lower()
			if media_type not in ('movie', 'tvshow'): return None
			item_id = self._item_label('UniqueID(tmdb)') or self._item_label('Property(tmdb_id)')
			if not item_id: return None
			label = self._item_label('Label')
			if not label: return None
			identity = '|'.join(('actor', media_type, item_id))
			return identity, '', media_type, item_id, False, False
		media_type = self._item_label('DBType').lower()
		if media_type not in ('movie', 'tvshow'): return None
		trailer = self._item_label('Trailer')
		is_summary = self._item_label('Property(PovLiteSummary)').lower() == 'true'
		if not trailer and not is_summary: return None
		label = self._item_label('Label')
		if not label or label.lower().startswith('next page'): return None
		item_id = self._item_label('UniqueID(tmdb)') or self._item_label('FileNameAndPath')
		if is_summary and not item_id: return None
		identity = self._item_label('Property(PovFocusIdentity)') if is_summary else ''
		if not identity: identity = '|'.join(('listing', media_type, item_id)) if is_summary else '|'.join((media_type, item_id or label, trailer))
		return identity, trailer, media_type, item_id, False, is_summary

	def _preview_context(self):
		if not self._preview_window_active() or get_property('PovInfoTransition'): return ''
		if not kodi_utils.get_visibility(TRAILER_PREVIEW_SPECIAL_CONTEXTS):
			if not kodi_utils.get_visibility('ControlGroup(77777).HasFocus()'): return ''
			if not kodi_utils.get_visibility(TRAILER_PREVIEW_SPECIAL_CONTEXTS): return 'listing'
		for _ in range(2):
			if kodi_utils.get_visibility('Window.IsActive(1123)'):
				if kodi_utils.get_visibility(TRAILER_PREVIEW_INFO_CARD_FOCUS): return 'info_card'
				if kodi_utils.get_visibility(TRAILER_PREVIEW_INFO_CAST_FOCUS): return ''
				return 'info'
			if kodi_utils.get_visibility('Window.IsActive(DialogVideoInfo.xml)'): return 'dialog'
			if kodi_utils.get_visibility('Window.IsActive(1122)'):
				if not kodi_utils.get_visibility('Control.HasFocus(610) | Control.HasFocus(620) | Control.HasFocus(630)'): return ''
				if kodi_utils.get_visibility(TRAILER_PREVIEW_ACTOR_CONTEXT): return 'actor'
				continue
			if kodi_utils.get_visibility('Window.IsActive(Videos)'):
				if not kodi_utils.get_visibility('Control.HasFocus(523)'): return ''
				if kodi_utils.get_visibility(TRAILER_PREVIEW_NON_LISTING_CONTEXTS): continue
				return 'listing'
			return 'listing' if kodi_utils.get_visibility('ControlGroup(77777).HasFocus()') else ''
		return ''

	def _preview_navigation_away(self):
		if kodi_utils.get_visibility('Window.IsActive(DialogVideoInfo.xml) | Window.IsActive(1123)'): return False
		if kodi_utils.get_visibility('Window.IsActive(1122)'):
			return False
		if kodi_utils.get_visibility('Window.IsActive(Videos)'):
			return kodi_utils.get_visibility('ControlGroup(9000).HasFocus() | Control.HasFocus(9000)')
		return kodi_utils.get_visibility('ControlGroup(9001).HasFocus() | Control.HasFocus(900) | Control.HasFocus(4444)')

	def _preview_window_active(self):
		if not kodi_utils.get_visibility(TRAILER_PREVIEW_WINDOW_VISIBILITY): return False
		if kodi_utils.xbmc.getSkinDir() != 'skin.titan.bingie.lite': return False
		return not kodi_utils.get_visibility('Window.IsActive(VideoOSD)')

	def _item_label(self, label):
		return kodi_utils.get_infolabel('Container.ListItem.%s' % label).strip() or kodi_utils.get_infolabel('ListItem.%s' % label).strip()

	def _track_candidate(self, candidate, now):
		identity = candidate[0] if candidate else ''
		if self.lookup_pending and self.lookup_pending[0] != identity: self.lookup_pending = None
		if identity != self.identity:
			self._clear_focused_metadata()
			if identity != self.prepare_identity: self._invalidate_preparation()
		if self.suppressed_identity and identity != self.suppressed_identity: self.suppressed_identity = ''
		if self.manual_identity and identity != self.manual_identity: self.manual_identity = ''
		self.identity = identity
		self.focused_at = now
		self.played = bool(identity and identity == self.suppressed_identity)

	def _start_trailer_lookup(self, identity, media_type, tmdb_id):
		self._schedule_lookup(identity, media_type, tmdb_id, False)

	def _start_focused_metadata_lookup(self, identity, media_type, tmdb_id):
		self._schedule_lookup(identity, media_type, tmdb_id, True)

	def _schedule_lookup(self, identity, media_type, tmdb_id, focused):
		if self.closed: return
		key = identity, focused
		pending = self.lookup_pending
		if key in self.lookup_workers:
			if not pending or focused or pending[0] != identity or not pending[3]: self.lookup_pending = None
			return
		if not focused and (identity, True) in self.lookup_workers:
			if not pending or pending[0] != identity or not pending[3]: self.lookup_pending = None
			return
		if pending and (pending[0], pending[3]) == key:
			self._start_pending_lookup()
			return
		if not focused and pending and pending[0] == identity and pending[3]: return
		job = identity, media_type, tmdb_id, focused
		if focused and pending and pending[0] == identity and not pending[3]: self.lookup_pending = job
		elif len(self.lookup_workers) >= TRAILER_LOOKUP_WORKERS:
			self.lookup_pending = job
			return
		else: self.lookup_pending = job
		self._start_pending_lookup()

	def _start_pending_lookup(self):
		if not self.lookup_pending or self.closed or len(self.lookup_workers) >= TRAILER_LOOKUP_WORKERS: return
		identity, media_type, tmdb_id, focused = self.lookup_pending
		key = identity, focused
		if key in self.lookup_workers:
			self.lookup_pending = None
			return
		if not focused and (identity, True) in self.lookup_workers:
			self.lookup_pending = None
			return
		worker = Thread(target=self._lookup_media, args=(identity, media_type, tmdb_id, focused), name='BINGIE focused metadata lookup' if focused else 'BINGIE trailer lookup', daemon=True)
		self.lookup_workers[key] = worker
		self.lookup_pending = None
		try: worker.start()
		except Exception as exc:
			self.lookup_workers.pop(key, None)
			logger('BINGIE Lite focused metadata lookup' if focused else 'BINGIE Lite trailer lookup', str(exc))
			self.lookup_results.put((identity, focused, None))

	def _lookup_media(self, identity, media_type, tmdb_id, focused):
		result = None
		try:
			if focused:
				from modules.dialogs import get_focused_media_info
				result = get_focused_media_info(media_type, tmdb_id)
			else:
				from indexers.tmdb_api import tmdb_media_videos
				from indexers.metadata import select_trailer
				videos = tmdb_media_videos(media_type, tmdb_id)
				if videos is not None: result = select_trailer(videos.get('results')) or ''
		except Exception as exc: logger('BINGIE Lite focused metadata lookup' if focused else 'BINGIE Lite trailer lookup', str(exc))
		if not self.closed: self.lookup_results.put((identity, focused, result))

	def _consume_lookup_results(self):
		if self.closed:
			self._discard_lookup_results()
			return
		while True:
			try: identity, focused, result = self.lookup_results.get_nowait()
			except Empty: break
			self.lookup_workers.pop((identity, focused), None)
			self._consume_lookup_result(identity, focused, result)

	def _discard_lookup_results(self):
		while True:
			try: identity, focused, _ = self.lookup_results.get_nowait()
			except Empty: break
			self.lookup_workers.pop((identity, focused), None)

	def _consume_lookup_result(self, identity, focused, result):
		if focused:
			if result is None:
				self.focused_metadata_retries.pop(identity, None)
				self.focused_metadata_retries[identity] = monotonic() + FOCUSED_METADATA_RETRY_DELAY
				if len(self.focused_metadata_retries) > TRAILER_PREVIEW_CACHE_LIMIT: self.focused_metadata_retries.popitem(last=False)
				return
			metadata = result
			self.focused_metadata_retries.pop(identity, None)
			self.resolved_focused_metadata.pop(identity, None)
			self.resolved_focused_metadata[identity] = metadata
			if len(self.resolved_focused_metadata) > TRAILER_PREVIEW_CACHE_LIMIT: self.resolved_focused_metadata.popitem(last=False)
			self._cache_trailer(identity, metadata.get('trailer') or '')
			if self.identity == identity: self._publish_focused_metadata(identity, metadata)
			return
		trailer = result
		if trailer is None:
			if self.identity == identity: self.played = True
			if self.manual_identity == identity: self.manual_identity = ''
			return
		self._cache_trailer(identity, trailer)

	def _cache_trailer(self, identity, trailer):
		self.resolved_trailers.pop(identity, None)
		self.resolved_trailers[identity] = trailer
		if len(self.resolved_trailers) > TRAILER_PREVIEW_CACHE_LIMIT: self.resolved_trailers.popitem(last=False)

	def _ensure_focused_metadata(self, identity, media_type, tmdb_id):
		if get_property(FOCUSED_METADATA_IDENTITY_PROPERTY) == identity: return
		if monotonic() < self.focused_metadata_retries.get(identity, 0.0): return
		self.focused_metadata_retries.pop(identity, None)
		try: metadata = self.resolved_focused_metadata.pop(identity)
		except KeyError:
			self._start_focused_metadata_lookup(identity, media_type, tmdb_id)
			return
		self.resolved_focused_metadata[identity] = metadata
		self._publish_focused_metadata(identity, metadata)

	def _publish_focused_metadata(self, identity, metadata):
		if self.identity != identity: return
		clear_property(FOCUSED_METADATA_IDENTITY_PROPERTY)
		for prop, key in FOCUSED_METADATA_FIELDS:
			value = metadata.get(key) or ''
			set_property(prop, str(value)) if value else clear_property(prop)
		set_property(FOCUSED_METADATA_IDENTITY_PROPERTY, identity)
		self.focused_metadata_published = True

	def _clear_focused_metadata(self, force=False):
		if not force and not self.focused_metadata_published: return
		clear_property(FOCUSED_METADATA_IDENTITY_PROPERTY)
		for prop, _ in FOCUSED_METADATA_FIELDS: clear_property(prop)
		self.focused_metadata_published = False

	def _cached_trailer(self, identity):
		try: trailer = self.resolved_trailers.pop(identity)
		except KeyError: return False, ''
		self.resolved_trailers[identity] = trailer
		return True, trailer

	def _preparing(self):
		return bool(self.prepare_pending or self.prepare_workers)

	def _invalidate_preparation(self):
		self.prepare_generation += 1
		self.prepare_identity = ''
		self.prepare_trailer = ''
		self.prepare_pending = None

	def _start_preview_preparation(self, identity, trailer):
		if identity == self.prepare_identity and trailer == self.prepare_trailer:
			self._start_pending_preparation()
			return
		self.prepare_generation += 1
		generation = self.prepare_generation
		self.prepare_identity = identity
		self.prepare_trailer = trailer
		self.prepare_pending = generation, identity, trailer
		clear_property(TRAILER_RESOLVED_PROPERTY)
		self._start_pending_preparation()

	def _start_pending_preparation(self):
		if not self.prepare_pending or self.closed: return
		generation, identity, trailer = self.prepare_pending
		if generation != self.prepare_generation or identity != self.prepare_identity or trailer != self.prepare_trailer:
			self.prepare_pending = None
			return
		cached = self._cached_prepared_trailer(trailer)
		if cached is not None:
			prepared, prepared_at = cached
			self.prepare_pending = None
			self.prepare_workers[generation] = None, identity, trailer
			self.prepare_results.put((generation, identity, trailer, prepared, None, prepared_at))
			return
		if any(job[2] == trailer for job in self.prepare_workers.values()): return
		if sum(1 for worker, _, _ in self.prepare_workers.values() if worker is not None and worker.is_alive()) >= TRAILER_PREPARE_WORKERS: return
		worker = Thread(target=self._prepare_preview, args=(generation, identity, trailer), name='BINGIE trailer preparation', daemon=True)
		self.prepare_workers[generation] = worker, identity, trailer
		self.prepare_pending = None
		try: worker.start()
		except Exception as exc:
			self.prepare_workers[generation] = None, identity, trailer
			self.prepare_results.put((generation, identity, trailer, None, exc, monotonic()))

	def _prepare_preview(self, generation, identity, trailer):
		prepared, error = None, None
		try:
			from modules.trailers import prepare_data_isolated
			prepared = prepare_data_isolated(trailer)
		except Exception as exc:
			error = exc
		if not self.closed: self.prepare_results.put((generation, identity, trailer, prepared, error, monotonic()))

	def _consume_prepare_results(self):
		current_result = None
		while True:
			try: result = self.prepare_results.get_nowait()
			except Empty: break
			generation, identity, trailer, prepared, error, prepared_at = result
			self.prepare_workers.pop(generation, None)
			prepared_valid = error is not None or self._cache_prepared_trailer(trailer, prepared, prepared_at)
			if generation == self.prepare_generation and identity == self.prepare_identity and trailer == self.prepare_trailer:
				if prepared_valid: current_result = result
				else: self.prepare_pending = generation, identity, trailer
		self._start_pending_preparation()
		if current_result is None: return
		generation, identity, trailer, prepared, error, _ = current_result
		self.prepare_identity = ''
		self.prepare_trailer = ''
		candidate = self._candidate()
		if not self._candidate_matches_preparation(candidate, identity, trailer) or self.active or self.played or self.pending_stop_trailer or kodi_utils.get_visibility('Player.HasMedia'): return
		if candidate[4] and self.manual_identity != identity: return
		if error is not None:
			clear_property(TRAILER_RESOLVED_PROPERTY)
			logger('BINGIE trailer playback', str(error))
			kodi_utils.notification('Trailer unavailable', 2500)
			self.manual_identity = ''
			self.played = True
			return
		try:
			from modules.trailers import commit_prepared
			playback_url, listitem = commit_prepared(prepared)
		except Exception as exc:
			clear_property(TRAILER_RESOLVED_PROPERTY)
			logger('BINGIE trailer playback', str(exc))
			kodi_utils.notification('Trailer unavailable', 2500)
			self.manual_identity = ''
			self.played = True
			return
		candidate = self._candidate()
		if not self._candidate_matches_preparation(candidate, identity, trailer) or self.active or self.played or self.pending_stop_trailer or kodi_utils.get_visibility('Player.HasMedia'):
			clear_property(TRAILER_RESOLVED_PROPERTY)
			return
		if candidate[4] and self.manual_identity != identity:
			clear_property(TRAILER_RESOLVED_PROPERTY)
			return
		self._launch_preview(playback_url, listitem)

	def _candidate_matches_preparation(self, candidate, identity, trailer):
		if not candidate or candidate[0] != identity: return False
		return (candidate[1] or self.resolved_trailers.get(identity, '')) == trailer

	def _cache_prepared_trailer(self, trailer, prepared, prepared_at):
		if not prepared or len(prepared) < 2 or not prepared[1]: return True
		if monotonic() - prepared_at >= TRAILER_PREPARED_CACHE_TTL: return False
		self.prepared_trailers.pop(trailer, None)
		self.prepared_trailers[trailer] = prepared_at, prepared
		if len(self.prepared_trailers) > TRAILER_PREPARED_CACHE_LIMIT: self.prepared_trailers.popitem(last=False)
		return True

	def _cached_prepared_trailer(self, trailer):
		try: prepared_at, prepared = self.prepared_trailers.pop(trailer)
		except KeyError: return None
		if monotonic() - prepared_at >= TRAILER_PREPARED_CACHE_TTL: return None
		self.prepared_trailers[trailer] = prepared_at, prepared
		return prepared, prepared_at

	def _launch_preview(self, playback_url, listitem):
		self.preview_generation += 1
		generation = self.preview_generation
		self.av_started_generation = -1
		self.preview_ready = False
		clear_property(TRAILER_PREVIEW_READY_PROPERTY)
		self.manual_identity = ''
		self.active = True
		self.played = True
		self.playback_started = False
		self.launched_at = monotonic()
		self.trailer = playback_url
		self.cancelled = False
		self.fullscreen_exit_at = 0.0
		set_property(TRAILER_PREVIEW_PROPERTY, 'true')
		logger('BINGIE Lite', 'Starting BINGIE row trailer preview')
		self.preview_player = TrailerPreviewPlayer(self, generation)
		if listitem is None: self.preview_player.play(playback_url, windowed=True)
		else: self.preview_player.play(playback_url, listitem, windowed=True)

	def _on_av_started(self, generation):
		if self.active and generation == self.preview_generation: self.av_started_generation = generation

	def _publish_preview_ready(self):
		if self.preview_ready or self.av_started_generation != self.preview_generation: return
		self.preview_ready = True
		set_property(TRAILER_PREVIEW_READY_PROPERTY, 'true')
		logger('BINGIE Lite', 'BINGIE row trailer preview first frame ready')

	def _restore_preview_window(self, now):
		if not self.active or not kodi_utils.get_visibility('Window.IsActive(fullscreenvideo)'):
			self.fullscreen_exit_at = 0.0
			return False
		if now >= self.fullscreen_exit_at:
			logger('BINGIE Lite', 'Returning trailer playback to the embedded preview window')
			kodi_utils.execute_builtin('Action(FullScreen)')
			self.fullscreen_exit_at = now + 1.0
		return True

	def _begin_preview_stop(self, now):
		if not self.active: return
		trailer = self.trailer
		player = self.preview_player
		self._finish_preview(preserve_window=True)
		if not trailer:
			self._clear_pending_stop()
			return
		self.pending_stop_trailer = trailer
		self.pending_stop_player = player
		self.pending_stop_deadline = now + TRAILER_PREVIEW_STOP_TIMEOUT
		self.pending_stop_requested = False

	def _stop_pending_preview(self, now):
		trailer = self.pending_stop_trailer
		if not trailer: return False
		owns_preview = self._owns_preview(trailer)
		if owns_preview is False:
			self._clear_pending_stop()
			return False
		if owns_preview and not self.pending_stop_requested:
			logger('BINGIE Lite', 'Stopping BINGIE row trailer preview playback')
			kodi_utils.execute_builtin('PlayerControl(Stop)')
			self.pending_stop_requested = True
		if self.pending_stop_requested and not kodi_utils.get_visibility('Player.HasMedia'):
			self._clear_pending_stop()
			return False
		if now >= self.pending_stop_deadline:
			self._clear_pending_stop()
			return False
		return True

	def _clear_pending_stop(self):
		self.pending_stop_trailer = ''
		self.pending_stop_player = None
		self.pending_stop_deadline = 0.0
		self.pending_stop_requested = False
		clear_property(TRAILER_PREVIEW_PROPERTY)
		clear_property(TRAILER_PREVIEW_READY_PROPERTY)
		clear_property(TRAILER_RESOLVED_PROPERTY)

	def _stop_preview(self):
		if self.active and kodi_utils.get_visibility('Player.HasMedia'):
			owns_preview = self._owns_preview()
			if owns_preview is None and not self.playback_started and monotonic() - self.launched_at < 10.0:
				self.cancelled = True
				clear_property(TRAILER_PREVIEW_PROPERTY)
				clear_property(TRAILER_PREVIEW_READY_PROPERTY)
				return
			if owns_preview: kodi_utils.execute_builtin('PlayerControl(Stop)')
			self._finish_preview()
		elif self.active and not self.playback_started and monotonic() - self.launched_at < 10.0:
			self.cancelled = True
			kodi_utils.execute_builtin('PlayerControl(Stop)')
			clear_property(TRAILER_PREVIEW_PROPERTY)
			clear_property(TRAILER_PREVIEW_READY_PROPERTY)
		else:
			if self.active and not self.playback_started: kodi_utils.execute_builtin('PlayerControl(Stop)')
			self._finish_preview()

	def _owns_preview(self, trailer=None):
		playing_file = kodi_utils.get_infolabel('Player.FilenameAndPath').strip()
		if not playing_file: return None
		playing_url = playing_file.partition('?')[0]
		expected_file = trailer or self.trailer
		if expected_file:
			if playing_file == expected_file: return True
			if expected_file.startswith('http://127.0.0.1:') and playing_url == expected_file.partition('?')[0]: return True
		expected_file = get_property(TRAILER_RESOLVED_PROPERTY)
		if expected_file:
			if playing_file == expected_file: return True
			if expected_file.startswith('http://127.0.0.1:') and playing_url == expected_file.partition('?')[0]: return True
		return False

	def _finish_preview(self, preserve_window=False):
		if self.active: logger('BINGIE Lite', 'Stopping BINGIE row trailer preview')
		self.active = False
		self.playback_started = False
		self.preview_ready = False
		self.preview_generation += 1
		self.av_started_generation = -1
		self.preview_player = None
		self.trailer = ''
		self.cancelled = False
		self.fullscreen_exit_at = 0.0
		clear_property(TRAILER_PREVIEW_READY_PROPERTY)
		if not preserve_window:
			clear_property(TRAILER_PREVIEW_PROPERTY)
			clear_property(TRAILER_RESOLVED_PROPERTY)

class POVMonitor(kodi_utils.xbmc_monitor):
	def __enter__(self):
		initializeDatabases()
		normalizeMenuData()
		self._install_keymap()
		try: viewsSetWindowProperties()
		except: pass
		self.threads = [Thread(target=self._deferred_database_maintenance)]
		if self._streaming_cache_ready(): self._tune_streaming_cache()
		else: self.threads.append(Thread(target=self._deferred_streaming_cache_tune))
		self.threads = tuple(self.threads)
		self.focused_fanart = FocusedFanart()
		self.trailer_preview = TrailerPreview()
		self.next_page_prefetch = NextPagePrefetch()
		return self

	def __exit__(self, exc_type, exc_value, traceback):
		if hasattr(self, 'focused_fanart'): self.focused_fanart.close()
		if hasattr(self, 'trailer_preview'): self.trailer_preview.close()
		if hasattr(self, 'next_page_prefetch'): self.next_page_prefetch.cancel()
		for i in getattr(self, 'threads', ()): i.join()

	def run(self):
		with self:
			try: metadataCachePrefetch()
			except: pass
			for i in getattr(self, 'threads', ()): i.start()
			try: clearSubs()
			except: pass
			poll_interval = TRAILER_PREVIEW_IDLE_POLL
			while not _wait_for_service_tick(self, poll_interval):
				if get_property('pov_lite_pause_services'):
					self.next_page_prefetch.cancel()
					self.focused_fanart.pause()
					poll_interval = TRAILER_PREVIEW_ACTIVE_POLL if self.trailer_preview.pause() else TRAILER_PREVIEW_IDLE_POLL
					continue
				self.next_page_prefetch.tick()
				fanart_pending = self.focused_fanart.tick()
				preview_active = self.trailer_preview.tick()
				poll_interval = _service_poll_interval(fanart_pending, self.focused_fanart.stable, preview_active)

	def _deferred_database_maintenance(self):
		if self.waitForAbort(DATABASE_MAINTENANCE_DELAY): return
		while not self._database_maintenance_ready():
			if self.waitForAbort(DATABASE_MAINTENANCE_RETRY): return
		try: databaseMaintenance()
		except: pass

	def _deferred_streaming_cache_tune(self):
		while not self._streaming_cache_ready():
			if self.waitForAbort(STREAMING_CACHE_RETRY): return
		self._tune_streaming_cache()

	def _streaming_cache_ready(self):
		return not kodi_utils.get_visibility('Player.HasMedia') and not kodi_utils.get_visibility('Window.IsVisible(busydialog)') and not kodi_utils.get_visibility('Window.IsVisible(busydialognocancel)')

	def _tune_streaming_cache(self):
		try:
			from modules.streaming_cache import tune
			tune()
		except Exception as exc: logger('BINGIE streaming cache', str(exc))

	def _install_keymap(self):
		try:
			from modules.keymap import install
			if install(): logger('BINGIE keymap', 'Installed long-Play source selection keymap')
		except Exception as exc: logger('BINGIE keymap', str(exc))

	def _database_maintenance_ready(self):
		if kodi_utils.get_visibility('Container.IsUpdating'): return False
		if kodi_utils.get_visibility('Window.IsVisible(busydialog)'): return False
		if kodi_utils.get_visibility('Window.IsVisible(busydialognocancel)'): return False
		if kodi_utils.get_visibility('Player.HasMedia'): return False
		return kodi_utils.get_visibility('System.IdleTime(%d)' % DATABASE_MAINTENANCE_IDLE_SECONDS)

	def ver(*args):
		return f"{kodi_utils.get_addoninfo('id')}-{kodi_utils.get_addoninfo('version')}"

	def onScreensaverActivated(self):
		set_property('pov_lite_pause_services', 'true')

	def onScreensaverDeactivated(self):
		clear_property('pov_lite_pause_services')

	def onNotification(self, sender, method, data):
		if method == 'System.OnSleep': set_property('pov_lite_pause_services', 'true')
		elif method == 'System.OnWake': clear_property('pov_lite_pause_services')

def initializeDatabases():
	from modules.cache import check_databases
	logger('BINGIE Lite', 'InitializeDatabases Service Starting')
	check_databases()
	return logger('BINGIE Lite', 'InitializeDatabases Service Finished')

def normalizeMenuData():
	logger('BINGIE Lite', 'NormalizeMenuData Service Starting')
	from modules.cache import normalize_menu_data
	normalize_menu_data()
	return logger('BINGIE Lite', 'NormalizeMenuData Service Finished')

def metadataCachePrefetch():
	from caches.meta_cache import MetaCache
	MetaCache().prefetch()

def databaseMaintenance():
	current_time = int(datetime.now().timestamp())
	next_clean = current_time + 259200 # 3 days
	due_clean = int(get_setting('database.maintenance.due', '0'))
	if current_time < due_clean: return
	logger('BINGIE Lite', 'Database Maintenance Service Starting')
	from modules.cache import clean_databases
	clean_databases(current_time, database_check=False, silent=True)
	set_setting('database.maintenance.due', str(next_clean))
	return logger('BINGIE Lite', 'Database Maintenance Service Finished')

def viewsSetWindowProperties():
	logger('BINGIE Lite', 'ViewsSetWindowProperties Service Starting')
	kodi_utils.set_view_properties()
	return logger('BINGIE Lite', 'ViewsSetWindowProperties Service Finished')

def clearSubs():
	logger('BINGIE Lite', 'Clear Subtitles Service Starting')
	subtitle_path = 'special://temp/'
	for i in kodi_utils.list_dirs(subtitle_path)[1]:
		if i.startswith('POVLiteSubs_'):
			kodi_utils.delete_file(subtitle_path + i)
	return logger('BINGIE Lite', 'Clear Subtitles Service Finished')
