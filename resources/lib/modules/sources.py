import re
import json
import time
from copy import copy
from concurrent.futures import ThreadPoolExecutor as TPE
from threading import Event, Lock, Thread
from magneto import sources as magneto_sources
from windows import open_window, create_window
from caches.providers_cache import ExternalProvidersCache
from indexers.metadata import movie_meta, tvshow_meta, season_episodes_meta, get_title
from modules.debrid import debrid_enabled, debrid_type_enabled, Source, DebridCheck
from modules import player, kodi_utils, settings, source_utils
try: from modules import playback_health
except Exception: playback_health = None
from modules.source_search import CORE_CACHED_RESULT_TARGET, EXTERNAL_PROVIDERS, ProviderOutcome, external_worker_count, split_external_providers
from modules.utils import get_datetime, safe_string, string_to_float
#from modules.kodi_utils import logger

POVPlayer, progressDialogBG, notification = player.POVPlayer, kodi_utils.progressDialogBG, kodi_utils.notification
show_busy_dialog, hide_busy_dialog, close_all_dialog = kodi_utils.show_busy_dialog, kodi_utils.hide_busy_dialog, kodi_utils.close_all_dialog
get_property, clear_property = kodi_utils.get_property, kodi_utils.clear_property
ls, monitor, sleep, get_setting = kodi_utils.local_string, kodi_utils.monitor, kodi_utils.sleep, kodi_utils.get_setting
quality_filter = settings.quality_filter
pack_enable_check = source_utils.pack_enable_check
get_cache_expiry, get_file_info = source_utils.get_cache_expiry, source_utils.get_file_info
quality_ranks = {'4K': 1, '1080p': 2, '720p': 3, 'SD': 4, 'SCR': 5, 'CAM': 5, 'TELE': 5}
DEFERRED_LINK_TTL = 30.0
NEXT_EPISODE_PREP_TTL = 1800.0
_PLAYBACK_FAILED = object()
_RETRY_SEARCH = object()
av1_filter_key, hevc_filter_key, hdr_filter_key, dolby_vision_filter_key = '[B]AV1[/B]', '[B]HEVC[/B]', '[B]HDR[/B]', '[B]D/VISION[/B]'
ext_scr_format, format_line = '[COLOR %s][B]%s[/B][/COLOR]', '%s[CR]%s[CR]%s'
diag_format, resolutions, pack_display = '4K: %s | 1080p: %s | 720p: %s | SD: %s | Total: %s', '4K 1080p 720p SD total', '%s (%s)'
season_str, show_str, nores_str = ls(32537), ls(32089), ls(32760)
season_display, show_display = ls(32537), ls(32089)
pack_check = (season_display, show_display)

def log_search_timing(meta, stage, started, provider=None):
	try:
		fields = ['search=%s' % meta['_bingie_search_id'], 'stage=%s' % stage, 'seconds=%.3f' % (time.monotonic() - started)]
		if provider is not None: fields.append('provider=%s' % (provider if provider in (*EXTERNAL_PROVIDERS, 'realdebrid', 'alldebrid', 'torbox') else 'other'))
		kodi_utils.logger('BINGIE search timing', ' '.join(fields))
	except Exception: pass

def timed_search_stage(meta, stage, function, *args):
	started = time.monotonic()
	try: return function(*args)
	finally: log_search_timing(meta, stage, started)

class SourceResultSink:
	def __init__(self):
		self.items = []
		self.accepting = True
		self.lock = Lock()

	def extend(self, items):
		if not items: return False
		with self.lock:
			if not self.accepting: return False
			self.items.extend(items)
			return True

	def close(self):
		with self.lock: self.accepting = False

