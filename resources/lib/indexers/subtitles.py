import hashlib
import json
import re
import secrets
from urllib.parse import urlsplit, urlunsplit

from modules import kodi_utils


subtitle_file_prefix = 'POVLiteSubs_'
subtitle_languages = ('eng', 'vie')
subtitle_context_property = 'pov_lite_subtitle_context'
subtitle_context_version = 2
subtitle_context_max_bytes = 64 * 1024
subtitle_context_max_candidates = 100
subtitle_extensions = ('srt', 'ass', 'ssa', 'vtt', 'sub')
subtitle_providers = ('opensubtitles', 'subdl', 'subsource')


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


def _clean_identifier(value):
	value = str(value or '').strip()
	return '' if not value or value.lower() == 'none' or '://' in value else value


def _normalized_text(value):
	return ' '.join(re.findall(r'[a-z0-9]+', str(value or '').lower()))


def playing_file_fingerprint(playing_file):
	playing_file = str(playing_file or '')
	return hashlib.sha256(playing_file.encode('utf-8', 'surrogatepass')).hexdigest() if playing_file else ''


def stable_media_identity(imdb_id='', tmdb_id='', mediatype='', title='', year='', season=None, episode=None):
	imdb_id, tmdb_id = _clean_identifier(imdb_id).lower(), _clean_identifier(tmdb_id)
	mediatype = str(mediatype or ('episode' if season not in (None, '') else 'movie')).strip().lower()
	if imdb_id: base = 'imdb:%s' % imdb_id
	elif tmdb_id and mediatype in ('movie', 'episode', 'tvshow'): base = '%s:tmdb:%s' % (mediatype, tmdb_id)
	else:
		metadata = '|'.join((_normalized_text(mediatype), _normalized_text(title), _normalized_text(year)))
		if not _normalized_text(title): return ''
		base = 'meta:%s' % hashlib.sha256(metadata.encode('utf-8')).hexdigest()
	if season not in (None, ''): base = '%s:s%s:e%s' % (base, season, episode)
	return base


def _cache_token(identity):
	if identity.startswith('imdb:'):
		value = re.sub(r'[^a-zA-Z0-9_-]', '', identity.split(':', 2)[1])
		if value: return value
	return hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32] if identity else ''


def _safe_context_string(value, limit):
	value = ' '.join(str(value or '').split())[:limit]
	return '' if '://' in value else value


def _safe_poster(value):
	value = str(value or '').strip()
	if not value or any(character in value for character in ('\r', '\n')): return ''
	if '://' not in value: return value[:1024]
	try:
		parsed = urlsplit(value)
		if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password: return ''
		return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))[:1024]
	except Exception: return ''


def _candidate_projection(candidate, generation, index):
	if not isinstance(candidate, dict): return None
	provider, candidate_id = str(candidate.get('provider') or ''), str(candidate.get('id') or '')
	language = str(candidate.get('lang') or '')
	if provider not in subtitle_providers or language not in subtitle_languages or not candidate_id or len(candidate_id) > 256 or '://' in candidate_id or any(character in candidate_id for character in ('\r', '\n')): return None
	extension = str(candidate.get('extension') or '').lower().lstrip('.')
	result = {
		'token': hashlib.sha256(('%s\0%s\0%s\0%s' % (generation, provider, candidate_id, index)).encode('utf-8')).hexdigest()[:24],
		'provider': provider, 'id': candidate_id, 'lang': language
	}
	if extension in subtitle_extensions or extension == 'zip': result['extension'] = extension
	release_names = candidate.get('release_names')
	release = next((str(value).strip() for value in release_names or () if isinstance(value, str) and value.strip()), '') if isinstance(release_names, (list, tuple)) else ''
	release = release or (str(candidate.get('release')).strip() if isinstance(candidate.get('release'), str) else '')
	if release and '://' not in release: result['release'] = release[:180]
	for key in ('score', 'rating'):
		value = candidate.get(key)
		if isinstance(value, (int, float)) and not isinstance(value, bool): result[key] = value
	if candidate.get('sync') is True: result['sync'] = True
	return result


