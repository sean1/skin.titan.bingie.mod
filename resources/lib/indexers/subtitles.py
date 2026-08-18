import json

from modules import kodi_utils


subtitle_file_prefix = 'POVLiteSubs_'
subtitle_languages = ('eng', 'vie')
subtitle_context_property = 'pov_lite_subtitle_context'
subtitle_extensions = ('srt', 'ass', 'ssa', 'vtt', 'sub')


class SubtitleCancelled(Exception):
	pass


def _failure(operation, category, detail=''):
	detail = ' '.join(str(detail).split())[:160]
	message = 'operation=%s outcome=failed category=%s' % (operation, category)
	if detail: message = '%s detail=%s' % (message, detail)
	kodi_utils.logger('BINGIE Lite Subtitles', message)
	return 'Subtitle %s failed' % operation


def _wait(delay, cancelled=None):
	if cancelled and cancelled(): raise SubtitleCancelled
	if kodi_utils.monitor.waitForAbort(delay): raise SubtitleCancelled
	if cancelled and cancelled(): raise SubtitleCancelled


class Subtitles(kodi_utils.xbmc_player):
	def configure(self, imdb_id, season=None, episode=None, poster='', expected_playing_file=None, release_name='', quality='', extra_info='', year=''):
		self.languages = subtitle_languages
		self.imdb_id, self.season, self.episode, self.poster = imdb_id, season, episode, poster
		self.subtitle_path, self.expected_playing_file = 'special://temp/', expected_playing_file
		self.media = {
			'imdb_id': imdb_id, 'season': season, 'episode': episode, 'release_name': release_name or '',
			'quality': quality or '', 'extra_info': extra_info or '', 'year': year or ''
		}
		self.provider_manager = None
		if season not in (None, ''): self.sub_filename = '%s%s_%s_%s' % (subtitle_file_prefix, imdb_id, season, episode)
		else: self.sub_filename = '%s%s' % (subtitle_file_prefix, imdb_id)
		return self

	def _cancelled(self):
		try:
			if kodi_utils.monitor.abortRequested(): return True
		except Exception: pass
		expected_playing_file = getattr(self, 'expected_playing_file', None)
		if expected_playing_file is None: return False
		try:
			if not self.isPlayingVideo(): return True
			return bool(expected_playing_file and self.getPlayingFile() != expected_playing_file)
		except Exception: return True

	def _manager(self):
		if self.provider_manager is None:
			from indexers.subtitle_providers import ProviderManager
			self.provider_manager = ProviderManager(self.media, self._cancelled)
		return self.provider_manager

	def subtitles_search(self):
		try: return self._manager().search()
		except Exception as error:
			from indexers.subtitle_providers import ProviderCancelled
			if isinstance(error, ProviderCancelled): raise SubtitleCancelled
			_failure('search', 'unexpected', type(error).__name__)
			return []

	def download_candidate(self, candidate):
		try: return self._manager().download(candidate)
		except Exception as error:
			from indexers.subtitle_providers import ProviderCancelled
			if isinstance(error, ProviderCancelled): raise SubtitleCancelled
			_failure('download', 'unexpected', type(error).__name__)
			return None

	def download_by_id(self, provider_name, candidate_id):
		try:
			manager = self._manager()
			candidate = manager.find(provider_name, candidate_id)
			return manager.download(candidate) if candidate else None
		except Exception as error:
			from indexers.subtitle_providers import ProviderCancelled
			if isinstance(error, ProviderCancelled): raise SubtitleCancelled
			_failure('download', 'unexpected', type(error).__name__)
			return None

	def save_subtitle(self, payload, final_path):
		try:
			if isinstance(payload, dict): content = payload.get('content')
			else:
				try: content = payload.text
				except Exception: content = payload.content
		except Exception as error:
			_failure('write', 'invalid_response', type(error).__name__)
			return False
		if content in (None, '', b''):
			_failure('write', 'empty_response')
			return False
		opened = False
		try:
			with kodi_utils.open_file(final_path, 'w') as subtitle_file:
				opened = True
				if subtitle_file.write(content) is False: raise OSError('write returned false')
		except Exception as error:
			_failure('write', 'filesystem', type(error).__name__)
			if opened:
				try: kodi_utils.delete_file(final_path)
				except Exception: pass
			return False
		return True

	def _notify_failure(self):
		if self._cancelled(): return False
		kodi_utils.notification(32856, icon=self.poster)
		return False

	def _notify_no_results(self):
		if self._cancelled(): return False
		kodi_utils.notification(32793, icon=self.poster)
		return False

	def _ensure_current_playback(self):
		if self._cancelled(): raise SubtitleCancelled

	@staticmethod
	def _subtitle_stream_language(stream):
		stream = str(stream or '').strip().lower().replace('_', '-')
		language = stream.split('-', 1)[0].strip()
		if language in ('en', 'eng', 'english'): return 'eng'
		if language in ('vi', 'vie', 'vietnamese'): return 'vie'
		if stream.startswith('english'): return 'eng'
		if stream.startswith('vietnamese'): return 'vie'
		return ''

	def _video_file_subs(self):
		self._ensure_current_playback()
		try: available_subtitles = self.getAvailableSubtitleStreams()
		except: available_subtitles = None
		if available_subtitles is not None:
			selected = next((index for language in self.languages for index, stream in enumerate(available_subtitles) if self._subtitle_stream_language(stream) == language), None)
			if selected is None: return False
			self._ensure_current_playback()
			self.setSubtitleStream(selected)
		else:
			try: available_sub_language = self._subtitle_stream_language(self.getSubtitles())
			except: available_sub_language = ''
			if available_sub_language not in self.languages: return False
		self._ensure_current_playback()
		self.showSubtitles(True)
		kodi_utils.notification(32852, icon=self.poster)
		return True

	def _downloaded_subs(self):
		self._ensure_current_playback()
		files = kodi_utils.list_dirs(self.subtitle_path)[1]
		final_match = next((filename for language in self.languages for filename in sorted(files) if self._is_cached_filename(filename, language)), None)
		if not final_match: return False
		subtitle = '%s%s' % (self.subtitle_path, final_match)
		self._ensure_current_playback()
		self.setSubtitles(subtitle)
		kodi_utils.notification(32792, icon=self.poster)
		return True

	def _is_cached_filename(self, filename, language):
		prefix = '%s_%s.' % (self.sub_filename, language)
		return filename.startswith(prefix) and filename.rsplit('.', 1)[-1].lower() in subtitle_extensions

	def _searched_subs(self):
		candidates = self.subtitles_search()
		self._ensure_current_playback()
		self._set_context(candidates)
		if not candidates: return self._notify_no_results()
		for candidate in candidates:
			self._ensure_current_playback()
			payload = self.download_candidate(candidate)
			if not payload: continue
			extension = self._safe_extension(payload.get('extension'))
			if not extension: continue
			final_path = '%s%s' % (self.subtitle_path, self._search_filename(candidate['lang'], extension))
			if not self.save_subtitle(payload, final_path): continue
			self._ensure_current_playback()
			try: self.setSubtitles(final_path)
			except Exception as error:
				_failure('attach', 'runtime', type(error).__name__)
				try: kodi_utils.delete_file(final_path)
				except Exception: pass
				continue
			return True
		return self._notify_failure()

	@staticmethod
	def _safe_extension(extension):
		extension = str(extension or '').lower().lstrip('.')
		return extension if extension in subtitle_extensions else ''

	def _search_filename(self, language, extension='srt'):
		return '%s_%s.%s' % (self.sub_filename, language, extension)

	def _set_context(self, subtitles=None):
		try: playing_file = self.getPlayingFile()
		except: playing_file = ''
		from indexers.subtitle_providers import public_candidate
		context = {
			'imdb_id': self.imdb_id, 'season': self.season, 'episode': self.episode, 'poster': self.poster, 'playing_file': playing_file,
			'release_name': self.media.get('release_name', ''), 'quality': self.media.get('quality', ''), 'extra_info': self.media.get('extra_info', ''), 'year': self.media.get('year', ''),
			'subtitles': [public_candidate(item) for item in subtitles or ()]
		}
		kodi_utils.set_property(subtitle_context_property, json.dumps(context))

	def run(self, query, imdb_id, season, episode, poster, release_name='', quality='', extra_info='', year=''):
		try:
			try: self.expected_playing_file = self.getPlayingFile()
			except Exception: self.expected_playing_file = ''
			self.configure(imdb_id, season, episode, poster, self.expected_playing_file, release_name, quality, extra_info, year)
			self._set_context()
			_wait(2.5, self._cancelled)
			return self._video_file_subs() or self._downloaded_subs() or self._searched_subs()
		except SubtitleCancelled: return False
		except Exception as error:
			_failure('automatic', 'unexpected', type(error).__name__)
			return self._notify_failure()