class Sources:
	nextep_params = []
	nextep_lock = Lock()
	nextep_prepared = None
	nextep_preparing_key = None
	nextep_generation = 0

	@staticmethod
	def _nextep_key(params):
		return json.dumps({key: value for key, value in params.items() if key != 'background'}, sort_keys=True, default=str)

	@classmethod
	def factory(cls, params):
		try: int(params['episode'])
		except: return cls().source_select(params)
		cls.nextep_callback(params)
		while True:
			queued = cls._pop_nextep()
			if queued is None: break
			try: cls().source_select(queued)
			except: pass

	@classmethod
	def nextep_callback(cls, params):
		if not isinstance(params, dict): return
		key = cls._nextep_key(params)
		with cls.nextep_lock:
			if cls.nextep_preparing_key != key and (not cls.nextep_prepared or cls.nextep_prepared['key'] != key):
				cls.nextep_generation += 1
				cls.nextep_preparing_key, cls.nextep_prepared = None, None
			cls.nextep_params[:] = [params]

	@classmethod
	def _pop_nextep(cls):
		with cls.nextep_lock: return cls.nextep_params.pop() if cls.nextep_params else None

	@classmethod
	def clear_nextep(cls):
		with cls.nextep_lock:
			cls.nextep_params.clear()
			cls.nextep_generation += 1
			cls.nextep_preparing_key, cls.nextep_prepared = None, None

	@classmethod
	def background_prep(cls, params):
		key = cls._nextep_key(params)
		with cls.nextep_lock:
			cls.nextep_generation += 1
			generation = cls.nextep_generation
			cls.nextep_preparing_key, cls.nextep_prepared = key, None
		self = cls()
		self.source_select({**params, 'background': 'true'})
		with cls.nextep_lock:
			valid = cls.nextep_generation == generation
			if valid:
				cls.nextep_preparing_key = None
				if self.pov_background_url and getattr(self, 'prepared_candidate', None):
					cls.nextep_prepared = {'key': key, 'created': time.monotonic(), 'results': self.prepared_candidate}
		return self.pov_background_url if valid else False

	@classmethod
	def _take_nextep_prepared(cls, params):
		key = cls._nextep_key(params)
		with cls.nextep_lock:
			prepared = cls.nextep_prepared
			cls.nextep_prepared = None
			if prepared and prepared['key'] == key and time.monotonic() - prepared['created'] <= NEXT_EPISODE_PREP_TTL: return prepared

	def __init__(self):
		self.params = {}
		self.filters_ignored = False
		self.progress_dialog, self.pov_background_url = None, None
		self.restart_from_beginning = False
		self.pending_search = None
		self.search_cancelled = False
		self.using_nextep_prepared = False
		self.source_sink = SourceResultSink()
		self.sources = self.source_sink.items
		self.exclude_list = ['library']# needs to be mutable so leave as list.
		self.config_loader = ConfigLoader()
		self.meta_builder = MetaBuilder()
		self.scraper_processor = ScraperProcessor(self)
		self.results_processor = ResultsProcessor(self)

	def source_select(self, params=None):
		try:
			while True:
				search_started = time.monotonic()
				self.config_loader.apply(self, params)
				if not hasattr(self, 'meta'): self.meta = self.meta_builder.get(self)
				self.meta['_bingie_search_id'] = time.monotonic_ns()
				self.meta['_bingie_search_started_at'] = search_started
				results = self.get_sources()
				if self.search_cancelled: return
				outcome = self._process_post_results() if not results and not getattr(self, 'unverified_results', []) else self.play_source(results)
				if outcome is not _RETRY_SEARCH: return outcome
				retry_state = None
				if getattr(self, 'using_nextep_prepared', False) and self.autoplay:
					params = {**self.params, 'force_full_search': 'true'}
					retry_state = (set(self.autoplay_attempted), self.autoplay_resume_percent, self.restart_from_beginning)
				else:
					params = {**self.params, 'force_full_search': 'true', 'autoplay': 'false'}
					params.pop('autoplay_next', None)
				self._cancel_pending_search()
				self.__init__()
				self.prepared_retry_state = retry_state
		finally: self._cancel_pending_search()

	def _cancel_pending_search(self):
		pending = getattr(self, 'pending_search', None)
		if pending: pending['cancelled'].set()

	def get_sources(self):
		start_time = time.monotonic()
		self.meta.pop('full_search_available', None)
		self.meta.pop('_bingie_pending_search', None)
		self.progress_dialog = DialogProgress(getattr(self, 'full_screen', False))
		prepared = None if self.background else self._take_nextep_prepared(self.params)
		if prepared:
			self.orig_results = [dict(item) for item in prepared['results']]
			results = self.results_processor.process(self.orig_results)
			if results:
				self.using_nextep_prepared = True
				self.meta.update({'scrape_time': time.monotonic() - start_time})
				return results
		self.orig_results = self.collect_results()
		results = self.results_processor.process(self.orig_results)
		self.meta.update({'scrape_time': time.monotonic() - start_time})
		return results

	def collect_results(self):
		self.scraper_processor.activate_external()
		if self.debrid_torrent_enabled and (self.external_providers or self.background):
			args = (self.external_providers, self.debrid_torrent_enabled, self.progress_dialog)
			if not self.background and not self.force_full_search and not self.ignore_scrape_filters:
				return self._collect_source_snapshot(args)
			eligibility_filter = None if self.ignore_scrape_filters else self.results_processor.filter_staged_results
			manager = ExternalManager(self.meta, *args, eligibility_filter=eligibility_filter, force_full_search=self.force_full_search)
			self.source_sink.extend(manager.results(self.meta['search_info']))
		self.source_sink.close()
		return self.sources

	def _collect_source_snapshot(self, args):
		meta = dict(self.meta)
		sink = self.source_sink
		search_source = copy(self)
		search_source.meta = meta
		ready, finished, cancelled = Event(), Event(), Event()
		pending = {'ready': ready, 'finished': finished, 'cancelled': cancelled, 'snapshot': None, 'meta': meta}
		self.pending_search = pending

		def publish(results):
			pending['snapshot'] = [dict(item) for item in results]
			ready.set()

		def collect():
			try:
				sink.extend(manager.results(meta['search_info']))
			except Exception: pass
			finally:
				sink.close()
				pending['finished_at'] = time.monotonic()
				finished.set()
				ready.set()

		manager = ExternalManager(meta, *args, eligibility_filter=ResultsProcessor(search_source).filter_staged_results, snapshot_callback=publish, cancel_event=cancelled)
		manager.background = True
		manager.manual_progress = True
		cancellable = manager.progress_dialog.full_screen
		display = kodi_utils.xbmcgui.DialogProgress() if cancellable else progressDialogBG
		hide_busy_dialog()
		display.create('BINGIE Lite', 'Searching for sources...')
		try:
			Thread(target=collect, daemon=True).start()
			while not ready.wait(0.1):
				if monitor.abortRequested() or (cancellable and display.iscanceled()):
					cancelled.set()
					self.search_cancelled = True
					return []
				progress = getattr(manager, 'manual_progress_state', None)
				if progress: manager.progress_update(*progress, display=display)
			if monitor.abortRequested() or (cancellable and display.iscanceled()):
				cancelled.set()
				self.search_cancelled = True
				return []
		finally: display.close()
		if pending['snapshot'] is not None and not finished.is_set():
			self.meta['full_search_available'] = True
			self.meta['_bingie_pending_search'] = True
			return pending['snapshot']
		self.pending_search = None
		self.meta['full_search_available'] = meta.get('full_search_available', False)
		return list(self.sources)

	def _process_post_results(self):
		if self.ignore_results_filter and self.orig_results:
			return self._process_ignore_filters()
		return self._no_results()

	def _process_ignore_filters(self):
		if self.autoplay: notification(32686)
		self.autoplay = False
		self.filters_ignored = True
		results = self.results_processor.sort_results(self.orig_results)
		self.unverified_results = [item for item in results if item.get('_identity_status') == 'unverified']
		results = [item for item in results if item.get('_identity_status') != 'unverified']
		if not results and not self.unverified_results: return self._no_results()
		return self.play_source(results)

	def _no_results(self):
		hide_busy_dialog()
		if self.background: return
		notification(nores_str)

	def display_results(self, results, unverified_view=False):
		verified_results = None if unverified_view else results
		while True:
			chosen_item = open_window(
				('windows.sources', 'SourceResults'),
				'sources_results.xml',
				results=results,
				meta=self.meta,
				scraper_settings=self.scraper_settings,
				filters_ignored=self.filters_ignored,
				full_search_available=self.meta.get('full_search_available', False) and not unverified_view,
				unverified_count=0 if unverified_view else len(getattr(self, 'unverified_results', [])),
				unverified_view=unverified_view
			)
			if (not chosen_item or chosen_item[0] is None) and unverified_view and verified_results:
				results, unverified_view = verified_results, False
				continue
			if not chosen_item: return self.progress_dialog.kill()
			action, chosen_item = chosen_item
			if action == 'play':
				outcome = self.play_file(results, chosen_item)
				if outcome is not _PLAYBACK_FAILED: return outcome
				if self.pending_search and self.pending_search['cancelled'].is_set():
					self.pending_search = None
					self.meta.pop('_bingie_pending_search', None)
					self.meta['full_search_available'] = True
				choice = kodi_utils.dialog.select('Playback failed', ['Search again', 'Choose a source or adjust filters'])
				if choice == 0: return _RETRY_SEARCH
				if choice == 1: continue
				return
			if action == 'show_unverified':
				results, unverified_view = self.unverified_results, True
				continue
			if action == 'perform_full_search':
				pending = self.pending_search
				if pending is None: return _RETRY_SEARCH
				wait_dialog = kodi_utils.xbmcgui.DialogProgress()
				wait_dialog.create('BINGIE Lite', 'Searching for remaining sources...')
				cancelled = False
				try:
					while not pending['finished'].wait(0.1):
						if monitor.abortRequested(): return
						if wait_dialog.iscanceled():
							cancelled = True
							break
					if wait_dialog.iscanceled(): cancelled = True
				finally: wait_dialog.close()
				if cancelled: continue
				self.pending_search = None
				self.meta.pop('_bingie_pending_search', None)
				self.meta['full_search_available'] = pending['meta'].get('full_search_available', False)
				self.meta['scrape_time'] = pending['finished_at'] - self.meta['_bingie_search_started_at']
				self.orig_results = list(self.sources)
				results = self.results_processor.process(self.orig_results)
				verified_results = results
				if not results and not getattr(self, 'unverified_results', []): return self._process_post_results()
				continue
			return

	def play_source(self, results):
		results = [item for item in results if item.get('_identity_status') != 'unverified']
		if self.background:
			self.pov_background_url = bool(results)
			if results: self.prepared_candidate = [dict(item) for item in self.orig_results]
		elif self.autoplay:
			retry_state = getattr(self, 'prepared_retry_state', None)
			self.prepared_retry_state = None
			if retry_state:
				self.autoplay_attempted, self.autoplay_resume_percent, self.restart_from_beginning = retry_state
				results = [item for item in results if self._autoplay_source_key(item) not in self.autoplay_attempted]
				if not results: return notification('Playback failed. Search again or adjust filters.', time=10000)
			else:
				self.autoplay_attempted = set()
				self.autoplay_resume_percent = 0
			outcome = self.play_file(results)
			pending = self.pending_search
			if outcome is _PLAYBACK_FAILED and not pending and getattr(self, 'using_nextep_prepared', False): return _RETRY_SEARCH
			if outcome is not _PLAYBACK_FAILED or not pending: return outcome
			display = kodi_utils.xbmcgui.DialogProgress() if self.progress_dialog.full_screen else progressDialogBG
			display.create('BINGIE Lite', 'Searching for remaining sources...')
			try:
				while not pending['finished'].wait(0.1):
					if monitor.abortRequested() or (self.progress_dialog.full_screen and display.iscanceled()): return
				if monitor.abortRequested() or (self.progress_dialog.full_screen and display.iscanceled()): return
			finally: display.close()
			self.pending_search = None
			self.orig_results = list(self.sources)
			results = self.results_processor.process(self.orig_results)
			results = [item for item in results if self._autoplay_source_key(item) not in self.autoplay_attempted]
			if results: return self.play_file(results)
			return notification('Playback failed. Search again or adjust filters.', time=10000)
		else: return self.display_results(results)

	@staticmethod
	def _autoplay_source_key(item):
		return tuple(item.get(key) for key in ('hash', 'debrid', 'source', 'provider', 'url', 'unrestricted_link', 'name'))

	@staticmethod
	def _log_playback_timing(timing, outcome):
		try:
			now = time.monotonic()
			fields = [
				'selection=%s' % timing['selection_id'], 'attempt=%s' % timing['attempt'], 'provider=%s' % timing['provider'], 'outcome=%s' % outcome,
				'selection_to_attempt=%.3f' % (timing['attempt_started'] - timing['selection_started']),
				'attempt_to_event=%.3f' % (now - timing['attempt_started']), 'selection_to_event=%.3f' % (now - timing['selection_started'])
			]
			for stage in ('transfer_file_discovery', 'unlock', 'resolve_total'):
				if stage in timing: fields.append('%s=%.3f' % (stage, timing[stage]))
			if 'kodi_play_started' in timing: fields.append('kodi_play_to_event=%.3f' % (now - timing['kodi_play_started']))
			kodi_utils.logger('BINGIE playback timing', ' '.join(fields))
		except Exception: pass

	def play_file(self, results, source=None):
		startup_owner = {}
		try:
			if not hasattr(self, 'autoplay_attempted'): self.autoplay_attempted = set()
			selection_started = self.meta.pop('_bingie_selection_started_at', None) or time.monotonic()
			selection_id = time.monotonic_ns()
			retry_resume_percent = getattr(self, 'autoplay_resume_percent', 0) if source is None else 0
			manual_selection = source is not None
			if source is not None and source.get('_identity_status') == 'unverified':
				source_index, items = None, [source]
			elif source is None:
				source_index, items = 0, [item for item in results if item.get('_identity_status') != 'unverified']
			elif source in results:
				source_index = results.index(source)
				items = results[source_index:] + results[:source_index]
				items = [i for i in items if 'Uncached' not in i.get('cache_provider', '') and i.get('_identity_status') != 'unverified']
			else: source_index, items = None, [source]
			if self.progress_dialog.full_screen:
				self.progress_dialog.make(self.meta)
				progress_media = self.progress_dialog.kill
			else:
				progressDialogBG.create('BINGIE Lite', 'BINGIE Lite loading...')
				progress_media = None
			original_total = len(items)
			attempted = False
			for count, item in enumerate(items, 1):
				link = None
				health_context = self._playback_health_context(item)
				if item.get('_bingie_host_deferred') and time.monotonic() - item.get('_bingie_resolved_at', 0) > DEFERRED_LINK_TTL:
					if item.get('_bingie_can_reresolve'): item.pop('unrestricted_link', None)
					else: continue
				try:
					if monitor.abortRequested(): break
					elif self.progress_dialog.full_screen and self.progress_dialog.iscanceled(): break
					if self.progress_dialog.full_screen and self.progress_dialog.dialog_progress is None: self.progress_dialog.make(self.meta)
					percent = int(((total_items := len(items))-count)/total_items*100)
					name = (item.get('display_name') or item['name']).upper()
					line1 = item.get('scrape_provider'), item.get('cache_provider'), item.get('provider')
					if source_index is not None: line1 = ('[B]%02d[/B]' % (results.index(item) + 1), *line1)
					line2 = item.get('size_label', ''), item.get('extraInfo', '')
					line1 = ' | '.join(i for i in line1 if i and i != 'external').upper()
					line2 = ' | '.join(i for i in line2 if i)
					if self.progress_dialog.full_screen:
						self.progress_dialog.update(format_line % (line1, line2, name), percent)
					else: progressDialogBG.update(percent, name)
				except: pass
				resolve_started = time.monotonic()
				provider = item.get('debrid', '')
				if provider not in ('realdebrid', 'alldebrid', 'torbox'): provider = 'other'
				timing = {'selection_started': selection_started, 'attempt_started': resolve_started, 'selection_id': selection_id, 'attempt': count, 'provider': provider, 'log': self._log_playback_timing}
				attempted = True
				if self.autoplay and not manual_selection: self.autoplay_attempted.add(self._autoplay_source_key(item))
				try:
					if 'unrestricted_link' in item: link = item['unrestricted_link']
					else: link = Source(item, self.meta, timing).resolve_sources()
				except: link = None
				resolve_latency = time.monotonic() - resolve_started
				timing['resolve_total'] = resolve_latency
				if link is None:
					self._log_playback_timing(timing, 'resolve_failed')
					self._record_playback_health(health_context, 'resolve_fail', latency=resolve_latency)
					continue
				health_context = self._playback_health_context(item, link) or health_context
				if not item.get('_bingie_resolve_recorded'): self._record_playback_health(health_context, 'resolve_ok', latency=resolve_latency)
				if not manual_selection and not item.get('_bingie_host_deferred') and count < original_total and self._host_health_penalty(health_context) >= 60:
					self._log_playback_timing(timing, 'deferred')
					deferred_item = item.copy()
					deferred_item.update({
						'unrestricted_link': link, '_bingie_host_deferred': True, '_bingie_resolve_recorded': True,
						'_bingie_can_reresolve': 'unrestricted_link' not in item, '_bingie_resolved_at': time.monotonic()
					})
					items.append(deferred_item)
					continue
				if not self.progress_dialog.full_screen: progressDialogBG.close()
				playback_meta = self.meta.copy()
				playback_meta.update({
					'release_name': item.get('name') or item.get('display_name') or '',
					'release_quality': item.get('quality') or '',
					'release_info': item.get('extraInfo') or '',
					'_playback_health_context': health_context,
					'_playback_health_started_at': time.monotonic(),
					'_playback_health_bitrate_mbps': self._source_bitrate_mbps(item),
					'_playback_health_allow_stall_recovery': self.autoplay and not manual_selection,
					'_playback_health_has_stall_fallback': count < len(items),
					'_autoplay_startup_fallback': self.autoplay and not manual_selection and any(
						candidate.get('_identity_status') != 'unverified' and 'Uncached' not in candidate.get('cache_provider', '')
						and (not candidate.get('_bingie_host_deferred') or candidate.get('_bingie_can_reresolve')) for candidate in items[count:]
					)
				})
				playback_meta['_playback_timing'] = timing
				pending = getattr(self, 'pending_search', None)
				if pending and manual_selection: playback_meta['_search_cancel_event'] = pending['cancelled']
				if retry_resume_percent: playback_meta['_retry_resume_percent'] = retry_resume_percent
				if getattr(self, 'restart_from_beginning', False): playback_meta['_restart_from_beginning'] = True
				playback_player = POVPlayer()
				if self.autoplay and not manual_selection:
					startup_owner.update({'player': playback_player, 'previous_url': startup_owner.get('url'), 'url': link})
					playback_player.startup_owner = startup_owner
				try: playback_result = playback_player.run(link, playback_meta, progress_media)
				except Exception as exc:
					try: kodi_utils.logger('Sources', 'Playback attempt failed: %s' % exc)
					except: pass
					playback_result = False
				if playback_result is False: self._log_playback_timing(timing, 'playback_failed_after_av' if timing.get('av_started') else 'playback_start_failed')
				elif not timing.get('av_started'):
					outcome = 'playback_started_unattributed' if getattr(playback_player, 'startup_playback_started', False) else 'cancelled'
					self._log_playback_timing(timing, outcome)
				retry_resume_percent = getattr(playback_player, 'retry_resume_percent', 0)
				if self.autoplay and not manual_selection: self.autoplay_resume_percent = retry_resume_percent
				self.restart_from_beginning = getattr(playback_player, 'restart_from_beginning', False)
				if playback_result is not False: return playback_result
			else:
				cancelled = monitor.abortRequested() or (self.progress_dialog.full_screen and self.progress_dialog.iscanceled())
				if self.progress_dialog.full_screen: self.progress_dialog.kill()
				else: progressDialogBG.close()
				if cancelled: return
				if self.autoplay and not manual_selection and (self.pending_search or getattr(self, 'using_nextep_prepared', False)): return _PLAYBACK_FAILED
				if not attempted: return self._no_results()
				if manual_selection: return _PLAYBACK_FAILED
				return notification('Playback failed. Search again or adjust filters.', time=10000)
			if self.progress_dialog.full_screen: self.progress_dialog.kill()
			else: progressDialogBG.close()
		except: pass
		finally: startup_owner['player'] = None

	@staticmethod
	def _playback_health_context(item, link=None):
		if playback_health is None: return None
		try: return playback_health.source_context(item, link)
		except: return None

	@staticmethod
	def _record_playback_health(context, event, **kwargs):
		if playback_health is None or not context: return
		try: playback_health.record(context, event, **kwargs)
		except: pass

	@staticmethod
	def _host_health_penalty(context):
		if playback_health is None or not context: return 0
		try: return playback_health.host_penalty(context)
		except: return 0

	def _source_bitrate_mbps(self, item):
		try:
			mediatype = getattr(self, 'mediatype', self.meta.get('mediatype'))
			duration = float(self.meta.get('duration') or (3600 if mediatype == 'episode' else 5400))
			size = float(item.get('size') or 0)
			return round(size * 8000 / duration, 3) if duration > 0 and size > 0.01 else None
		except (TypeError, ValueError, OverflowError): return None

