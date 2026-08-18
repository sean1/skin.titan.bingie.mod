import io
import json
import math
import os
import re
import stat
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import PurePosixPath
from threading import Lock

import requests
from modules import kodi_utils


CONFIG_PATH = 'resources/private/subtitle_providers.json'
LANGUAGES = ('eng', 'vie')
LANGUAGE_CODES = {'en': 'eng', 'eng': 'eng', 'english': 'eng', 'vi': 'vie', 'vie': 'vie', 'vietnamese': 'vie'}
SUPPORTED_EXTENSIONS = ('.srt', '.ass', '.ssa', '.vtt', '.sub')
RETRYABLE_STATUS_CODES = (429, 502, 503, 504)
REQUEST_TIMEOUT = 20.0
MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_SUBTITLE_BYTES = 5 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 100
MAX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
MAX_COMPRESSION_RATIO = 250


class ProviderError(Exception):
	pass


class ProviderCancelled(Exception):
	pass


def _log(operation, outcome, provider='all', category=''):
	message = 'provider=%s operation=%s outcome=%s' % (provider, operation, outcome)
	if category: message = '%s category=%s' % (message, category)
	kodi_utils.logger('BINGIE Lite Subtitles', message)


def _normalize_language(language):
	return LANGUAGE_CODES.get(str(language or '').strip().lower(), '')


def _as_int(value):
	try: return int(value)
	except (TypeError, ValueError): return None


def _as_float(value):
	try: return float(value)
	except (TypeError, ValueError): return None


def _as_bool(value):
	return value if isinstance(value, bool) else None


def _read_config(path):
	with kodi_utils.open_file(path) as config_file: return config_file.readBytes().decode('utf-8-sig')


def load_provider_config(path=None):
	path = path or '%s%s' % (kodi_utils.addon_path, CONFIG_PATH)
	try:
		if not kodi_utils.path_exists(path):
			_log('config', 'disabled', category='missing')
			return {}
		if os.name == 'posix':
			if os.path.islink(path) or not os.path.isfile(path) or stat.S_IMODE(os.stat(path).st_mode) != 0o600:
				_log('config', 'disabled', category='insecure_file')
				return {}
		payload = json.loads(_read_config(path))
	except (OSError, UnicodeError):
		_log('config', 'disabled', category='unreadable')
		return {}
	except (TypeError, ValueError):
		_log('config', 'disabled', category='invalid_json')
		return {}
	if not isinstance(payload, dict):
		_log('config', 'disabled', category='invalid_schema')
		return {}
	config = {}
	for provider in ('opensubtitles', 'subdl', 'subsource'):
		entry = payload.get(provider)
		if not isinstance(entry, dict): continue
		api_key = entry.get('api_key')
		if not isinstance(api_key, str) or not api_key.strip() or api_key.startswith('YOUR_'): continue
		clean = {'api_key': api_key.strip()}
		if provider == 'opensubtitles':
			user_agent = entry.get('user_agent')
			if not isinstance(user_agent, str) or not user_agent.strip(): continue
			clean['user_agent'] = user_agent.strip()
			username, password = entry.get('username'), entry.get('password')
			if all(isinstance(value, str) and value.strip() and not value.startswith('YOUR_') for value in (username, password)):
				clean.update({'username': username.strip(), 'password': password})
		config[provider] = clean
	return config


def _cancelled(cancelled):
	if cancelled and cancelled(): raise ProviderCancelled


