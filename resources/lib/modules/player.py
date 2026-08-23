import json
from threading import Thread
from time import monotonic
from caches import watched_cache as ws
from windows import open_window
from indexers.segments import SegmentScraper
from indexers.metadata import art_infodict, movie_show_infodict, episode_infodict, info_tagger, resized_cast
from indexers import tmdb_api
from modules import kodi_utils, settings
try: from modules import playback_health
except Exception: playback_health = None
from modules.meta_lists import meta_languages
from modules.utils import sec2time
# from modules.kodi_utils import logger

KODI_VERSION, make_cast_list = kodi_utils.get_kodi_version(), kodi_utils.make_cast_list
ls, get_setting = kodi_utils.local_string, kodi_utils.get_setting
get_art_provider, meta_user_info = settings.get_art_provider, settings.metadata_user_info
fanart_empty = kodi_utils.get_addoninfo('fanart')
poster_empty = kodi_utils.media_path('box_office.png')
PLAYBACK_START_TIMEOUT = 30.0
PLAYBACK_DURATION_TIMEOUT = 5.0
MINIMUM_DURATION_RATIO = 0.5
MINIMUM_DURATION_GAP = 600
ENGLISH_AUDIO_LANGUAGES = ('en', 'eng', 'english')
ALTERNATE_AUDIO_NAMES = ('commentary', 'audio description', 'descriptive audio')
ISO_639_EQUIVALENTS = (
	('sq', 'alb', 'sqi'), ('hy', 'arm', 'hye'), ('eu', 'baq', 'eus'), ('my', 'bur', 'mya'), ('zh', 'chi', 'zho'), ('cs', 'cze', 'ces'), ('nl', 'dut', 'nld'),
	('fr', 'fre', 'fra'), ('ka', 'geo', 'kat'), ('de', 'ger', 'deu'), ('el', 'gre', 'ell'), ('is', 'ice', 'isl'), ('mk', 'mac', 'mkd'), ('mi', 'mao', 'mri'),
	('ms', 'may', 'msa'), ('fa', 'per', 'fas'), ('ro', 'rum', 'ron'), ('sr', 'scc', 'srp'), ('sk', 'slo', 'slk'), ('bo', 'tib', 'bod'), ('cy', 'wel', 'cym')
)
AUDIO_LANGUAGE_GROUPS = tuple(
	frozenset(str(value).strip().lower().replace('_', '-').split('-', 1)[0] for value in language.values() if value)
	for language in meta_languages.values()
)