class ConfigLoader:
	def _as_bool(self, value, default=False):
		if value in (None, ''): value = default
		if isinstance(value, bool): return value
		return str(value).lower() == 'true'

	def _as_int(self, value, default=''):
		if value in (None, ''): return default
		return int(value)

	def quality_filter(self, source):
		if not source.autoplay: setting = 'results_quality_%s' % source.mediatype
		else: setting = 'autoplay_quality_%s' % source.mediatype
		filter_list = quality_filter(setting)
		if source.include_prerelease_results and 'SD' in filter_list: filter_list += ['SCR', 'CAM', 'TELE']
		return filter_list

	def _use_full_screen_progress(self, source):
		if source.background: return False
		if self._as_bool(source.params.get('autoplay_next'), False): return True
		if get_property('pov_lite_total_autoplays') != '': return False
		return get_setting('load_action') == '1'

	def apply(self, source, params=None):
		if params: source.params = params
		params_get = source.params.get
		source.background = self._as_bool(params_get('background'), False)
		if source.background: hide_busy_dialog()
		else: show_busy_dialog()
		source.disabled_ignored = self._as_bool(params_get('disabled_ignored'), False)
		source.ignore_scrape_filters = self._as_bool(params_get('ignore_scrape_filters'), False)
		source.force_full_search = self._as_bool(params_get('force_full_search'), False)
		source.tmdb_id = params_get('tmdb_id')
		source.season = self._as_int(params_get('season'), '')
		source.episode = self._as_int(params_get('episode'), '')
		source.custom_title = params_get('custom_title')
		source.custom_year = params_get('custom_year')
		source.custom_season = self._as_int(params_get('custom_season'), None)
		source.custom_episode = self._as_int(params_get('custom_episode'), None)
		source.mediatype = 'episode' if source.episode else 'movie'
		if 'autoplay' in source.params: source.autoplay = self._as_bool(params_get('autoplay'), False)
		else: source.autoplay = settings.auto_play(source.mediatype)
		source.display_uncached_torrents = settings.display_uncached_torrents()
		source.ignore_results_filter = settings.ignore_results_filter()
		source.provider_sort_ranks = settings.provider_sort_ranks()
		source.scraper_settings = settings.scraping_settings()
		source.sort_function = settings.results_sort_order()
		source.filter_av1 = settings.filter_status('av1')
		source.filter_hevc = settings.filter_status('hevc')
		source.filter_hdr = settings.filter_status('hdr')
		source.filter_dv = settings.filter_status('dv')
		source.hybrid_allowed = source.filter_hdr in (0, 2)
		source.include_prerelease_results, source.include_3D_results = settings.include_prerelease_3d_results()
		source.quality_filter = self.quality_filter(source)
		source.full_screen = self._use_full_screen_progress(source)
		source.size_filter = int(get_setting('results.size_filter', '0'))
		source.include_unknown_size = self._as_bool(get_setting('results.include.unknown.size'), False)
		source.timeout = 20
		if self._as_bool(get_setting('results.language_filter'), False):
			source.priority_language = get_setting('results.language')
		else: source.priority_language = None