def _request(method, url, provider, cancelled=None, **kwargs):
	for attempt in (1, 2):
		_cancelled(cancelled)
		try: response = requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
		except (requests.Timeout, requests.ConnectionError) as error:
			if attempt == 2: raise ProviderError(type(error).__name__)
			_log(method.lower(), 'retry', provider, type(error).__name__)
			if kodi_utils.monitor.waitForAbort(1): raise ProviderCancelled
			continue
		if cancelled and cancelled():
			try: response.close()
			except Exception: pass
			raise ProviderCancelled
		if response.status_code not in RETRYABLE_STATUS_CODES or attempt == 2: return response
		try: retry_after = int(response.headers.get('Retry-After', 1)) if response.status_code == 429 else 1
		except (AttributeError, TypeError, ValueError): retry_after = 1
		retry_after = min(max(retry_after, 1), 10)
		try: response.close()
		except Exception: pass
		_log(method.lower(), 'retry', provider, 'http_%s' % response.status_code)
		if kodi_utils.monitor.waitForAbort(retry_after): raise ProviderCancelled
	raise ProviderError('request_failed')


def _json_response(response, provider, operation):
	try:
		if not response.ok: raise ProviderError('http_%s' % response.status_code)
		payload = response.json()
		if not isinstance(payload, dict): raise ProviderError('invalid_schema')
		return payload
	except (TypeError, ValueError): raise ProviderError('invalid_json')
	finally:
		try: response.close()
		except Exception: pass


def _candidate(provider, candidate_id, language, release_names=(), **kwargs):
	language = _normalize_language(language)
	if not candidate_id or language not in LANGUAGES: return None
	names = [str(name).strip() for name in release_names if str(name or '').strip()]
	result = {
		'provider': provider, 'id': str(candidate_id), 'lang': language, 'release_names': names,
		'fps': _as_float(kwargs.pop('fps', None)), 'season': _as_int(kwargs.pop('season', None)), 'episode': _as_int(kwargs.pop('episode', None)),
		'hearing_impaired': _as_bool(kwargs.pop('hearing_impaired', None)), 'foreign_parts_only': _as_bool(kwargs.pop('foreign_parts_only', None)),
		'forced': _as_bool(kwargs.pop('forced', None)), 'machine_translated': _as_bool(kwargs.pop('machine_translated', None)), 'trusted': _as_bool(kwargs.pop('trusted', None)),
		'rating': _as_float(kwargs.pop('rating', None)), 'downloads': _as_int(kwargs.pop('downloads', None)),
		'match_score': _as_float(kwargs.pop('match_score', None)), 'hash_match': _as_bool(kwargs.pop('hash_match', None)),
		'full_season': _as_bool(kwargs.pop('full_season', None)),
	}
	result.update(kwargs)
	return result


class Provider:
	name = ''

	def __init__(self, config, media, cancelled=None):
		self.config, self.media, self.cancelled = config, media, cancelled

	def request_json(self, method, url, operation, **kwargs):
		try: return _json_response(_request(method, url, self.name, self.cancelled, **kwargs), self.name, operation)
		except ProviderCancelled: raise
		except ProviderError as error:
			_log(operation, 'failed', self.name, str(error))
			raise

	def search(self):
		raise NotImplementedError

	def download(self, candidate):
		raise NotImplementedError