class POVPlayer(kodi_utils.xbmc_player):
	def __init__(self):
		kodi_utils.xbmc_player.__init__(self)
		self.set_resume, self.set_watched = 5, 90
		self.playback_event, self.progress_media = None, None
		self.playback_error, self.retry_resume_percent = False, 0
		self.playback_health_context, self.playback_health_started_at, self.playback_health_recorded = None, None, False
		self.ignore_startup_stop, self.startup_playback_started = False, False
		self.media_marked, self.nextep_info_gathered = False, False
		self.subs_searched, self.stingers_checked = False, False
		self.nextep_started, self.next_episode_requested, self.play_random_continual = False, False, False
		self.autoplay_next_episode = False
		self.autoplay_nextep = settings.autoplay_next_episode()
		self.autoscrape_next_episode = False
		self.autoscrape_nextep = settings.autoscrape_next_episode()
		self.art_provider = (*get_art_provider(), poster_empty, fanart_empty)
		self.stinger_enabled = get_setting('stingers.enable') == 'true'
		self.stinger_check = int(get_setting('stingers.threshold', '30'))
		self.skip_intro_enabled = get_setting('skip_intro.enable') == 'true'
		self.volume_check = get_setting('volumecheck.enabled', 'false') == 'true'

	def onAVStarted(self):
		self.playback_event = True
		try: playback_file = self.getPlayingFile()
		except: playback_file = ''
		Thread(target=self._select_preferred_audio, args=(playback_file,), daemon=True).start()

	def _select_preferred_audio(self, playback_file=''):
		try:
			if not playback_file: playback_file = self.getPlayingFile()
			if not playback_file: return
			for _ in range(10):
				if not self._audio_selection_is_current(playback_file): return
				request = {'jsonrpc': '2.0', 'id': 1, 'method': 'Player.GetProperties', 'params': {'playerid': 1, 'properties': ['audiostreams', 'currentaudiostream']}}
				result = json.loads(kodi_utils.execJSONRPC(json.dumps(request))).get('result', {})
				streams = result.get('audiostreams') or []
				if streams: break
				if kodi_utils.monitor.waitForAbort(0.2): return
			else: return
			original_language = self.meta_get('original_language', '')
			selected = self._preferred_audio_stream(streams, original_language)
			if selected is None and not original_language:
				original_language = tmdb_api.media_original_language(self.mediatype, self.tmdb_id)
				if original_language:
					self.meta['original_language'] = original_language
					selected = self._preferred_audio_stream(streams, original_language)
			if selected is None: return
			if not self._audio_selection_is_current(playback_file): return
			if selected.get('index') != result.get('currentaudiostream', {}).get('index'):
				self.setAudioStream(selected['index'])
		except: pass

	def _audio_selection_is_current(self, playback_file):
		if not self.isPlayingVideo(): return False
		if not playback_file: return True
		try: return self.getPlayingFile() == playback_file
		except: return False

	@classmethod
	def _preferred_audio_stream(cls, streams, original_language=''):
		streams = [stream for stream in streams if isinstance(stream, dict) and isinstance(stream.get('index'), int)]
		streams = [stream for stream in streams if not cls._is_alternate_audio(stream)]
		preferred = [stream for stream in streams if cls._is_english_audio(stream)]
		if not preferred and original_language:
			preferred = [stream for stream in streams if cls._matches_audio_language(stream, original_language)]
		if not preferred: preferred = [stream for stream in streams if cls._is_original_audio(stream)]
		if not preferred: return None
		return min(preferred, key=cls._preferred_audio_sort_key)

	@staticmethod
	def _is_english_audio(stream):
		language = str(stream.get('language') or '').strip().lower().replace('_', '-')
		name = str(stream.get('name') or '').strip().lower()
		return language in ENGLISH_AUDIO_LANGUAGES or language.startswith('en-') or (not language and 'english' in name)

	@staticmethod
	def _audio_language_aliases(language):
		language = str(language or '').strip().lower().replace('_', '-').split('-', 1)[0]
		if not language: return frozenset()
		aliases = next((aliases for aliases in AUDIO_LANGUAGE_GROUPS if language in aliases), frozenset((language,)))
		try:
			converted = str(kodi_utils.xbmc.convertLanguage(language, kodi_utils.xbmc.ISO_639_2) or '').strip().lower()
			if converted: aliases = aliases.union((converted,))
		except: pass
		for equivalents in ISO_639_EQUIVALENTS:
			if aliases.intersection(equivalents): aliases = aliases.union(equivalents)
		return aliases

	@classmethod
	def _matches_audio_language(cls, stream, language):
		stream_aliases = cls._audio_language_aliases(stream.get('language'))
		return bool(stream_aliases and stream_aliases.intersection(cls._audio_language_aliases(language)))

	@staticmethod
	def _is_original_audio(stream):
		return bool(stream.get('isoriginal')) or 'original' in str(stream.get('name') or '').strip().lower()

	@staticmethod
	def _is_alternate_audio(stream):
		name = str(stream.get('name') or '').lower()
		return bool(stream.get('isimpaired')) or any(label in name for label in ALTERNATE_AUDIO_NAMES)

	@classmethod
	def _preferred_audio_sort_key(cls, stream):
		return not cls._is_original_audio(stream), not bool(stream.get('isdefault')), stream.get('index', 0)

	def onPlayBackStarted(self):
		if self.playback_event is None: self.startup_playback_started = True
		try: kodi_utils.hide_busy_dialog()
		except: pass

	def onPlayBackStopped(self):
		if not self._set_terminal_playback_event() or self.next_episode_requested: return
		from modules.sources import Sources
		Sources.nextep_params.clear()
		kodi_utils.clear_property('pov_lite_total_autoplays')

	def onPlayBackEnded(self):
		self._record_healthy_play(natural_end=True)
		self._set_terminal_playback_event()

	def onPlayBackError(self):
		if self.playback_event is True:
			self._record_playback_health('stream_error', elapsed=self._playback_health_elapsed())
			self.playback_error = True
			self.retry_resume_percent = self._current_resume_percent()
		else:
			self.playback_error = False
			self.retry_resume_percent = 0
		self.playback_event = False

	def _current_resume_percent(self):
		try:
			total_time, curr_time = self.getTotalTime(), self.getTime()
			return max(0, min(100, float(curr_time) / float(total_time) * 100)) if total_time > 0 else 0
		except: return 0

	def _duration_is_plausible(self):
		try: expected = float(self.meta_get('duration') or 0)
		except: expected = 0
		if expected <= 0: return True
		deadline = monotonic() + PLAYBACK_DURATION_TIMEOUT
		actual = 0
		while monotonic() < deadline:
			try: actual = float(self.getTotalTime() or 0)
			except: actual = 0
			if actual > 0: break
			if kodi_utils.monitor.waitForAbort(0.2): return True
		if actual <= 0: return True
		return not (actual < expected * MINIMUM_DURATION_RATIO and expected - actual > MINIMUM_DURATION_GAP)

	def _set_terminal_playback_event(self):
		if self.playback_event is None and self.ignore_startup_stop and not self.startup_playback_started:
			self.ignore_startup_stop = False
			return False
		self.playback_event = False
		return True

	def request_next_episode(self):
		self.next_episode_requested = True
		self.stop()

	def run(self, url=None, meta=None, progress_media=None):
		if not url: return
		try:
			self.meta = meta or {}
			self.playback_health_context = self.meta.pop('_playback_health_context', None)
			self.playback_health_started_at = self.meta.pop('_playback_health_started_at', None)
			self.playback_health_recorded = False
			self.meta_get = self.meta.get
			self.tmdb_id, self.imdb_id = self.meta_get('tmdb_id'), self.meta_get('imdb_id')
			self.title, self.year = self.meta_get('title'), self.meta_get('year')
			self.mediatype, self.tvdb_id = self.meta_get('mediatype'), self.meta_get('tvdb_id')
			self.season, self.episode = self.meta_get('season', ''), self.meta_get('episode', '')
			retry_resume = self.meta.pop('_retry_resume_percent', 0)
			if retry_resume: bookmark = retry_resume
			elif any(i in self.meta for i in ('random', 'random_continual')): bookmark = 0
			else: bookmark = self.bookmarkPOV()
			if bookmark == 'cancel': return
			self.meta.update({'url': url, 'bookmark': bookmark})
			listitem = self.make_listitem()
			listitem.setContentLookup(False)
			listitem.setLabel(self.title)
			listitem.setArt(art_infodict(self.meta, self.art_provider, meta_user_info()))
			listitem.setPath(url)
			listitem.setProperty('StartPercent', str(bookmark))

			self.ignore_startup_stop = self.isPlaying()
			self.startup_playback_started = False
			self.playback_error, self.retry_resume_percent = False, 0
			self.playback_event = None
			self.play(url, listitem)
			start_deadline = monotonic() + PLAYBACK_START_TIMEOUT
			while self.playback_event is None and monotonic() < start_deadline:
				if kodi_utils.monitor.waitForAbort(0.1): return
			if self.playback_event is not True:
				self._record_playback_health('startup_fail', latency=self._playback_health_elapsed())
				kodi_utils.logger('POVPlayer', 'Playback startup failed or timed out')
				return False
			if not self._duration_is_plausible():
				self._record_playback_health('startup_fail', latency=self._playback_health_elapsed())
				kodi_utils.logger('POVPlayer', 'Playback duration is implausibly short; trying the next source')
				self.stop()
				return False
			self._record_playback_health('startup_ok', latency=self._playback_health_elapsed())
			if callable(progress_media): progress_media()
			kodi_utils.close_all_dialog()
			if self.mediatype == 'episode':
				self.play_random_continual = 'random_continual' in self.meta
				if not self.play_random_continual and self.autoplay_nextep:
					self.autoplay_next_episode = 'random' not in self.meta
				if not self.play_random_continual and self.autoscrape_nextep:
					self.autoscrape_next_episode = 'random' not in self.meta
				if not self.play_random_continual and self.autoplay_nextep and self.autoscrape_nextep:
					self.autoscrape_next_episode = False
				self.exec_task('episode_handler')
			if self.volume_check: kodi_utils.volume_checker()
			while self.isPlayingVideo(): self.check_playback_events()
			if self.playback_error: return False
			if not self.media_marked: self.media_watched_marker()
			ws.clear_local_bookmarks()
			return True
		except: pass

	def check_playback_events(self):
		try:
			kodi_utils.sleep(1000)
			self.total_time, self.curr_time = self.getTotalTime(), self.getTime()
			self.current_point = round(float(self.curr_time/self.total_time * 100), 1)
			self.remaining_time = round(self.total_time - self.curr_time)
			if self.curr_time >= 60: self._record_healthy_play()
			if not self.subs_searched:
				self.exec_task('subtitles')
			if not self.stingers_checked and self.mediatype == 'movie':
				if self.stinger_enabled and self.curr_time > self.stinger_check:
					self.exec_task('stingers')
			if not self.media_marked:
				if self.current_point >= self.set_watched:
					self.media_watched_marker()
			if self.nextep_info_gathered and not self.nextep_started:
				if self.play_random_continual and self.remaining_time <= self.start_prep:
					self.exec_task('random_continual')
				elif self.autoplay_next_episode and self.remaining_time <= self.start_prep:
					if self.autoplay_nextep: self.exec_task('next_ep')
				elif self.autoscrape_next_episode and self.remaining_time <= self.autoscrape_next_window_time:
					if self.autoscrape_nextep: self.exec_task('scrape_next_ep')
		except: pass

	def _playback_health_elapsed(self):
		try: return max(0, monotonic() - float(self.playback_health_started_at))
		except: return None

	def _record_playback_health(self, event, **kwargs):
		context = getattr(self, 'playback_health_context', None)
		if playback_health is None or not context: return
		try: playback_health.record(context, event, **kwargs)
		except: pass

	def _record_healthy_play(self, natural_end=False):
		if getattr(self, 'playback_health_recorded', False) or getattr(self, 'playback_error', False): return
		if not natural_end:
			try:
				if self.getTime() < 60: return
			except: return
		self.playback_health_recorded = True
		self._record_playback_health('healthy_play', elapsed=self._playback_health_elapsed())

	def make_listitem(self):
		listitem = kodi_utils.make_listitem()
		try:
			if self.mediatype == 'movie':
				info = movie_show_infodict(self.meta)
				uids = {'imdb': self.imdb_id, 'tmdb': str(self.tmdb_id)}
			else:
				info = {**episode_infodict(self.meta), 'title': self.meta_get('ep_name')}
				uids = {'imdb': self.imdb_id, 'tmdb': str(self.tmdb_id), 'tvdb': str(self.tvdb_id)}
			if KODI_VERSION < 20:
				listitem.setInfo('video', info)
				listitem.setUniqueIDs(uids)
				listitem.setCast(resized_cast(self.meta_get('cast', [])))
			else:
				infotag = info_tagger(listitem, info)
				infotag.setUniqueIDs(uids)
				infotag.setCast(make_cast_list(resized_cast(self.meta_get('cast', []))))
		except: pass
		return listitem

	def episode_handler(self):
		for _ in range(150):
			total_time = False
			try: total_time = self.getTotalTime() > 0
			except: pass
			if total_time: break
			kodi_utils.sleep(200)
		else: return
		self.intro, self.credits = SegmentScraper(self.imdb_id, self.season, self.episode).run()
		if self.intro is not None and self.skip_intro_enabled:
			self.exec_task('skip_intro')
		if self.play_random_continual or self.autoplay_next_episode or self.autoscrape_next_episode:
			self.info_next_ep()

	def info_next_ep(self):
		try:
			self.nextep_settings = settings.autoplay_next_settings()
			if self.credits is not None and self.credits <= self.getTotalTime():
				window_time = round(self.getTotalTime() - self.credits + 5)
				self.nextep_settings['window_time'] = window_time
				self.nextep_settings['autoscrape_next_window_time'] = window_time
			elif not self.nextep_settings['run_popup']:
				window_time = round(0.02 * self.getTotalTime())
				self.nextep_settings['window_time'] = window_time
			elif self.nextep_settings['timer_method'] == 'percentage':
				percentage = self.nextep_settings['window_percentage']
				window_time = round((percentage/100) * self.getTotalTime())
				self.nextep_settings['window_time'] = window_time
			else:
				window_time = self.nextep_settings['window_time']
			threshold_check = window_time + 21
			self.start_prep = self.nextep_settings['scraper_time'] + threshold_check
			self.nextep_settings.update({'threshold_check': threshold_check, 'start_prep': self.start_prep})
			self.autoscrape_next_window_time = self.nextep_settings['autoscrape_next_window_time']
		except: pass
		finally: self.nextep_info_gathered = True

	def media_watched_marker(self):
		self.media_marked = True
		try:
			if self.current_point >= self.set_watched:
				if self.mediatype == 'movie': watched_params, watched_function = {
					'mode': 'mark_as_watched_unwatched_movie', 'action': 'mark_as_watched',
					'refresh': 'false', 'from_playback': 'true',
					'tmdb_id': self.tmdb_id, 'title': self.title, 'year': self.year
					}, ws.mark_as_watched_unwatched_movie
				else: watched_params, watched_function = {
					'mode': 'mark_as_watched_unwatched_episode', 'action': 'mark_as_watched',
					'refresh': 'false', 'from_playback': 'true',
					'tmdb_id': self.tmdb_id, 'title': self.title, 'year': self.year,
					'tvdb_id': self.tvdb_id, 'season': self.season, 'episode': self.episode
					}, ws.mark_as_watched_unwatched_episode
				return self.exec_task('media_watched', watched_function, watched_params)
			kodi_utils.clear_property('pov_lite_total_autoplays')
			if self.current_point < self.set_resume: return
			self.exec_task(
				'media_bookmark', self.mediatype,
				self.tmdb_id, self.curr_time, self.total_time,
				self.title, self.season, self.episode, 'progress'
			)
		except: pass

	def bookmarkPOV(self):
		bookmark = 0
		bookmarks = ws.get_bookmarks(settings.watched_indicators(), self.mediatype)
		try: resume_point, curr_time, _ = ws.detect_bookmark(bookmarks, self.tmdb_id, self.season, self.episode)
		except: resume_point, curr_time = 0, 0
		resume_check = float(resume_point)
		if resume_check > 0:
			percent = str(resume_point)
			resume_point = sec2time(float(curr_time), n_msec=0)
			bookmark = self.getResumeStatus(resume_point, percent, bookmark)
			if bookmark == 0: ws.erase_bookmark(self.mediatype, self.tmdb_id, self.season, self.episode)
		return bookmark

	def getResumeStatus(self, resume_point, percent, bookmark):
		if settings.auto_resume(self.mediatype): return percent
		choice = open_window(
			('windows.progress', 'ProgressMedia'),
			'progress_media.xml',
			meta=self.meta,
			text=ls(32790) % resume_point,
			enable_buttons=True,
			true_button=ls(32832),
			false_button=ls(32833),
			focus_button=10,
			percent=percent
		)
		return percent if choice is True else bookmark if choice is False else 'cancel'

	def getStingers(self, tmdb_id, poster):
		if not tmdb_id: return
		from indexers import tmdb_api
		stingers = {'duringcreditsstinger': 'During Credit Scene', 'aftercreditsstinger': 'After Credit Scene'}
		keywords = tmdb_api.movie_keywords(tmdb_id) or []
		keywords = [str(i['name']) for i in keywords]
		if all((i in keywords for i in stingers.keys())): stinger = 'Dual Credit Scenes'
		else: stinger = next((v for k, v in stingers.items() if k in keywords), None)
		return kodi_utils.notification(stinger, time=6000, icon=poster) if stinger else ''

	def exec_task(self, task_name, *args):
		try:
			if task_name == 'media_bookmark':
				if args: ws.set_bookmark(*args)
			elif task_name == 'media_watched':
				if args: Thread(target=args[0], args=(args[1],)).start()
			elif task_name == 'episode_handler':
				Thread(target=self.episode_handler).start()
			elif task_name == 'skip_intro':
				from modules.episode_tools import execute_skip_intro
				Thread(target=execute_skip_intro, args=(self, self.meta)).start()
			elif task_name == 'scrape_next_ep':
				self.nextep_started = True
				from modules.episode_tools import execute_scrape_nextep
				Thread(target=execute_scrape_nextep, args=(self, self.meta)).start()
			elif task_name in ('next_ep', 'random_continual'):
				self.nextep_started = True
				from modules.episode_tools import execute_nextep
				Thread(target=execute_nextep, args=(self, self.meta, self.nextep_settings)).start()
			elif task_name == 'subtitles':
				self.subs_searched = True
				poster = self.meta.get('poster') or poster_empty
				season = self.season if self.mediatype == 'episode' else None
				episode = self.episode if self.mediatype == 'episode' else None
				from indexers.subtitles import Subtitles
				Thread(target=Subtitles().run, args=(
					self.title, self.imdb_id, season, episode, poster, self.meta.get('release_name', ''),
					self.meta.get('release_quality', ''), self.meta.get('release_info', ''), self.year
				)).start()
			elif task_name == 'stingers':
				self.stingers_checked = True
				poster = self.meta.get('poster') or poster_empty
				tmdb_id = self.tmdb_id if self.mediatype == 'movie' and self.stinger_enabled else None
				Thread(target=self.getStingers, args=(tmdb_id, poster)).start()
		except: pass