class DialogProgress:
	def __init__(self, full_screen=True):
		self.full_screen = full_screen
		self.dialog_progress = None

	def make(self, meta):
		self.dialog_progress = create_window(('windows.progress', 'ProgressMedia'), 'progress_media.xml', meta=meta)
		self.iscanceled = self.dialog_progress.iscanceled
		self.update = self.dialog_progress.update
		Thread(target=self.dialog_progress.run).start()

	def kill(self):
		try: self.dialog_progress.close()
		except: close_all_dialog()
		try: del self.dialog_progress
		except: pass
		self.dialog_progress = None

class MetaBuilder:
	def get(self, source):
		if 'meta' in source.params: meta = json.loads(source.params['meta'])
		else: meta = self.get_meta(source)
		for i in ('custom_title', 'custom_year', 'custom_season', 'custom_episode'):
			if getattr(source, i, False): meta[i] = getattr(source, i, '')
		expiry_times = get_cache_expiry(source.mediatype, meta, source.season)
		title = get_title(meta)
		aliases = self.make_alias_dict(meta, title)
		year = meta.get('custom_year', meta.get('year') or '0')
		ep_name = self.get_ep_name(meta)
		require_year, conflicting_years = self.tv_identity_policy(source, title, year)
		search_info = {
			'scrape_timeout': source.timeout, 'mediatype': source.mediatype, 'expiry_times': expiry_times,
			'tmdb_id': source.tmdb_id, 'imdb_id': meta.get('imdb_id'), 'tvdb_id': meta.get('tvdb_id'),
			'title': title, 'aliases': aliases, 'year': year, 'ep_name': ep_name,
			'require_tv_year': require_year, 'conflicting_tv_years': conflicting_years,
			'total_seasons': meta.get('total_seasons', ''),
			'season': source.custom_season or source.season,
			'episode': source.custom_episode or source.episode,
		}
		meta.update({
			'search_info': search_info, 'background': source.background,
			'mediatype': source.mediatype, 'season': source.season, 'episode': source.episode
		})
		return meta

	def require_tv_year(self, source, title, year):
		return self.tv_identity_policy(source, title, year)[0]

	def tv_identity_policy(self, source, title, year):
		if source.mediatype != 'episode' or not title or not year: return False, []
		conflicting_years, require_year = [], False
		try:
			from indexers.tmdb_api import tmdb_tv_title_year
			requested_title, requested_year, requested_id = source_utils.clean_title(title), str(year), str(source.tmdb_id)
			results = tmdb_tv_title_year(title).get('results', [])
			for item in results:
				candidate_year = (item.get('first_air_date') or '')[:4]
				candidate_titles = (item.get('name') or '', item.get('original_name') or '')
				if candidate_year == requested_year or str(item.get('id')) == requested_id: continue
				if not any(source_utils.clean_title(candidate_title) == requested_title for candidate_title in candidate_titles): continue
				if not candidate_year:
					require_year = True
					continue
				conflicting_years.append(candidate_year)
				require_year = self.tv_season_overlap(source, item.get('id')) or require_year
		except: return True, conflicting_years
		return require_year, conflicting_years

	def tv_season_overlap(self, source, tmdb_id):
		# Only a complete season list can establish that another series lacks this numbering.
		try:
			season = int(getattr(source, 'custom_season', None) or source.season)
			if season <= 0 or not tmdb_id: return True
			meta = tvshow_meta('tmdb_id', tmdb_id, settings.metadata_user_info(), get_datetime())
			seasons, total = meta['season_data'], int(meta['total_seasons'])
			if not isinstance(seasons, list) or not seasons or total < 1: return True
			numbers = [int(item['season_number']) for item in seasons]
			if any(number < 0 for number in numbers) or len(numbers) != len(set(numbers)): return True
			if set(number for number in numbers if number > 0) != set(range(1, total + 1)): return True
			return season in numbers
		except: return True

	def get_meta(self, source):
		meta_user_info, current_date = settings.metadata_user_info(), get_datetime()
		if source.mediatype == 'episode':
			meta = tvshow_meta('tmdb_id', source.tmdb_id, meta_user_info, current_date)
			try:
				episodes_data = season_episodes_meta(source.season, meta, meta_user_info)
				ep_data = next((i for i in episodes_data if i['episode'] == int(source.episode)))
				meta.update({
					'mediatype': 'episode', 'season': ep_data['season'], 'episode': ep_data['episode'],
					'ep_name': ep_data['title'], 'premiered': ep_data['premiered'], 'plot': ep_data['plot']
				})
			except: pass
		else: meta = movie_meta('tmdb_id', source.tmdb_id, meta_user_info, current_date)
		return meta

	def make_alias_dict(self, meta, title):
		seen = set()
		country_codes = (i.replace('GB', 'UK') for i in meta.get('country_codes', []))
		country_codes = tuple(dict.fromkeys(country_codes))
		raw_titles = (meta['title'], meta['original_title'], *meta.get('alternative_titles', []))
		raw_titles = tuple(dict.fromkeys(raw_titles))
		aliases = [{'title': i, 'country': ''} for i in raw_titles]
		aliases.extend({'title': '%s %s' % (title, i), 'country': ''} for i in country_codes)
		valid_titles = (i for i in aliases if i['title'] != title and safe_string(i['title']).strip())
		aliases = [i for i in valid_titles if i['title'] not in seen and not seen.add(i['title'])]
		return aliases

	def get_ep_name(self, meta):
		if meta.get('mediatype') == 'episode':
			ep_name = meta.get('ep_name') or ''
			try: ep_name = safe_string(ep_name)
			except: pass
		else: ep_name = None
		return ep_name