class OpenSubtitlesProvider(Provider):
	name = 'opensubtitles'
	api_url = 'https://api.opensubtitles.com/api/v1'
	_session, _session_lock = {}, Lock()

	def __init__(self, config, media, cancelled=None):
		super().__init__(config, media, cancelled)
		self.base_url, self.token, self.download_disabled = self.api_url, '', False

	def headers(self, authenticated=False):
		headers = {'Api-Key': self.config['api_key'], 'User-Agent': self.config['user_agent'], 'Accept': 'application/json'}
		if authenticated and self.token: headers['Authorization'] = 'Bearer %s' % self.token
		return headers

	def login(self, force=False):
		if not self.config.get('username') or not self.config.get('password'): return
		with self._session_lock:
			if not force and self._session.get('username') == self.config['username'] and self._session.get('api_key') == self.config['api_key'] and self._session.get('token'):
				self.token, self.base_url = self._session['token'], self._session['base_url']
				return
			payload = self.request_json('POST', '%s/login' % self.api_url, 'login', headers=self.headers(), json={'username': self.config['username'], 'password': self.config['password']})
			if not payload.get('token'): raise ProviderError('authentication')
			self.token = payload['token']
			base_url = str(payload.get('base_url') or '').rstrip('/')
			if base_url:
				if not base_url.startswith('https://'): base_url = 'https://%s' % base_url
				self.base_url = base_url if base_url.endswith('/api/v1') else '%s/api/v1' % base_url
			type(self)._session = {'username': self.config['username'], 'api_key': self.config['api_key'], 'token': self.token, 'base_url': self.base_url}

	def authenticated_json(self, method, endpoint, operation, **kwargs):
		for attempt in (1, 2):
			try: return self.request_json(method, '%s/%s' % (self.base_url, endpoint.lstrip('/')), operation, headers=self.headers(True), **kwargs)
			except ProviderError as error:
				if attempt == 2 or str(error) != 'http_401' or not self.config.get('username'): raise
				self.login(force=True)

	def search(self):
		try: self.login()
		except ProviderError: return []
		imdb_id = str(self.media.get('imdb_id') or '').lower().replace('tt', '', 1)
		params = {'languages': 'en,vi'}
		if self.media.get('season') is None: params['imdb_id'] = imdb_id
		else: params.update({'parent_imdb_id': imdb_id, 'season_number': self.media['season'], 'episode_number': self.media['episode']})
		try: payload = self.authenticated_json('GET', 'subtitles', 'search', params=params)
		except ProviderError: return []
		results = []
		for item in payload.get('data') or ():
			if not isinstance(item, dict): continue
			attributes = item.get('attributes')
			if not isinstance(attributes, dict): continue
			feature = attributes.get('feature_details') if isinstance(attributes.get('feature_details'), dict) else {}
			files = attributes.get('files') if isinstance(attributes.get('files'), list) else []
			for subtitle_file in files:
				if not isinstance(subtitle_file, dict): continue
				candidate = _candidate(
					self.name, subtitle_file.get('file_id'), attributes.get('language'),
					(attributes.get('release'), subtitle_file.get('file_name')), fps=attributes.get('fps'),
					season=feature.get('season_number'), episode=feature.get('episode_number'), hearing_impaired=attributes.get('hearing_impaired'), foreign_parts_only=attributes.get('foreign_parts_only'),
					machine_translated=attributes.get('machine_translated') or attributes.get('ai_translated'), trusted=attributes.get('from_trusted'),
					rating=attributes.get('ratings'), downloads=attributes.get('download_count'), hash_match=attributes.get('moviehash_match'),
					extension=PurePosixPath(str(subtitle_file.get('file_name') or '')).suffix.lower().lstrip('.') or 'srt'
				)
				if candidate: results.append(candidate)
		return results

	def download(self, candidate):
		if self.download_disabled: return None
		try:
			payload = self.authenticated_json('POST', 'download', 'download', json={'file_id': int(candidate['id'])})
			link = payload.get('link')
			if not isinstance(link, str) or not link.startswith('https://'): raise ProviderError('invalid_schema')
			response = _request('GET', link, self.name, self.cancelled)
			if not response.ok: raise ProviderError('http_%s' % response.status_code)
			content = response.content
			response.close()
			if not content or len(content) > MAX_SUBTITLE_BYTES: raise ProviderError('invalid_size')
			return {'content': content, 'extension': candidate.get('extension') or 'srt'}
		except ProviderError as error:
			if str(error) == 'http_406': self.download_disabled = True
			return None


