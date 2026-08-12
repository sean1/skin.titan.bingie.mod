import json
import requests
from modules import kodi_utils
# logger = kodi_utils.logger

timeout = 20.0
retryable_status_codes = (429, 502, 503, 504)
subtitle_file_prefix = 'POVLiteSubs_'
subtitle_languages = ('eng', 'vie')
subtitle_manifest = 'https://submaker.elfhosted.com/addon/0784f5f345ca13ddd7296af5f0bde65d/manifest.json'
subtitle_context_property = 'pov_lite_subtitle_context'

class SubtitleCancelled(Exception):
	pass

def _failure(operation, category, detail=''):
	detail = ' '.join(str(detail).split())[:160]
	message = 'operation=%s outcome=failed category=%s' % (operation, category)
	if detail: message = '%s detail=%s' % (message, detail)
	kodi_utils.logger('BINGIE Lite Subtitles', message)
	return 'Subtitle %s failed' % operation

def _retry_delay(response):
	if response.status_code != 429: return 1
	try: delay = int(response.headers.get('Retry-After', 10))
	except (AttributeError, TypeError, ValueError): delay = 10
	return min(max(delay, 1), 10)

def _wait_for_retry(delay, cancelled=None):
	if cancelled and cancelled(): raise SubtitleCancelled
	if kodi_utils.monitor.waitForAbort(delay): raise SubtitleCancelled
	if cancelled and cancelled(): raise SubtitleCancelled

def _close_response(response):
	try: response.close()
	except Exception: pass

def _get(url, params=None, stream=False, operation='request', cancelled=None):
	for attempt in (1, 2):
		if cancelled and cancelled(): raise SubtitleCancelled
		try: response = requests.get(url, params=params, stream=stream, timeout=timeout)
		except (requests.Timeout, requests.ConnectionError) as error:
			if attempt == 2: raise
			delay = 1
			if cancelled and cancelled(): raise SubtitleCancelled
			kodi_utils.logger('BINGIE Lite Subtitles', 'operation=%s outcome=retry attempt=%s next_attempt=%s delay=%s category=%s' % (operation, attempt, attempt + 1, delay, type(error).__name__))
			_wait_for_retry(delay, cancelled)
			continue
		if response.status_code not in retryable_status_codes or attempt == 2: return response
		delay = _retry_delay(response)
		if cancelled and cancelled():
			_close_response(response)
			raise SubtitleCancelled
		kodi_utils.logger('BINGIE Lite Subtitles', 'operation=%s outcome=retry attempt=%s next_attempt=%s delay=%s status=%s' % (operation, attempt, attempt + 1, delay, response.status_code))
		_close_response(response)
		_wait_for_retry(delay, cancelled)

def _http_failure(operation, response):
	status = getattr(response, 'status_code', 'unknown')
	reason = getattr(response, 'reason', '') or 'HTTP error'
	result = _failure(operation, 'http', 'status=%s reason=%s' % (status, reason))
	_close_response(response)
	return result