class ScraperProcessor:
	def __init__(self, source_instance):
		self.source = source_instance

	def activate_external(self):
		self.source.external_providers = []
		self.source.debrid_enabled = debrid_enabled()
		self.source.debrid_torrent_enabled = debrid_type_enabled('torrent', self.source.debrid_enabled)
		if not self.source.debrid_torrent_enabled:
			self.source.progress_dialog.kill()
			return notification(32854)
		external_providers = magneto_sources(ret_all=self.source.disabled_ignored)
		self.source.external_providers.extend([
			i for i in external_providers if i[0] not in self.source.exclude_list
			and (i[1].hasEpisodes if self.source.mediatype == 'episode' else i[1].hasMovies)
		])
		if self.source.mediatype != 'episode': return
		self.source.external_providers = [(i[0], i[1], '') for i in self.source.external_providers]
		season_packs, show_packs = pack_enable_check(self.source.meta, self.source.season, self.source.episode)
		if not season_packs: return
		pack_capable = [i for i in self.source.external_providers if i[1].pack_capable]
		if pack_capable:
			self.source.external_providers.extend([(i[0], i[1], season_str) for i in pack_capable])
		if pack_capable and show_packs:
			self.source.external_providers.extend([(i[0], i[1], show_str) for i in pack_capable])