class SubDLProvider(Provider):
	name = 'subdl'
	api_url = 'https://api.subdl.com/api/v2/subtitles/search'
	file_api_url = 'https://api.subdl.com/api/v2/files/search'
	download_url = 'https://dl.subdl.com'

	def headers(self):
		return {'Authorization': 'Bearer %s' % self.config['api_key'], 'Accept': 'application/json'}

	def search(self):
		results = self._filename_search() if self.media.get('release_name') else []
		results.extend(self._id_search())
		merged = {}
		for candidate in results:
			release = next(iter(candidate.get('release_names') or ()), '')
			release_key = ' '.join(sorted(_release_tokens(release))) or candidate['id']
			signature = (candidate['lang'], release_key, candidate.get('hearing_impaired'))
			current = merged.get(signature)
			if current is None:
				merged[signature] = candidate
				continue
			preferred = candidate if candidate['id'].startswith('file:') and not current['id'].startswith('file:') else current
			preferred['match_score'] = max(current.get('match_score') or 0.0, candidate.get('match_score') or 0.0)
			merged[signature] = preferred
		return list(merged.values())

	def _filename_search(self):
		params = {'filename': self.media['release_name'], 'type': 'tv' if self.media.get('season') is not None else 'movie', 'languages': 'en,vi', 'subs_per_page': 30}
		if self.media.get('season') is not None: params['episode_scope'] = 'exact'
		try: payload = self.request_json('GET', self.file_api_url, 'search', headers=self.headers(), params=params)
		except ProviderError: return []
		match = payload.get('match') if isinstance(payload.get('match'), dict) else {}
		requested_imdb = str(self.media.get('imdb_id') or '').lower()
		matched_imdb = str(match.get('imdb_id') or '').lower()
		if match.get('degraded') or matched_imdb and requested_imdb and matched_imdb != requested_imdb: return []
		results = []
		for item in payload.get('subtitles') or ():
			if not isinstance(item, dict): continue
			path = item.get('url')
			if not isinstance(path, str) or not path.startswith('/subtitle/') or '://' in path: continue
			archive_id = PurePosixPath(path).name
			if not archive_id or '/' in archive_id or '\\' in archive_id: continue
			candidate = _candidate(
				self.name, 'archive:%s' % archive_id, item.get('lang') or item.get('language'), (item.get('release_name'), item.get('name')),
				fps=item.get('fps'), season=item.get('season'), episode=item.get('episode'), hearing_impaired=item.get('hi'), full_season=item.get('full_season'),
				match_score=item.get('match_score'), extension='zip'
			)
			if candidate: results.append(candidate)
		return results

	def _id_search(self):
		params = {'imdb_id': self.media.get('imdb_id'), 'type': 'tv' if self.media.get('season') is not None else 'movie', 'languages': 'en,vi', 'unpack': 1}
		if self.media.get('season') is not None: params.update({'season_number': self.media['season'], 'episode_number': self.media['episode']})
		try: payload = self.request_json('GET', self.api_url, 'search', headers=self.headers(), params=params)
		except ProviderError: return []
		results = []
		for parent in payload.get('subtitles') or ():
			if not isinstance(parent, dict): continue
			files = parent.get('unpack_files') if isinstance(parent.get('unpack_files'), list) else []
			for subtitle_file in files:
				if not isinstance(subtitle_file, dict): continue
				season, episode = subtitle_file.get('season'), subtitle_file.get('episode')
				if self.media.get('season') is not None and (_as_int(season), _as_int(episode)) != (_as_int(self.media['season']), _as_int(self.media['episode'])): continue
				path = subtitle_file.get('url')
				if not isinstance(path, str) or not path.startswith('/') or '://' in path: continue
				parts = PurePosixPath(path).parts
				if len(parts) < 4 or parts[-1] != str(subtitle_file.get('file_n_id')): continue
				n_id, file_n_id = parts[-2], parts[-1]
				candidate = _candidate(
					self.name, 'file:%s:%s' % (n_id, file_n_id), subtitle_file.get('language') or parent.get('lang'),
					(subtitle_file.get('release_name'), subtitle_file.get('name'), parent.get('release_name'), parent.get('name')),
					fps=parent.get('fps'), season=season, episode=episode, hearing_impaired=subtitle_file.get('hi', parent.get('hi')),
					full_season=parent.get('full_season'),
					extension=str(subtitle_file.get('format') or PurePosixPath(str(subtitle_file.get('name') or '')).suffix.lstrip('.') or 'srt').lower()
				)
				if candidate: results.append(candidate)
		return results

	def download(self, candidate):
		candidate_id = str(candidate.get('id') or '')
		if candidate_id.startswith('file:'):
			parts = candidate_id.split(':', 2)[1:]
			if len(parts) != 2 or not all(parts) or any('/' in part or '\\' in part for part in parts): return None
			locator, archive = '/subtitle/%s/%s' % tuple(parts), False
		elif candidate_id.startswith('archive:'):
			archive_id = candidate_id.split(':', 1)[1]
			if not archive_id or '/' in archive_id or '\\' in archive_id: return None
			locator, archive = '/subtitle/%s' % archive_id, True
		else: return None
		try:
			response = _request('GET', '%s%s' % (self.download_url, locator), self.name, self.cancelled, headers={'X-API-Key': self.config['api_key']})
			if not response.ok: raise ProviderError('http_%s' % response.status_code)
			content = response.content
			response.close()
			if not content: raise ProviderError('empty_response')
			if archive:
				payload = extract_subtitle_archive(content, self.media.get('season'), self.media.get('episode'), self.media.get('release_name', ''))
				if not payload: raise ProviderError('invalid_archive')
				return payload
			if len(content) > MAX_SUBTITLE_BYTES: raise ProviderError('invalid_size')
			return {'content': content, 'extension': candidate.get('extension') or 'srt'}
		except ProviderError as error:
			_log('download', 'failed', self.name, str(error))
			return None