def _context_candidate_order(candidates):
	buckets = {language: [] for language in subtitle_languages}
	for candidate in candidates or ():
		if isinstance(candidate, dict) and candidate.get('lang') in buckets: buckets[candidate['lang']].append(candidate)
	for index in range(max((len(bucket) for bucket in buckets.values()), default=0)):
		for language in subtitle_languages:
			if index < len(buckets[language]): yield buckets[language][index]


class Subtitles(kodi_utils.xbmc_player):
	def configure(self, imdb_id, season=None, episode=None, poster='', expected_playing_file=None, release_name='', quality='', extra_info='', year='', tmdb_id='', mediatype='', title='', context_generation=''):
		self.languages = subtitle_languages
		self.imdb_id, self.tmdb_id, self.season, self.episode, self.poster = imdb_id, tmdb_id, season, episode, poster
		self.mediatype, self.title = mediatype or ('episode' if season not in (None, '') else 'movie'), title or ''
		self.context_generation = context_generation or secrets.token_hex(16)
		self.subtitle_path, self.expected_playing_file = 'special://temp/', expected_playing_file
		self.media = {
			'imdb_id': imdb_id, 'season': season, 'episode': episode, 'release_name': release_name or '',
			'quality': quality or '', 'extra_info': extra_info or '', 'year': year or '', 'tmdb_id': tmdb_id or '', 'mediatype': self.mediatype, 'title': self.title
		}
		self.provider_manager = None
		self.media_identity = stable_media_identity(imdb_id, tmdb_id, self.mediatype, self.title, year, season, episode)
		cache_token = _cache_token(self.media_identity) or 'session_%s' % self.context_generation
		if season not in (None, ''): self.sub_filename = '%s%s_%s_%s' % (subtitle_file_prefix, cache_token, season, episode)
		else: self.sub_filename = '%s%s' % (subtitle_file_prefix, cache_token)
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

	def subtitle_diagnostics(self):
		manager = self.provider_manager
		return manager.diagnostics() if manager and hasattr(manager, 'diagnostics') else {}

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
		context = {
			'version': subtitle_context_version, 'generation': self.context_generation, 'media_identity': self.media_identity, 'playing_fingerprint': playing_file_fingerprint(playing_file),
			'imdb_id': _clean_identifier(self.imdb_id), 'tmdb_id': _clean_identifier(self.tmdb_id), 'mediatype': self.mediatype, 'title': _safe_context_string(self.title, 180), 'poster': _safe_poster(self.poster),
			'season': self.season, 'episode': self.episode, 'release_name': _safe_context_string(self.media.get('release_name', ''), 300),
			'quality': _safe_context_string(self.media.get('quality', ''), 80), 'extra_info': _safe_context_string(self.media.get('extra_info', ''), 300), 'year': self.media.get('year', ''), 'subtitles': []
		}
		for index, candidate in enumerate(_context_candidate_order(subtitles)):
			projection = _candidate_projection(candidate, self.context_generation, index)
			if not projection: continue
			context['subtitles'].append(projection)
			if len(json.dumps(context, separators=(',', ':')).encode('utf-8')) > subtitle_context_max_bytes:
				context['subtitles'].pop()
				break
			if len(context['subtitles']) >= subtitle_context_max_candidates: break
		payload = json.dumps(context, separators=(',', ':'))
		kodi_utils.set_property(subtitle_context_property, payload)
		return context

	def run(self, query, imdb_id, season, episode, poster, release_name='', quality='', extra_info='', year='', tmdb_id='', mediatype='', identity_title=''):
		try:
			try: self.expected_playing_file = self.getPlayingFile()
			except Exception: self.expected_playing_file = ''
			self.configure(imdb_id, season, episode, poster, self.expected_playing_file, release_name, quality, extra_info, year, tmdb_id, mediatype, identity_title or query)
			self._set_context()
			_wait(2.5, self._cancelled)
			return self._video_file_subs() or self._downloaded_subs() or self._searched_subs()
		except SubtitleCancelled: return False
		except Exception as error:
			_failure('automatic', 'unexpected', type(error).__name__)
			return self._notify_failure()