class ResultsProcessor:
	def __init__(self, source_instance):
		self.source = source_instance
		self._bandwidth_profile = None

	def process(self, results):
		if self.source.ignore_scrape_filters:
			self.source.filters_ignored = True
			results = self.sort_results(results)
		else:
			results = self.filter_results(results)
			results = self.sort_results(results)
			results = self.special_filter(results, hevc_filter_key, self.source.filter_hevc)
			results = self.special_filter(results, hdr_filter_key, self.source.filter_hdr)
			results = self.special_filter(results, dolby_vision_filter_key, self.source.filter_dv)
			results = self.special_filter(results, av1_filter_key, self.source.filter_av1)
		matched_hashes = {item.get('hash') for item in results if item.get('_identity_status') != 'unverified' and item.get('hash')}
		self.source.unverified_results = [item for item in results if item.get('_identity_status') == 'unverified' and item.get('hash') not in matched_hashes]
		return [item for item in results if item.get('_identity_status') != 'unverified']

	def filter_results(self, results):
		results = [i for i in results if i['quality'] in self.source.quality_filter]
		if not self.source.include_3D_results:
			results = [i for i in results if '3D' not in i['extraInfo']]
		if not self.source.size_filter:
			return results
		if self.source.size_filter == 1:
			duration = self.source.meta['duration'] or (3600 if self.source.mediatype == 'episode' else 5400)
			max_size = string_to_float(get_setting('results.size.speed', '20'), '20')
			max_size = ((0.125 * (0.90 * max_size)) * duration)/1000
		if self.source.size_filter == 2:
			max_size = string_to_float(get_setting('results.size.file', '10000'), '10000') / 1000
		if self.source.include_unknown_size:
			results = [i for i in results if i['scrape_provider'].startswith('folder') or i['size'] <= max_size]
		else: results = [
			i for i in results if i['scrape_provider'].startswith('folder') or 0.01 < i['size'] <= max_size
		]
		return results

	def filter_staged_results(self, results):
		results = self.filter_results(results)
		for key, setting in (
			(hevc_filter_key, self.source.filter_hevc), (hdr_filter_key, self.source.filter_hdr),
			(dolby_vision_filter_key, self.source.filter_dv), (av1_filter_key, self.source.filter_av1)
		):
			if setting == 1: results = self.special_filter(results, key, setting)
		return results

	def sort_results(self, results):
		for item in results:
			provider, quality = item['scrape_provider'], item.get('quality', 'SD')
			account_type = item['debrid'].lower() if provider == 'external' else provider.lower()
			item['provider_rank'] = self.get_provider_rank(account_type)
			item['quality_rank'] = self.get_quality_rank(quality)
		results.sort(key=self.source.sort_function)
		if self.source.autoplay: results.sort(key=self.autoplay_source_key)
		if self.source.priority_language:
			results = self.sort_language_to_top(results)
		results = self.sort_uncached_torrents(results)
		clear_property('pov_lite_fs_filterless_search')
		return results

	def autoplay_source_key(self, item):
		duration = self.source.meta.get('duration') or (3600 if self.source.mediatype == 'episode' else 5400)
		if self._bandwidth_profile is None:
			bandwidth_mbps = string_to_float(get_setting('results.size.speed', '20'), '20')
			resolution_fallback_mbps = None
			if playback_health is not None:
				try: bandwidth_mbps = playback_health.learned_bandwidth(bandwidth_mbps)
				except: pass
				try: resolution_fallback_mbps = playback_health.resolution_fallback_limit()
				except: pass
			self._bandwidth_profile = bandwidth_mbps, resolution_fallback_mbps
		else: bandwidth_mbps, resolution_fallback_mbps = self._bandwidth_profile
		max_sustainable_size = ((0.125 * (0.65 * bandwidth_mbps)) * duration) / 1000
		size = item.get('size') or 0
		unknown_size = size <= 0.01
		oversized = not unknown_size and size > max_sustainable_size
		minimum_bitrate = {'4K': 8.0, '1080p': 3.0, '720p': 1.5, 'SD': 0.5}.get(item.get('quality'), 0.5)
		minimum_plausible_size = ((0.125 * minimum_bitrate) * duration) / 1000
		implausibly_tiny = not unknown_size and size < minimum_plausible_size
		fallback_rank = 3 if unknown_size else 2 if implausibly_tiny else 1 if oversized else 0
		provider_penalty = 0
		if playback_health is not None:
			try:
				provider = playback_health.source_context(item).get('provider')
				provider_penalty = playback_health.provider_penalty(provider)
			except: pass
		quality, quality_rank, resolution_tier = item.get('quality'), item['quality_rank'], 0
		resolution_oversized = resolution_fallback_mbps is not None and not unknown_size and size > ((0.125 * resolution_fallback_mbps) * duration) / 1000
		if quality == '4K' and resolution_oversized:
			quality_rank, resolution_tier = quality_ranks['1080p'], 1
		elif resolution_fallback_mbps is not None and quality == '1080p' and (resolution_oversized or unknown_size or implausibly_tiny): resolution_tier = 2
		return quality_rank, resolution_tier, fallback_rank, item['quality_rank'], item.get('provider_rank', 11), provider_penalty, -size if fallback_rank == 0 else size

	def get_provider_rank(self, account_type):
		return self.source.provider_sort_ranks[account_type] or 11

	def get_quality_rank(self, quality):
		return quality_ranks[quality]

	def sort_language_to_top(self, results):
		from modules.meta_lists import meta_languages
		try:
			tokens = meta_languages[self.source.priority_language].values()
			tokens = [re.sub(r'\W', '', self.source.priority_language), *tokens]
			if 'Spanish' in self.source.priority_language: tokens += 'latino', 'lat', 'esp'
			if 'Brazil' in self.source.priority_language: tokens += 'portuguese', 'por', 'pt'
			pattern = re.compile(r'\b(%s)\b' % '|'.join(i for i in tokens if i), re.I)
			sort_first, sort_last = [], []
			for item in results:
				(sort_first if pattern.search(item.get('name_info', '')) else sort_last).append(item)
			results = sort_first + sort_last
		except: pass
		return results

	def sort_uncached_torrents(self, results):
		if self.source.background or self.source.autoplay:
			return [i for i in results if 'Uncached' not in i.get('cache_provider', '')]
		if self.source.display_uncached_torrents or get_property('pov_lite_fs_filterless_search') == 'true':
			return results
		return [i for i in results if 'Uncached' not in i.get('cache_provider', '')]

	def special_filter(self, results, key, enable_setting):
		if enable_setting == 1:
			if key == dolby_vision_filter_key and self.source.hybrid_allowed:
				results = [
					i for i in results if all(x in i['extraInfo']
					for x in (key, hdr_filter_key)) or key not in i['extraInfo']
				]
			else: results = [i for i in results if key not in i['extraInfo']]
		elif enable_setting == 2 and self.source.autoplay:
			priority_list, remainder_list = [], []
			for item in results:
				(priority_list if key in item['extraInfo'] else remainder_list).append(item)
			results = priority_list + remainder_list
		elif enable_setting == 3:
			results.sort(
				key=lambda k: key in k['extraInfo'] and 'Uncached' not in k.get('cache_provider', ''),
				reverse=True
			)
		return results