def _episode_matches(value, season, episode):
	value, season, episode = str(value or ''), int(season), int(episode)
	patterns = (
		re.compile(r'(?i)s0?(\d+)[ ._-]*e0?(\d+)(?:[ ._]*(?:-|to)[ ._]*e?0?(\d+)|[ ._-]*e0?(\d+))?'),
		re.compile(r'(?i)(\d+)x0?(\d+)(?:[ ._]*(?:-|to)[ ._]*0?(\d+))?')
	)
	for pattern in patterns:
		for match in pattern.finditer(value):
			if int(match.group(1)) != season: continue
			start = int(match.group(2))
			ends = [int(group) for group in match.groups()[2:] if group is not None]
			if episode == start or ends and min(start, ends[0]) <= episode <= max(start, ends[0]): return True
	return False


def _safe_archive_name(name):
	name = str(name or '').replace('\\', '/')
	path = PurePosixPath(name)
	return bool(name) and not path.is_absolute() and '..' not in path.parts and not re.match(r'^[a-zA-Z]:', name)


def extract_subtitle_archive(content, season=None, episode=None, release_name=''):
	if not isinstance(content, bytes) or not content or len(content) > MAX_ARCHIVE_BYTES: return None
	try:
		with zipfile.ZipFile(io.BytesIO(content)) as archive:
			infos = [info for info in archive.infolist() if not info.is_dir()]
			if not infos or len(infos) > MAX_ARCHIVE_ENTRIES: return None
			if any(not _safe_archive_name(info.filename) or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 0x1 for info in infos): return None
			total_size = sum(info.file_size for info in infos)
			compressed_size = sum(max(info.compress_size, 1) for info in infos)
			if total_size > MAX_UNCOMPRESSED_BYTES or total_size / compressed_size > MAX_COMPRESSION_RATIO: return None
			eligible = [info for info in infos if PurePosixPath(info.filename.replace('\\', '/')).suffix.lower() in SUPPORTED_EXTENSIONS]
			if season is not None:
				matching = [info for info in eligible if _episode_matches(info.filename, season, episode)]
				if matching: eligible = matching
				elif len(eligible) != 1: return None
			if not eligible: return None
			eligible.sort(key=lambda info: (
				-release_match_score(release_name, (PurePosixPath(info.filename.replace('\\', '/')).name,)),
				SUPPORTED_EXTENSIONS.index(PurePosixPath(info.filename.replace('\\', '/')).suffix.lower()), -info.file_size, info.filename.lower()
			))
			selected = eligible[0]
			if selected.file_size > MAX_SUBTITLE_BYTES: return None
			selected_content = archive.read(selected)
			if not selected_content or len(selected_content) > MAX_SUBTITLE_BYTES: return None
			return {'content': selected_content, 'extension': PurePosixPath(selected.filename.replace('\\', '/')).suffix.lower().lstrip('.')}
	except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile): return None