class Subtitles(kodi_utils.xbmc_player):
	def configure(self, imdb_id, season=None, episode=None, poster='', expected_playing_file=None):
		self.manifest, self.languages = subtitle_manifest, subtitle_languages
		self.imdb_id, self.season, self.episode, self.poster = imdb_id, season, episode, poster
		self.subtitle_path, self.expected_playing_file = 'special://temp/', expected_playing_file
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

	def subtitles_download(self, url):
		try: response = _get(url, operation='download', cancelled=self._cancelled)
		except requests.RequestException as error: return _failure('download', 'network', type(error).__name__)
		self._ensure_current_playback()
		return response if response.ok else _http_failure('download', response)

	def subtitles_search(self):
		if self.season not in (None, ''): params = 'subtitles/series/%s:%s:%s' % (self.imdb_id, self.season, self.episode)
		else: params = 'subtitles/movie/%s' % self.imdb_id
		try: response = _get(self.manifest.replace('manifest', params), operation='search', cancelled=self._cancelled)
		except requests.RequestException as error: return _failure('search', 'network', type(error).__name__)
		self._ensure_current_playback()
		if not response.ok: return _http_failure('search', response)
		try: payload = response.json()
		except (TypeError, ValueError) as error:
			return _failure('search', 'invalid_json', type(error).__name__)
		finally: _close_response(response)
		if not isinstance(payload, dict) or not isinstance(payload.get('subtitles'), list): return _failure('search', 'invalid_schema')
		subtitles = payload['subtitles']
		valid_subtitles = [item for item in subtitles if isinstance(item, dict)]
		if subtitles and not valid_subtitles: return _failure('search', 'invalid_schema')
		return valid_subtitles

	def save_subtitle(self, response, final_path):
		try:
			try: content = response.text
			except Exception: content = response.content
		except Exception as error:
			_failure('write', 'invalid_response', type(error).__name__)
			return False
		if content in (None, '', b''):
			_failure('write', 'empty_response')
			return False
		opened = False
		try:
			with kodi_utils.open_file(final_path, 'w') as file:
				opened = True
				if file.write(content) is False: raise OSError('write returned false')
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

	def _video_file_subs(self):
		self._ensure_current_playback()
		try: available_sub_language = self.getSubtitles()
		except: available_sub_language = ''
		if available_sub_language not in self.languages: return False
		self._ensure_current_playback()
		self.showSubtitles(True)
		kodi_utils.notification(32852, icon=self.poster)
		return True

	def _downloaded_subs(self):
		self._ensure_current_playback()
		files = kodi_utils.list_dirs(self.subtitle_path)[1]
		final_match = next((filename for language in self.languages for filename in files if filename == self._search_filename(language)), None)
		if not final_match: return False
		subtitle = '%s%s' % (self.subtitle_path, final_match)
		self._ensure_current_playback()
		self.setSubtitles(subtitle)
		kodi_utils.notification(32792, icon=self.poster)
		return True

	def _searched_subs(self):
		subs = self.subtitles_search()
		if isinstance(subs, str): return self._notify_failure()
		self._ensure_current_playback()
		self._set_context(subs)
		if not subs: return self._notify_no_results()
		chosen_sub = next((item for language in self.languages for item in subs if item.get('lang') == language and item.get('url')), None)
		if not chosen_sub: return self._notify_no_results()
		response = self.subtitles_download(chosen_sub['url'])
		if isinstance(response, str): return self._notify_failure()
		if self._cancelled(): return False
		final_path = '%s%s' % (self.subtitle_path, self._search_filename(chosen_sub['lang']))
		if not self.save_subtitle(response, final_path): return self._notify_failure()
		if self._cancelled(): return False
		try: self.setSubtitles(final_path)
		except Exception as error:
			_failure('attach', 'runtime', type(error).__name__)
			return self._notify_failure()
		return True

	def _search_filename(self, language):
		return '%s_%s.srt' % (self.sub_filename, language)

	def _set_context(self, subtitles=None):
		try: playing_file = self.getPlayingFile()
		except: playing_file = ''
		context = {
			'imdb_id': self.imdb_id, 'season': self.season, 'episode': self.episode, 'poster': self.poster, 'playing_file': playing_file,
			'subtitles': [{key: item.get(key, '') for key in ('id', 'lang', 'url')} for item in subtitles or () if item.get('url')]
		}
		kodi_utils.set_property(subtitle_context_property, json.dumps(context))

	def run(self, query, imdb_id, season, episode, poster):
		try:
			try: self.expected_playing_file = self.getPlayingFile()
			except Exception: self.expected_playing_file = ''
			self.configure(imdb_id, season, episode, poster, self.expected_playing_file)
			self._set_context()
			_wait_for_retry(2.5, self._cancelled)
			return self._video_file_subs() or self._downloaded_subs() or self._searched_subs()
		except SubtitleCancelled: return False
		except Exception as error:
			_failure('automatic', 'unexpected', type(error).__name__)
			return self._notify_failure()