class ExternalManager:
	def dialog_hook(function):
		def wrapper(instance, *args, **kwargs):
			if not instance.background:
				instance.progress_heading = ext_scr_format % (instance.ext_dialog_highlight, ls(32118))
				hide_busy_dialog()
				if not instance.progress_dialog.full_screen:
					progressDialogBG.create('BINGIE Lite', 'BINGIE Lite loading...')
				else: instance.progress_dialog.make(instance.meta)
			result = function(instance, *args, **kwargs)
			if not instance.background:
				if not instance.progress_dialog.full_screen:
					progressDialogBG.close()
				else: instance.progress_dialog.kill()
			return result
		return wrapper

	def __init__(
		self, meta, source_dict, debrid_torrents, progress_dialog, eligibility_filter=None, force_full_search=False, snapshot_callback=None, cancel_event=None
	):
		self.meta, self.background = meta, meta.get('background', False)
		self.source_dict, self.debrid_torrents = source_dict, debrid_torrents
		self.progress_dialog = progress_dialog
		self.eligibility_filter, self.force_full_search = eligibility_filter, force_full_search
		self.snapshot_callback = snapshot_callback
		self.snapshot_sent = False
		self.cancel_event = cancel_event
		self.hostDict, self.sources, self.final_sources = [], [], []
		self.sleep_time = settings.display_sleep_time()
		self.timeout = int(self.meta.get('search_info', {}).get('scrape_timeout', '10'))
		self.ext_dialog_highlight = get_setting('ext_dialog_highlight', 'magenta')
		self.ext_total = ext_scr_format % (self.ext_dialog_highlight, '%s')
		self.resolutions = dict.fromkeys(resolutions.split(), 0)

	@dialog_hook
	def results(self, info):
		core_dict, fallback_dict = split_external_providers(self.source_dict)
		exhaustive_core = self.force_full_search or not core_dict
		if exhaustive_core: core_dict, fallback_dict = self.source_dict, []
		tpe = TPE(external_worker_count(len(core_dict), len(self.debrid_torrents), exhaustive_core))
		try:
			unique_urls, unique_hashes = set(), set()
			core_results = timed_search_stage(self.meta, 'core_providers', self.fetch_sources, core_dict, info, tpe)
			if self.cancel_event and self.cancel_event.is_set(): return self.final_sources
			core_sources = list(self.process_duplicates(core_results, unique_urls, unique_hashes))
			self.sources.extend(core_sources)
			core_final, core_cached = timed_search_stage(self.meta, 'core_debrid', self.cache_sources, core_sources, bool(self.snapshot_callback))
			self.final_sources.extend(core_final)
			if self.cancel_event and self.cancel_event.is_set(): return self.final_sources
			if fallback_dict and self.eligible_cached_count(core_final, core_cached) >= CORE_CACHED_RESULT_TARGET:
				self.meta['full_search_available'] = True
				return self.final_sources
			if fallback_dict:
				self.publish_cached_snapshot(core_final, core_cached)
				tpe.shutdown(wait=False, cancel_futures=True)
				tpe = TPE(external_worker_count(len(fallback_dict), len(self.debrid_torrents), True))
				fallback_results = timed_search_stage(self.meta, 'fallback_providers', self.fetch_sources, fallback_dict, info, tpe)
				if self.cancel_event and self.cancel_event.is_set(): return self.final_sources
				fallback_sources = list(self.process_duplicates(fallback_results, unique_urls, unique_hashes))
				self.sources.extend(fallback_sources)
				fallback_final, _cached = timed_search_stage(self.meta, 'fallback_debrid', self.cache_sources, fallback_sources)
				self.final_sources.extend(fallback_final)
		except:
			if not getattr(self, 'manual_progress', False): notification(32574)
		finally: tpe.shutdown(wait=False, cancel_futures=True)
		return self.final_sources

	def fetch_sources(self, source_dict, info, tpe):
		threads, results = [], []
		for provider, module, *pack in source_dict:
			if self.cancel_event and self.cancel_event.is_set(): break
			args = (provider, module, *pack) if pack else (provider, module)
			fut = tpe.submit(ExternalSource(self.meta, self.resolutions).results, info, args)
			fut.name = pack_display % (provider, *pack) if pack and pack[0] else provider
			threads.append(fut)
		self.thread_monitor(threads, ls(32676))
		for fut in threads:
			if not fut.done():
				fut.cancel()
				continue
			try: results.extend(fut.result())
			except: pass
		return results

	def publish_cached_snapshot(self, sources, cached_hashes):
		if not self.snapshot_callback or self.snapshot_sent or (self.cancel_event and self.cancel_event.is_set()): return
		try:
			eligible = self.eligibility_filter(list(sources)) if self.eligibility_filter else sources
			snapshot = [item for item in eligible if item.get('hash') in cached_hashes and item.get('_identity_status') != 'unverified' and 'Uncached' not in item.get('cache_provider', '') and 'Unchecked' not in item.get('cache_provider', '')]
			if snapshot:
				self.snapshot_callback(snapshot)
				self.snapshot_sent = True
		except Exception: pass

	def cache_sources(self, sources, publish_snapshot=False):
		if self.cancel_event and self.cancel_event.is_set(): return [], set()
		torrent_sources = [i for i in sources if 'torrent' in i['source']]
		if not torrent_sources: return [], set()
		try: check_sources = self.eligibility_filter(list(torrent_sources)) if self.eligibility_filter else torrent_sources
		except: check_sources = torrent_sources
		check_hashes = list(dict.fromkeys(i['hash'] for i in check_sources))
		if not check_hashes:
			return [
				{**i, 'cache_provider': '%s %s' % ('Unchecked', name), 'debrid': name}
				for name in self.debrid_torrents for i in torrent_sources
			], set()
		hash_list, cached_context = DebridCheck.request_context(check_hashes)
		tpe = TPE(external_worker_count(0, len(self.debrid_torrents), True))
		try:
			threads = []
			def check_debrid(item):
				started = time.monotonic()
				try: return DebridCheck(self.meta, item, hash_list, cached_context).cache_check()
				finally: log_search_timing(self.meta, 'debrid_provider', started, item)
			for item in self.debrid_torrents:
				if self.cancel_event and self.cancel_event.is_set(): break
				fut = tpe.submit(check_debrid, item)
				fut.name = item
				threads.append(fut)
			def publish_finished(threads):
				if not publish_snapshot or self.snapshot_sent: return
				if all(fut.done() for fut in threads): return
				cached_sources, cached_hashes = [], set()
				for fut in threads:
					if not fut.done(): continue
					try:
						cache_result = fut.result()
						hashes = set(cache_result.get('cached', ())) if isinstance(cache_result, dict) else set(cache_result)
						cached_hashes.update(hashes)
						cached_sources.extend({**item, 'cache_provider': fut.name, 'debrid': fut.name} for item in torrent_sources if item['hash'] in hashes)
					except Exception: pass
				if all(fut.done() for fut in threads): return
				self.publish_cached_snapshot(cached_sources, cached_hashes)
			self.thread_monitor(threads, ls(32579), publish_finished if publish_snapshot else None)
			results, cached_hashes = [], set()
			for fut in threads:
				if not fut.done():
					fut.cancel()
					continue
				try: cache_result = fut.result()
				except: continue
				if isinstance(cache_result, dict):
					hashes = set(cache_result.get('cached', ()))
					checked_hashes = set(cache_result.get('checked', ()))
				else:
					hashes = set(cache_result)
					checked_hashes = set()
				cached_hashes.update(hashes)
				results.extend({
					**i, 'cache_provider': fut.name if i['hash'] in hashes else '%s %s' % ('Uncached' if i['hash'] in checked_hashes else 'Unchecked', fut.name), 'debrid': fut.name
				} for i in torrent_sources)
			return results, cached_hashes
		finally: tpe.shutdown(wait=False, cancel_futures=True)

	def eligible_cached_count(self, sources, cached_hashes):
		try: eligible = self.eligibility_filter(list(sources)) if self.eligibility_filter else sources
		except: eligible = sources
		return len({i['hash'] for i in eligible if i.get('hash') in cached_hashes and i.get('_identity_status') != 'unverified'})

	def thread_monitor(self, threads, status_line='', on_update=None):
		len_threads = len(threads)
		end_time = time.monotonic() + self.timeout + 1
		while not monitor.abortRequested() and time.monotonic() <= end_time:
			if self.cancel_event and self.cancel_event.is_set(): break
			if on_update: on_update(threads)
			alive_threads = [x.name for x in threads if not x.done()]
			if not alive_threads: break
			if getattr(self, 'manual_progress', False): self.manual_progress_state = (alive_threads, len_threads, status_line)
			if not self.background: self.progress_update(alive_threads, len_threads, status_line)
			sleep(self.sleep_time)

	def progress_update(self, alive_threads, len_threads, string1, display=None):
		try:
			len_alive_threads = len(alive_threads)
			progress = max(0, int((len_threads-len_alive_threads)/max(1, len_threads)*100))
			if len_alive_threads > 5: line3 = string1 % str(len_threads-len_alive_threads)
			else: line3 = string1 % ', '.join(alive_threads).upper()
			if display is not None:
				display.update(progress, line3)
				return
			if self.progress_dialog.full_screen:
				ext_totals = [self.ext_total % v for v in self.resolutions.values()]
				line2 = diag_format % tuple(ext_totals)
				self.progress_dialog.update(format_line % (self.progress_heading, line2, line3), progress)
			else: progressDialogBG.update(progress, line3)
		except: pass

	def process_duplicates(self, sources, uniqueURLs=None, uniqueHashes=None):
		uniqueURLs = uniqueURLs if uniqueURLs is not None else set()
		uniqueHashes = uniqueHashes if uniqueHashes is not None else set()
		for provider in sources:
			try:
				evidence = provider.get('identity_evidence') or {}
				identity_group = ':unverified:%s:%s' % (evidence.get('file_sid', ''), evidence.get('file_path', '')) if provider.get('_identity_status') == 'unverified' else ''
				url = provider['url'].lower() + identity_group
				if url in uniqueURLs: continue
				uniqueURLs.add(url)
				if 'hash' in provider:
					_hash = provider['hash'].lower() + identity_group
					if _hash in uniqueHashes: continue
					uniqueHashes.add(_hash)
					yield provider
				else: yield provider
			except: yield provider