class SubSourceProvider(Provider):
	name = 'subsource'
	api_url = 'https://api.subsource.net/api/v1'

	def headers(self):
		return {'X-API-Key': self.config['api_key'], 'Accept': 'application/json'}

	@staticmethod
	def _items(payload):
		for key in ('data', 'results', 'subtitles'):
			items = payload.get(key)
			if isinstance(items, list): return [item for item in items if isinstance(item, dict)]
		for value in payload.values():
			if isinstance(value, dict):
				items = SubSourceProvider._items(value)
				if items: return items
		return []

	def _movie_id(self):
		params = {'searchType': 'imdb', 'imdb': self.media.get('imdb_id'), 'type': 'tv' if self.media.get('season') is not None else 'movie'}
		if self.media.get('season') is not None: params['season'] = self.media['season']
		payload = self.request_json('GET', '%s/movies/search' % self.api_url, 'lookup', headers=self.headers(), params=params)
		for item in self._items(payload):
			if item.get('movieId') is not None: return item['movieId']
		raise ProviderError('not_found')

	def search(self):
		try: movie_id = self._movie_id()
		except ProviderError: return []
		results = []
		for provider_language in ('english', 'vietnamese'):
			try: payload = self.request_json('GET', '%s/subtitles' % self.api_url, 'search', headers=self.headers(), params={'movieId': movie_id, 'language': provider_language, 'limit': 100})
			except ProviderError: continue
			for item in self._items(payload):
				releases = item.get('releaseInfo')
				if not isinstance(releases, list): releases = [releases] if releases else []
				if self.media.get('season') is not None and not any(_episode_matches(release, self.media['season'], self.media['episode']) for release in releases): continue
				production_type = str(item.get('productionType') or '').lower()
				candidate = _candidate(
					self.name, item.get('subtitleId'), item.get('language') or provider_language, releases,
					fps=item.get('framerate'), season=self.media.get('season'), episode=self.media.get('episode'), hearing_impaired=item.get('hearingImpaired'),
					foreign_parts_only=item.get('foreignParts'), forced=production_type == 'forced', machine_translated=True if 'machine' in production_type or 'ai' in production_type else None,
					rating=self._rating(item.get('rating')), downloads=item.get('downloads'), production_type=item.get('productionType'), release_type=item.get('releaseType')
				)
				if candidate: results.append(candidate)
		return results

	@staticmethod
	def _rating(rating):
		if not isinstance(rating, dict): return _as_float(rating)
		good, bad = _as_int(rating.get('good')) or 0, _as_int(rating.get('bad')) or 0
		return round(10 * good / float(good + bad), 3) if good + bad else None

	def download(self, candidate):
		try:
			response = _request('GET', '%s/subtitles/%s/download' % (self.api_url, candidate['id']), self.name, self.cancelled, headers=self.headers())
			if not response.ok: raise ProviderError('http_%s' % response.status_code)
			content = response.content
			response.close()
			payload = extract_subtitle_archive(content, self.media.get('season'), self.media.get('episode'), self.media.get('release_name', ''))
			if not payload: raise ProviderError('invalid_archive')
			return payload
		except ProviderError as error:
			_log('download', 'failed', self.name, str(error))
			return None


PROVIDER_CLASSES = {'opensubtitles': OpenSubtitlesProvider, 'subdl': SubDLProvider, 'subsource': SubSourceProvider}


def _release_tokens(value):
	return set(re.findall(r'[a-z0-9]+', str(value or '').lower()))


def release_match_score(release_name, candidate_names):
	source_tokens = _release_tokens(release_name)
	if not source_tokens: return 0.0
	best = 0.0
	for candidate_name in candidate_names or ():
		candidate_tokens = _release_tokens(candidate_name)
		if not candidate_tokens: continue
		union = source_tokens | candidate_tokens
		score = len(source_tokens & candidate_tokens) / float(len(union)) if union else 0.0
		if source_tokens == candidate_tokens: score = 1.0
		best = max(best, score)
	return best


def candidate_score(candidate, media):
	score = release_match_score(media.get('release_name'), candidate.get('release_names')) * 100
	score += min(max(candidate.get('match_score') or 0.0, 0.0), 1.0) * 40
	if candidate.get('hash_match'): score += 150
	if media.get('season') is not None and (candidate.get('season'), candidate.get('episode')) == (_as_int(media['season']), _as_int(media['episode'])): score += 30
	if candidate.get('trusted') is True: score += 8
	if candidate.get('machine_translated') is False: score += 4
	elif candidate.get('machine_translated') is True: score -= 4
	if candidate.get('hearing_impaired') is False: score += 2
	if candidate.get('rating') is not None: score += min(max(candidate['rating'], 0.0), 10.0) / 2
	if candidate.get('downloads') is not None: score += min(math.log10(max(candidate['downloads'], 1)), 5.0)
	return round(score, 6)


def rank_candidates(candidates, media):
	valid = []
	for candidate in candidates:
		if not isinstance(candidate, dict) or candidate.get('lang') not in LANGUAGES: continue
		if media.get('season') is not None and candidate.get('episode') is not None:
			if (candidate.get('season'), candidate.get('episode')) != (_as_int(media['season']), _as_int(media['episode'])): continue
		item = candidate.copy()
		item['score'] = candidate_score(item, media)
		valid.append(item)
	return sorted(valid, key=lambda item: (LANGUAGES.index(item['lang']), -item['score'], item['provider'], item['id']))


def public_candidate(candidate):
	release = next(iter(candidate.get('release_names') or ()), '') or candidate.get('release', '')
	return {key: candidate.get(key) for key in ('provider', 'id', 'lang', 'score')} | {'release': release[:180]}


class ProviderManager:
	def __init__(self, media, cancelled=None, config=None, provider_classes=None):
		self.media, self.cancelled = media, cancelled
		config = load_provider_config() if config is None else config
		classes = PROVIDER_CLASSES if provider_classes is None else provider_classes
		self.providers = {name: classes[name](provider_config, media, cancelled) for name, provider_config in config.items() if name in classes}
		self.failed_providers = []

	def search(self):
		if not self.providers: return []
		results = []
		executor = ThreadPoolExecutor(max_workers=len(self.providers), thread_name_prefix='subtitle-provider')
		try:
			futures = {executor.submit(provider.search): name for name, provider in self.providers.items()}
			for future in as_completed(futures):
				name = futures[future]
				try: results.extend(future.result() or ())
				except ProviderCancelled: raise
				except Exception as error:
					self.failed_providers.append(name)
					_log('search', 'failed', name, type(error).__name__)
		finally: executor.shutdown(wait=False, cancel_futures=True)
		_cancelled(self.cancelled)
		return rank_candidates(results, self.media)

	def download(self, candidate):
		provider = self.providers.get(candidate.get('provider'))
		if not provider: return None
		_cancelled(self.cancelled)
		payload = provider.download(candidate)
		_cancelled(self.cancelled)
		return payload

	def find(self, provider_name, candidate_id):
		provider = self.providers.get(provider_name)
		if not provider: return None
		for candidate in rank_candidates(provider.search(), self.media):
			if candidate.get('id') == str(candidate_id): return candidate
		return None