class ExternalSource:
	scrape_provider = 'external'
	timeout = 10
	hostDict = {}

	def __init__(self, meta, resolutions=None):
		self.sources = []
		self.meta = meta
		if isinstance(resolutions, dict): self.resolutions = resolutions
		else: self.resolutions = dict.fromkeys(resolutions.split(), 0)

	def results(self, info, args):
		started = time.monotonic()
		try:
			self.mediatype, self.tmdb_id  = info['mediatype'], str(info['tmdb_id'])
			self.year, aliases = info['year'], info['aliases']
			self.require_tv_year = info.get('require_tv_year', False)
			self.conflicting_tv_years = info.get('conflicting_tv_years', [])
			self.season, self.episode = info['season'], info['episode']
			self.total_seasons = info['total_seasons']
			self.title, self.orig_title = safe_string(info['title']), info['title']
			self.single_expiry, self.season_expiry, self.show_expiry = info['expiry_times']
			if 'scrape_timeout' in info: self.timeout = info['scrape_timeout']
			if self.mediatype == 'episode':
				season_divider = (
					i['episode_count'] for i in self.meta['season_data']
					if int(i['season_number']) == int(self.meta['season'])
				)
				self.season_divider = int(next(season_divider, 1))
				self.show_divider = int(self.meta['total_aired_eps'])
				self.data = {
					'timeout': self.timeout, 'imdb': info['imdb_id'], 'tvdb': info['tvdb_id'],
					'title': safe_string(info['ep_name']), 'tvshowtitle': self.title, 'year': self.year,
					'season': str(self.season), 'episode': str(self.episode),
					'total_seasons': self.total_seasons, 'aliases': aliases
				}
				self.get_episode_source(*args)
			else:
				self.season_divider, self.show_divider, self.data = 1, 1, {
					'timeout': self.timeout, 'imdb': info['imdb_id'],
					'title': self.title, 'year': self.year, 'aliases': aliases
				}
				self.get_movie_source(*args)
		except: pass
		finally: log_search_timing(self.meta, 'provider', started, args[0])
		return self.sources

	def get_movie_source(self, provider, module):
		epc = ExternalProvidersCache()
		try:
			outcome = epc.get(provider, self.mediatype, self.tmdb_id, self.title, self.year, '', '')
			if outcome is None:
				instance = module()
				outcome = ProviderOutcome.from_provider(instance.sources(self.provider_data(instance), self.hostDict), instance)
				outcome.sources = self.process_sources(provider, outcome.sources)
				epc.set(provider, self.mediatype, self.tmdb_id, self.title, self.year, '', '', outcome, self.single_expiry)
		finally: epc.close()
		sources = outcome.sources
		if sources:
			self.sources.extend(sources)

	def get_episode_source(self, provider, module, pack):
		if pack in pack_check: s_check, e_check = '' if pack == show_display else self.season, ''
		else: s_check, e_check = self.season, self.episode
		epc = ExternalProvidersCache()
		cache_version = 'v6' if provider == 'torrentio' else 'v4' if provider in ('bitmagnet', 'comet', 'mediafusion', 'nyaa', 'torrentsdb', 'torz', 'zilean') else 'v3'
		cache_provider = provider + ':tv_identity_' + cache_version
		try:
			outcome = epc.get(cache_provider, self.mediatype, self.tmdb_id, self.title, self.year, s_check, e_check)
			if outcome is None:
				instance = module()
				data = self.provider_data(instance)
				if pack == show_display:
					expiry_hours = self.show_expiry
					sources = instance.sources_packs(data, self.hostDict, search_series=True, total_seasons=self.total_seasons)
				elif pack == season_display:
					expiry_hours = self.season_expiry
					sources = instance.sources_packs(data, self.hostDict)
				else:
					expiry_hours = self.single_expiry
					sources = instance.sources(data, self.hostDict)
				outcome = ProviderOutcome.from_provider(sources, instance)
				epc.set(cache_provider, self.mediatype, self.tmdb_id, self.title, self.year, s_check, e_check, outcome, expiry_hours)
		finally: epc.close()
		sources = self.process_sources(provider, self.filter_ambiguous_tv_sources(outcome.sources, pack))
		if sources:
			if pack == season_display: sources = [i for i in sources if 'episode_start' not in i or i['episode_start'] <= self.episode <= i['episode_end']]
			elif pack == show_display: sources = [i for i in sources if i['last_season'] >= self.season]
			self.sources.extend(sources)

	def filter_ambiguous_tv_sources(self, sources, pack):
		require_year = self.require_tv_year
		expected_year = str(self.year)
		conflicting_years = getattr(self, 'conflicting_tv_years', [])
		filtered = []
		for original in sources or []:
			item = original.copy()
			evidence = item.get('identity_evidence') or {}
			requested_imdb = str(getattr(self, 'data', {}).get('imdb') or '')
			returned_imdb = str(evidence.get('imdb_id') or '')
			catalogue_imdb = str(evidence.get('catalogue_imdb_id') or '')
			if requested_imdb and any(value != requested_imdb for value in (returned_imdb, catalogue_imdb) if value): continue
			file_sid = evidence.get('file_sid')
			expected_sid = '%s:%s:%s' % (requested_imdb, getattr(self, 'season', ''), getattr(self, 'episode', ''))
			if evidence.get('provenance') == 'filename_match':
				if file_sid or evidence.get('provider_file_sid') != '*' or evidence.get('requested_sid') != expected_sid or not evidence.get('file_match_required'): continue
			else:
				if file_sid and str(file_sid) != expected_sid: continue
				if evidence.get('file_match_required') and not file_sid: continue
			name = '%s %s' % (item.get('name', ''), item.get('name_info', ''))
			def has_year(year): return re.search(r'(?<![A-Za-z0-9])%s(?![A-Za-z0-9])' % re.escape(year), name)
			if has_year(expected_year): pass
			elif any(has_year(year) for year in conflicting_years): continue
			elif require_year:
				item['_identity_status'] = 'unverified'
				item['_identity_reason'] = 'Provider association; release does not identify the series year' if returned_imdb or file_sid else 'Release does not identify the series year'
			else: pass
			filtered.append(item)
		return filtered

	def provider_data(self, instance):
		data = self.data.copy()
		try: data['timeout'] = min(int(self.timeout), int(instance.timeout))
		except: pass
		return data

	def process_sources(self, provider, sources):
		for i in sources:
			try:
				i_get = i.get
				if 'hash' in i: i['hash'] = str(i['hash']).lower()
				display_name = source_utils.clean_file_name(i_get('name')).replace('html', ' ')
				quality, extraInfo = get_file_info(name_info=i_get('name_info'))
				size, size_label, divider = 0, None, None
				try:
					size = i_get('size')
					if 'package' in i and not i_get('true_size', False):
						if i_get('package') == 'season': divider = self.season_divider
						else: divider = self.show_divider
						size = float(size) / divider
						size_label = '%.2f GB' % size
					else: size_label = '%.2f GB' % size
				except: pass
				i.update({
					'external': True, 'scrape_provider': self.scrape_provider,
					'provider': provider, 'display_name': display_name,
					'extraInfo': extraInfo, 'quality': quality,
					'size_label': size_label, 'size': round(size, 2)
				})
				if quality not in self.resolutions: self.resolutions['SD'] += 1
				else: self.resolutions[quality] += 1
				self.resolutions['total'] += 1
			except: pass
		return sources
