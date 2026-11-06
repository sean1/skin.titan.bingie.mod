import io
import base64
import json
import math
import re
import stat
import unicodedata
import zipfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from html.parser import HTMLParser
from pathlib import PurePosixPath
from threading import Lock
from types import SimpleNamespace
from time import monotonic
from urllib.parse import quote, unquote, urljoin, urlsplit

import requests
from modules import kodi_utils


CONFIG_PATH = 'resources/private/subtitle_providers.json'
LANGUAGES = ('eng', 'vie')
LANGUAGE_CODES = {'en': 'eng', 'eng': 'eng', 'english': 'eng', 'vi': 'vie', 'vie': 'vie', 'vietnamese': 'vie'}
SUPPORTED_EXTENSIONS = ('.srt', '.ass', '.ssa', '.vtt', '.sub')
RETRYABLE_STATUS_CODES = (429, 502, 503, 504)
REQUEST_TIMEOUT = 20.0
EARLY_MATCH_GRACE = 0.5
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
	if isinstance(value, bool): return value
	if isinstance(value, (int, float)) and value in (0, 1): return bool(value)
	if isinstance(value, str):
		value = value.strip().lower()
		if value in ('true', '1', 'yes'): return True
		if value in ('false', '0', 'no'): return False
	return None


def subtitle_coverage(value):
	if isinstance(value, dict):
		flags = [_as_bool(value.get(key)) for key in ('forced', 'isforced', 'isForced', 'foreign_parts_only', 'foreignParts')]
		names = [value.get(key) for key in ('name', 'filename', 'caption', 'release', 'production_type')]
		releases = value.get('release_names') or ()
		names.extend(releases if isinstance(releases, (list, tuple)) else (releases,))
	else: flags, names = [], [value]
	if True in flags: return 'partial'
	for name in names:
		text = ' '.join(re.findall(r'[a-z0-9]+', str(name or '').lower()))
		text = re.sub(r'\b(?:non|not) forced\b', 'unforced', text)
		if re.search(r'\bforced\b|\bforeign (?:parts|dialogue)(?: only)?\b|\bsigns (?:and )?songs\b|\bsongs (?:and )?signs\b|\b(?:signs|songs) only\b', text): return 'partial'
	if False in flags: return 'full'
	for name in names:
		if re.search(r'\b(?:full|complete)[\s._-]+(?:dialogue|subtitles?)\b|\((?:full|complete)\)', str(name or '').lower()): return 'full'
	return 'unknown'


def _read_config(path):
	with kodi_utils.open_file(path) as config_file: return config_file.readBytes().decode('utf-8-sig')


def _load_provider_config(path=None):
	path = path or '%s%s' % (kodi_utils.addon_path, CONFIG_PATH)
	try:
		if not kodi_utils.path_exists(path):
			_log('config', 'disabled', category='missing')
			return {}, 'missing'
		payload = json.loads(_read_config(path))
	except (OSError, UnicodeError):
		_log('config', 'disabled', category='unreadable')
		return {}, 'unreadable'
	except (TypeError, ValueError):
		_log('config', 'disabled', category='invalid_json')
		return {}, 'invalid_json'
	if not isinstance(payload, dict):
		_log('config', 'disabled', category='invalid_schema')
		return {}, 'invalid_schema'
	config = {}
	for provider in ('opensubtitles', 'subdl'):
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
	for provider in ('subf2m', 'subtitledb', 'gestdown'):
		entry = payload.get(provider, {})
		if isinstance(entry, dict) and entry.get('enabled', True) is not False: config[provider] = {}
	entry = payload.get('addic7ed')
	if isinstance(entry, dict) and entry.get('enabled', True) is not False:
		config['addic7ed'] = {}
	if not config:
		_log('config', 'disabled', category='invalid_schema')
		return {}, 'invalid_schema'
	return config, ''


def load_provider_config(path=None):
	return _load_provider_config(path)[0]


def _cancelled(cancelled):
	if cancelled and cancelled(): raise ProviderCancelled


def _request(method, url, provider, cancelled=None, **kwargs):
	timeout = kwargs.pop('timeout', REQUEST_TIMEOUT)
	attempts = kwargs.pop('attempts', 2)
	for attempt in range(1, attempts + 1):
		_cancelled(cancelled)
		try: response = requests.request(method, url, timeout=timeout, **kwargs)
		except (requests.Timeout, requests.ConnectionError) as error:
			if attempt == attempts: raise ProviderError(type(error).__name__)
			_log(method.lower(), 'retry', provider, type(error).__name__)
			if kodi_utils.monitor.waitForAbort(1): raise ProviderCancelled
			continue
		if cancelled and cancelled():
			try: response.close()
			except Exception: pass
			raise ProviderCancelled
		if response.status_code not in RETRYABLE_STATUS_CODES or attempt == attempts: return response
		try: retry_after = int(response.headers.get('Retry-After', 1)) if response.status_code == 429 else 1
		except (AttributeError, TypeError, ValueError): retry_after = 1
		retry_after = min(max(retry_after, 1), 10)
		try: response.close()
		except Exception: pass
		_log(method.lower(), 'retry', provider, 'http_%s' % response.status_code)
		if kodi_utils.monitor.waitForAbort(retry_after): raise ProviderCancelled
	raise ProviderError('request_failed')


def _download_binary(url, provider, max_bytes, cancelled=None, **kwargs):
	response = None
	_cancelled(cancelled)
	try:
		response = _request('GET', url, provider, cancelled, stream=True, **kwargs)
		_cancelled(cancelled)
		if not 200 <= response.status_code < 300: raise ProviderError('http_%s' % response.status_code)
		try: content_length = int(response.headers.get('Content-Length', -1))
		except (AttributeError, TypeError, ValueError): content_length = -1
		if content_length > max_bytes: raise ProviderError('invalid_size')
		content, total = [], 0
		try:
			for chunk in response.iter_content(chunk_size=64 * 1024):
				_cancelled(cancelled)
				if not chunk: continue
				if not isinstance(chunk, (bytes, bytearray)): raise ProviderError('read_error')
				total += len(chunk)
				if total > max_bytes: raise ProviderError('invalid_size')
				content.append(bytes(chunk))
		except (ProviderCancelled, ProviderError): raise
		except Exception as error: raise ProviderError('read_%s' % type(error).__name__)
		_cancelled(cancelled)
		if not content: raise ProviderError('empty_response')
		return b''.join(content)
	finally:
		if response is not None:
			try: response.close()
			except Exception: pass


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
		self.config, self.media, self.cancelled, self.failed = config, media, cancelled, False

	def request_json(self, method, url, operation, **kwargs):
		try: return _json_response(_request(method, url, self.name, self.cancelled, **kwargs), self.name, operation)
		except ProviderCancelled: raise
		except ProviderError as error:
			self.failed = True
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
					forced=attributes.get('forced'),
					machine_translated=attributes.get('machine_translated') or attributes.get('ai_translated'), trusted=attributes.get('from_trusted'),
					rating=attributes.get('ratings'), downloads=attributes.get('download_count'), hash_match=attributes.get('moviehash_match'),
					extension=PurePosixPath(str(subtitle_file.get('file_name') or '')).suffix.lower().lstrip('.') or 'srt'
				)
				if candidate: results.append(candidate)
		return results

	def download(self, candidate):
		if self.download_disabled: return None
		try:
			self.login()
			payload = self.authenticated_json('POST', 'download', 'download', json={'file_id': int(candidate['id'])})
			link = payload.get('link')
			if not isinstance(link, str) or not link.startswith('https://'): raise ProviderError('invalid_schema')
			content = _download_binary(link, self.name, MAX_SUBTITLE_BYTES, self.cancelled)
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
			signature = (candidate['lang'], release_key, candidate.get('hearing_impaired'), subtitle_coverage(candidate))
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
			archive_id = PurePosixPath(urlsplit(path).path).name
			if not archive_id or '/' in archive_id or '\\' in archive_id: continue
			candidate = _candidate(
				self.name, 'archive:%s' % archive_id, item.get('lang') or item.get('language'), (item.get('release_name'), item.get('name')),
				fps=item.get('fps'), season=item.get('season'), episode=item.get('episode'), hearing_impaired=item.get('hi'), full_season=item.get('full_season'),
				forced=item.get('forced'), foreign_parts_only=item.get('foreign_parts_only', item.get('foreignParts')),
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
				parts = PurePosixPath(urlsplit(path).path).parts
				if len(parts) < 4 or parts[-1] != str(subtitle_file.get('file_n_id')): continue
				n_id, file_n_id = parts[-2], parts[-1]
				candidate = _candidate(
					self.name, 'file:%s:%s' % (n_id, file_n_id), subtitle_file.get('language') or parent.get('lang'),
					(subtitle_file.get('release_name'), subtitle_file.get('name'), parent.get('release_name'), parent.get('name')),
					fps=parent.get('fps'), season=season, episode=episode, hearing_impaired=subtitle_file.get('hi', parent.get('hi')),
					forced=subtitle_file.get('forced', parent.get('forced')),
					foreign_parts_only=subtitle_file.get('foreign_parts_only', subtitle_file.get('foreignParts', parent.get('foreign_parts_only', parent.get('foreignParts')))),
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
			max_bytes = MAX_ARCHIVE_BYTES if archive else MAX_SUBTITLE_BYTES
			content = _download_binary('%s%s' % (self.download_url, locator), self.name, max_bytes, self.cancelled, headers={'X-API-Key': self.config['api_key']})
			if archive:
				payload = extract_subtitle_archive(content, self.media.get('season'), self.media.get('episode'), self.media.get('release_name', ''), full_dialogue_only=candidate.get('full_dialogue_only') is True)
				if not payload: raise ProviderError('invalid_archive')
				return payload
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


def extract_subtitle_archive(content, season=None, episode=None, release_name='', full_dialogue_only=False, allow_episode_names=False):
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
				if not matching and allow_episode_names:
					matching = [info for info in eligible if not re.search(r'(?i)(?:s\d+[ ._-]*e\d+|\d+x\d+|(?:^|[/\\])s\d+(?:[/\\]|$)|season[ ._-]*\d+)', info.filename) and re.search(r'(?i)(?<![a-z0-9])episode[ ._-]*0*%s\.(?:srt|ass|ssa|vtt|sub)$' % int(episode), info.filename)]
				if matching: eligible = matching
				elif len(eligible) != 1: return None
				elif allow_episode_names and re.search(r'(?i)(?:s\d+[ ._-]*e\d+|\d+x\d+|(?:^|[/\\])s\d+(?:[/\\]|$)|season[ ._-]*\d+|episode[ ._-]*\d+)', eligible[0].filename): return None
			if full_dialogue_only: eligible = [info for info in eligible if subtitle_coverage(PurePosixPath(info.filename.replace('\\', '/')).name) != 'partial']
			if not eligible: return None
			eligible.sort(key=lambda info: (
				('full', 'unknown').index(subtitle_coverage(PurePosixPath(info.filename.replace('\\', '/')).name)) if full_dialogue_only else 0,
				-release_match_score(release_name, (PurePosixPath(info.filename.replace('\\', '/')).name,)),
				SUPPORTED_EXTENSIONS.index(PurePosixPath(info.filename.replace('\\', '/')).suffix.lower()), -info.file_size, info.filename.lower()
			))
			selected = eligible[0]
			if selected.file_size > MAX_SUBTITLE_BYTES: return None
			selected_content = archive.read(selected)
			if not selected_content or len(selected_content) > MAX_SUBTITLE_BYTES: return None
			return {'content': selected_content, 'extension': PurePosixPath(selected.filename.replace('\\', '/')).suffix.lower().lstrip('.')}
	except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile): return None


class _Addic7edParser(HTMLParser):
	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.options, self.links, self.rows = [], [], []
		self.option, self.link, self.row, self.cell = None, None, None, None

	def handle_starttag(self, tag, attrs):
		attrs = dict(attrs)
		if tag == 'option': self.option = {'value': attrs.get('value', ''), 'text': []}
		elif tag == 'tr': self.row = {'class': attrs.get('class', '').split(), 'cells': []}
		elif tag == 'td' and self.row is not None:
			self.cell = {'class': attrs.get('class', '').split(), 'text': [], 'links': [], 'images': []}
			self.row['cells'].append(self.cell)
		elif tag == 'a': self.link = {'href': attrs.get('href', ''), 'text': []}
		elif tag == 'img' and self.cell is not None: self.cell['images'].append(attrs.get('title', ''))

	def handle_data(self, data):
		for item in (self.option, self.cell, self.link):
			if item is not None: item['text'].append(data)

	def handle_endtag(self, tag):
		if tag == 'option' and self.option is not None:
			self.option['text'] = ' '.join(''.join(self.option['text']).split())
			self.options.append(self.option)
			self.option = None
		elif tag == 'a' and self.link is not None:
			self.link['text'] = ' '.join(''.join(self.link['text']).split())
			self.links.append(self.link)
			if self.cell is not None: self.cell['links'].append(self.link)
			self.link = None
		elif tag == 'td' and self.cell is not None:
			self.cell['text'] = ' '.join(''.join(self.cell['text']).split())
			self.cell = None
		elif tag == 'tr' and self.row is not None:
			self.rows.append(self.row)
			self.row, self.cell = None, None


def _addic7ed_title(value):
	value = unicodedata.normalize('NFKD', str(value or ''))
	return ' '.join(re.findall(r'[a-z0-9]+', ''.join(character for character in value.lower() if not unicodedata.combining(character))))


class Addic7edProvider(Provider):
	name = 'addic7ed'
	base_url = 'https://www.addic7ed.com'
	request_budget = 25.0

	def __init__(self, config, media, cancelled=None):
		super().__init__(config, media, cancelled)
		self.deadline = 0.0

	def _fetch(self, path, operation, referer='/', params=None, max_bytes=2 * 1024 * 1024):
		url = urljoin(self.base_url + '/', path)
		try:
			for redirect in range(4):
				parsed = urlsplit(url)
				if parsed.scheme != 'https' or parsed.netloc != 'www.addic7ed.com' or parsed.username or parsed.password or '\\' in parsed.path: raise ProviderError('invalid_redirect')
				remaining = self.deadline - monotonic()
				if remaining <= 0: raise ProviderError('timeout')
				response = _request('GET', url, self.name, self.cancelled, headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36', 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', 'Referer': self.base_url + referer}, params=params, timeout=min(18.0, remaining), attempts=1, allow_redirects=False, stream=True)
				params = None
				try:
					if response.status_code in (301, 302, 303, 307, 308):
						location = response.headers.get('Location', '')
						if not location or redirect == 3: raise ProviderError('invalid_redirect')
						url = urljoin(url, location)
						continue
					if not 200 <= response.status_code < 300: raise ProviderError('http_%s' % response.status_code)
					chunks, total = [], 0
					for chunk in response.iter_content(chunk_size=64 * 1024):
						_cancelled(self.cancelled)
						if monotonic() >= self.deadline: raise ProviderError('timeout')
						if not chunk: continue
						total += len(chunk)
						if total > max_bytes: raise ProviderError('invalid_size')
						chunks.append(chunk)
					if not chunks: raise ProviderError('empty_response')
					return b''.join(chunks), urlsplit(url).path
				finally: response.close()
			raise ProviderError('invalid_redirect')
		except ProviderError as error:
			self.failed = True
			_log(operation, 'failed', self.name, str(error))
			raise
		except (requests.Timeout, requests.ConnectionError) as error:
			self.failed = True
			_log(operation, 'failed', self.name, type(error).__name__)
			raise ProviderError(type(error).__name__)

	def _get(self, path, operation, referer='/', params=None, max_bytes=2 * 1024 * 1024):
		return self._fetch(path, operation, referer, params, max_bytes)[0]

	def _page(self, path, operation, referer='/', params=None):
		content = self._get(path, operation, referer, params)
		text = content.decode('utf-8', 'replace')
		if re.search(r'(?i)<form[^>]+(?:login|signin)|name=["\'](?:password|pass)["\']', text):
			self.failed = True
			_log(operation, 'failed', self.name, 'authentication')
			raise ProviderError('authentication')
		parser = _Addic7edParser()
		parser.feed(text)
		return parser

	@staticmethod
	def _candidate_id(path, kind, source_id):
		match = re.fullmatch(r'/(updated|original)/([0-9]+)/([0-9]+)(?:/([0-9]+))?', str(path or ''))
		if not match or not str(source_id).isdigit(): return ''
		if kind == 'show' and (match.group(1) != 'updated' or match.group(4) is None): return ''
		if kind == 'movie' and (match.group(1) != 'original' or match.group(4) is not None or match.group(2) != str(source_id)): return ''
		return ':'.join((match.group(1), match.group(2), match.group(3), match.group(4) or '-', kind, str(source_id)))

	def _episode_reference(self, path):
		parsed = urlsplit(urljoin(self.base_url + '/', str(path or '')))
		if parsed.scheme != 'https' or parsed.netloc != 'www.addic7ed.com' or parsed.query or parsed.fragment: return ''
		parts = parsed.path.split('/')
		if len(parts) != 6 or parts[1] != 'serie' or not parts[3].isdigit() or not parts[4].isdigit(): return ''
		show, episode_title = unquote(parts[2]), unquote(parts[5])
		if any(not value or value in ('.', '..') or any(c in value for c in '/\\\r\n\x00') for value in (show, episode_title)): return ''
		if _addic7ed_title(show) != _addic7ed_title(self.media.get('title')) or (_as_int(parts[3]), _as_int(parts[4])) != (_as_int(self.media.get('season')), _as_int(self.media.get('episode'))): return ''
		return '/serie/%s/%s/%s/%s' % (quote(show, safe=''), parts[3], parts[4], quote(episode_title, safe=''))

	@staticmethod
	def _episode_candidate_id(path, referer):
		match = re.fullmatch(r'/(original|updated)/([0-9]+)/([0-9]+)(?:/([0-9]+))?', str(path or ''))
		if not match: return ''
		encoded = base64.urlsafe_b64encode(referer.encode('utf-8')).decode('ascii').rstrip('=')
		candidate_id = ':'.join(('ep', match.group(1), match.group(2), match.group(3), match.group(4) or '-', encoded))
		return candidate_id if len(candidate_id) <= 256 else ''

	def _episode_search(self):
		season, episode = _as_int(self.media.get('season')), _as_int(self.media.get('episode'))
		title = _addic7ed_title(self.media.get('title'))
		if season is None or episode is None or season < 0 or episode < 0 or not title: return []
		page = self._page('/', 'lookup')
		shows = [(item['value'], _addic7ed_title(item['text'])) for item in page.options if item['value'].isdigit()]
		matches = [show_id for show_id, name in shows if name == title]
		variant = re.compile(r'^(?:(?:19|20)\d{2}|us|uk|au|ca|united states|united kingdom|australia|canada)(?:\s+(?:(?:19|20)\d{2}|us|uk|au|ca|united states|united kingdom|australia|canada))?$')
		if any(name.startswith(title + ' ') and variant.fullmatch(name[len(title) + 1:]) for _, name in shows): return []
		if len(set(matches)) != 1: return []
		content, final_path = self._fetch('/search.php', 'search', '/', {'search': '%s %02dx%02d' % (self.media['title'], season, episode), 'Submit': 'Search'})
		if re.search(r'(?i)/(?:login|signin)(?:\.php)?/?$', final_path):
			self.failed = True
			_log('search', 'failed', self.name, 'authentication')
			return []
		page = _Addic7edParser()
		page.feed(content.decode('utf-8', 'replace'))
		references = [self._episode_reference(final_path)] if final_path.startswith('/serie/') else [self._episode_reference(link['href']) for link in page.links]
		references = list(dict.fromkeys(reference for reference in references if reference))
		results = []
		for reference in references[:3]:
			if reference != self._episode_reference(final_path):
				try: page = self._page(reference, 'search')
				except ProviderError: continue
			release = ''
			for row_index, row in enumerate(page.rows):
				cells = row['cells']
				notes = next((cell['text'] for cell in cells if 'NewsTitle' in cell['class']), '')
				if notes: release = re.sub(r'(?i)^version\s+', '', notes).split(', Duration:', 1)[0].strip()
				language = next((_normalize_language(cell['text']) for cell in cells if 'language' in cell['class']), '')
				if not language or not any('completed' in cell['text'].lower() for cell in cells): continue
				following = page.rows[row_index + 1]['cells'] if row_index + 1 < len(page.rows) else []
				if any('jointranslation' in link['href'].lower() for cell in cells + following for link in cell['links']): continue
				hearing_impaired = any('hearing impaired' in image.lower() for cell in following for image in cell['images'])
				for cell in cells:
					for link in cell['links']:
						candidate_id = self._episode_candidate_id(link['href'], reference)
						if candidate_id:
							candidate = _candidate(self.name, candidate_id, language, (release,), season=season, episode=episode, hearing_impaired=hearing_impaired, extension='srt')
							if candidate: results.append(candidate)
		return results

	def _movie_search(self):
		title, year = _addic7ed_title(self.media.get('title')), _as_int(self.media.get('year'))
		if not title or not year: return []
		page = self._page('/search.php', 'lookup', '/srch.php', {'Submit': 'Search', 'search': self.media['title']})
		ids = []
		for link in page.links:
			parsed = urlsplit(urljoin(self.base_url + '/', link['href']))
			if parsed.scheme != 'https' or parsed.netloc != 'www.addic7ed.com' or parsed.query or parsed.fragment: continue
			match = re.fullmatch(r'/movie/([0-9]+)', parsed.path)
			name = re.fullmatch(r'(.+?)\s*\((\d{4})\)', link['text'])
			if match and name and _addic7ed_title(name.group(1)) == title and int(name.group(2)) == year and match.group(1) not in ids: ids.append(match.group(1))
		results = []
		for movie_id in ids[:3]:
			try: page = self._page('/movie/%s' % movie_id, 'search', '/srch.php')
			except ProviderError: continue
			release = ''
			for row in page.rows:
				cells = row['cells']
				notes = next((cell['text'] for cell in cells if 'newsDate' in cell['class']), '')
				if notes: release = notes[:300]
				language = next((_normalize_language(cell['text']) for cell in cells if 'language' in cell['class']), '')
				if not language or not any('completed' in cell['text'].lower() for cell in cells): continue
				for cell in cells:
					for link in cell['links']:
						candidate_id = self._candidate_id(link['href'], 'movie', movie_id)
						if candidate_id:
							candidate = _candidate(self.name, candidate_id, language, (release, self.media['title']), extension='srt')
							if candidate: results.append(candidate)
		return results

	def search(self):
		self.deadline = monotonic() + self.request_budget
		try: return self._episode_search() if self.media.get('season') not in (None, '') else self._movie_search()
		except ProviderError: return []

	def download(self, candidate):
		self.deadline = monotonic() + self.request_budget
		parts = str(candidate.get('id') or '').split(':')
		if len(parts) == 6 and parts[0] == 'ep':
			try: referer = base64.b64decode(parts[5] + '=' * (-len(parts[5]) % 4), altchars=b'-_', validate=True).decode('utf-8')
			except (ValueError, UnicodeError): return None
			if not referer or self._episode_reference(referer) != referer: return None
			path = '/%s/%s/%s%s' % (parts[1], parts[2], parts[3], '' if parts[4] == '-' else '/' + parts[4])
			if self._episode_candidate_id(path, referer) != ':'.join(parts): return None
			try:
				content = self._get(path, 'download', referer, max_bytes=MAX_SUBTITLE_BYTES)
				if b'-->' not in content or re.search(br'(?i)^\s*<(?:!doctype|html|form)', content): raise ProviderError('invalid_subtitle')
				return {'content': content, 'extension': 'srt'}
			except ProviderError as error:
				_log('download', 'failed', self.name, str(error))
				return None
		if len(parts) != 6 or parts[0] not in ('updated', 'original') or not all(part.isdigit() for part in parts[1:3]) or parts[3] != '-' and not parts[3].isdigit() or parts[4] not in ('show', 'movie') or not parts[5].isdigit(): return None
		path = '/%s/%s/%s%s' % (parts[0], parts[1], parts[2], '' if parts[3] == '-' else '/' + parts[3])
		if self._candidate_id(path, parts[4], parts[5]) != ':'.join(parts): return None
		try:
			content = self._get(path, 'download', '/%s/%s' % (parts[4], parts[5]), max_bytes=MAX_SUBTITLE_BYTES)
			if b'-->' not in content or re.search(br'(?i)^\s*<(?:!doctype|html|form)', content): raise ProviderError('invalid_subtitle')
			return {'content': content, 'extension': 'srt'}
		except ProviderError as error:
			_log('download', 'failed', self.name, str(error))
			return None


import re
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit


class _Parser(HTMLParser):
	def __init__(self):
		super().__init__()
		self.links, self.items, self.stack, self.item, self.link = [], [], [], None, None

	def handle_starttag(self, tag, attrs):
		attrs = dict(attrs)
		if tag == 'li':
			self.stack.append(tag)
			if self.item is None and 'item' in attrs.get('class', '').split(): self.item = {'depth': len(self.stack), 'text': [], 'href': ''}
		if tag == 'a':
			self.link = {'href': attrs.get('href', ''), 'text': []}
			if self.item is not None and 'download' in attrs.get('class', '').split(): self.item['href'] = attrs.get('href', '')

	def handle_data(self, data):
		if self.item is not None: self.item['text'].append(data)
		if self.link is not None: self.link['text'].append(data)

	def handle_endtag(self, tag):
		if tag == 'a' and self.link is not None:
			self.links.append((' '.join(self.link['text']).strip(), self.link['href']))
			self.link = None
		if tag == 'li' and self.stack:
			if self.item is not None and self.item['depth'] == len(self.stack):
				self.item['text'] = ' '.join(self.item['text'])
				self.items.append(self.item)
				self.item = None
			self.stack.pop()


def _make_subf2m_provider(core):
	class Subf2mProvider(core.Provider):
		name = 'subf2m'
		base = 'https://subf2m.co'

		def _page(self, path):
			response = core._request('GET', self.base + path, self.name, self.cancelled, timeout=12, allow_redirects=False, stream=True)
			try:
				if response.status_code != 200: raise core.ProviderError('http_%s' % response.status_code)
				chunks, size = [], 0
				for chunk in response.iter_content(chunk_size=65536):
					core._cancelled(self.cancelled)
					size += len(chunk)
					if size > 2 * 1024 * 1024: raise core.ProviderError('invalid_size')
					chunks.append(chunk)
				parser = _Parser(); parser.feed(b''.join(chunks).decode('utf-8', 'replace')); return parser
			finally: response.close()

		def search(self):
			title = str(self.media.get('title') or '').strip()
			if not title: return []
			try:
				page = self._page('/subtitles/searchbytitle?query=' + quote(title))
				wanted = re.sub(r'[^a-z0-9]+', '', title.lower())
				season = self.media.get('season')
				matches = []
				for label, path in page.links:
					clean = re.sub(r'\s*[-–]\s*season\s+\d+.*$', '', label, flags=re.I)
					clean = re.sub(r'\s*\(\d{4}\)\s*$', '', clean)
					if re.sub(r'[^a-z0-9]+', '', clean.lower()) != wanted or not re.fullmatch(r'/subtitles/[a-z0-9-]+', path): continue
					if season is not None and not re.search(r'(?i)season\s+%s\b' % int(season), label): continue
					matches.append(path)
				if len(set(matches)) != 1: return []
				page = self._page(matches[0]); results = []
				for item in page.items:
					path, text = item['href'], item['text']
					match = re.fullmatch(r'/subtitles/[a-z0-9-]+/(english|vietnamese)/(\d+)', path)
					if not match: continue
					if season is not None:
						episode = int(self.media['episode'])
						ranges = re.findall(r'(?i)\bepisodes?[\s_]+(\d+)(?:[\s_]*[-–][\s_]*(\d+))?', text)
						if not core._episode_matches(text, season, episode) and not any(int(start) <= episode <= int(end or start) for start, end in ranges): continue
					candidate = core._candidate(self.name, path, match.group(1), (text,), season=season, episode=self.media.get('episode'), extension='zip')
					if candidate: results.append(candidate)
				return results
			except (core.ProviderError, ValueError, TypeError): self.failed = True; return []

		def download(self, candidate):
			path = str(candidate.get('id') or '')
			if not re.fullmatch(r'/subtitles/[a-z0-9-]+/(?:english|vietnamese)/\d+', path): return None
			try:
				url = self.base + path + '/download'
				for _ in range(4):
					parsed = urlsplit(url)
					if parsed.scheme != 'https' or parsed.hostname not in ('subf2m.co', 'isubcdn.com') or parsed.username or parsed.password or parsed.port not in (None, 443): raise core.ProviderError('invalid_redirect')
					response = core._request('GET', url, self.name, self.cancelled, stream=True, allow_redirects=False, timeout=12)
					try:
						if response.status_code in (301, 302, 303, 307, 308):
							location = response.headers.get('Location', '')
							if not location: raise core.ProviderError('invalid_redirect')
							url = urljoin(url, location); continue
						if response.status_code != 200: raise core.ProviderError('http_%s' % response.status_code)
						chunks, size = [], 0
						for chunk in response.iter_content(chunk_size=65536):
							core._cancelled(self.cancelled)
							size += len(chunk)
							if size > core.MAX_ARCHIVE_BYTES: raise core.ProviderError('invalid_size')
							chunks.append(chunk)
						content = b''.join(chunks); break
					finally: response.close()
				else: raise core.ProviderError('invalid_redirect')
				return core.extract_subtitle_archive(content, self.media.get('season'), self.media.get('episode'), self.media.get('release_name', ''), allow_episode_names=True)
			except core.ProviderError: self.failed = True; return None
	return Subf2mProvider


"""SubtitleDB integration for BINGIE's subtitle manager."""
import re
from urllib.parse import urljoin, urlsplit


def _make_tsdb_provider(core):
	class SubtitleDBProvider(core.Provider):
		name = 'subtitledb'
		api_url = 'https://api.thesubtitledb.org'

		def search(self):
			imdb = str(self.media.get('imdb_id') or '')
			if not re.fullmatch(r'tt\d+', imdb): return []
			path = '/v1/by-imdb/' + imdb
			season, episode = core._as_int(self.media.get('season')), core._as_int(self.media.get('episode'))
			if season is not None:
				if episode is None or season < 0 or episode < 0: return []
				path += '/season/%d/episode/%d' % (season, episode)
			try:
				response = core._request('GET', self.api_url + path, self.name, self.cancelled, params={'lang': 'en,vi', 'limit': 100, 'client': 'kodi'})
				if response.status_code == 404:
					response.close()
					return []
				payload = core._json_response(response, self.name, 'search')
			except core.ProviderError as error:
				self.failed = True
				core._log('search', 'failed', self.name, str(error))
				return []
			container = payload.get('subtitles')
			items = container.get('items', []) if isinstance(container, dict) else []
			results = []
			for item in items:
				if not isinstance(item, dict) or not str(item.get('id', '')).isdigit(): continue
				extension = str(item.get('format') or '').lower()
				if '.' + extension not in core.SUPPORTED_EXTENSIONS: continue
				candidate = core._candidate(self.name, item['id'], item.get('language'), (item.get('release_name'),), season=season, episode=episode, hearing_impaired=item.get('hearing_impaired'), fps=item.get('fps'), extension=extension)
				if candidate: results.append(candidate)
			return results

		def download(self, candidate):
			item_id = str(candidate.get('id') or '')
			if not item_id.isdigit(): return None
			url = '%s/get/%s?client=kodi' % (self.api_url, item_id)
			try:
				for _ in range(4):
					response = core._request('GET', url, self.name, self.cancelled, stream=True, allow_redirects=False)
					try:
						if response.status_code in (301, 302, 303, 307, 308):
							next_url = urljoin(url, response.headers.get('Location', ''))
							parsed = urlsplit(next_url)
							if parsed.scheme != 'https' or parsed.hostname not in ('api.thesubtitledb.org', 'cdn.thesubtitledb.org', 'files.thesubtitledb.org') or parsed.username or parsed.password or parsed.port not in (None, 443): raise core.ProviderError('invalid_redirect')
							url = next_url
							continue
						if not 200 <= response.status_code < 300: raise core.ProviderError('http_%s' % response.status_code)
						chunks, total = [], 0
						for chunk in response.iter_content(chunk_size=65536):
							core._cancelled(self.cancelled)
							if not chunk: continue
							total += len(chunk)
							if total > core.MAX_SUBTITLE_BYTES: raise core.ProviderError('invalid_size')
							chunks.append(chunk)
						if not chunks: raise core.ProviderError('empty_response')
						return {'content': b''.join(chunks), 'extension': candidate.get('extension') or 'srt'}
					finally: response.close()
				raise core.ProviderError('invalid_redirect')
			except core.ProviderError as error:
				core._log('download', 'failed', self.name, str(error))
				return None
	return SubtitleDBProvider


"""Gestdown TV subtitle provider."""
import re
from urllib.parse import quote


def _make_gestdown_provider(core):
	class GestdownProvider(core.Provider):
		name = 'gestdown'
		api_url = 'https://api.gestdown.info'

		def _lookup(self, path, operation):
			try:
				response = core._request('GET', self.api_url + path, self.name, self.cancelled)
				if response.status_code == 404:
					response.close()
					return None
				return core._json_response(response, self.name, operation)
			except core.ProviderError as error:
				self.failed = True
				core._log(operation, 'failed', self.name, str(error))
				return None

		def search(self):
			season, episode = core._as_int(self.media.get('season')), core._as_int(self.media.get('episode'))
			title = str(self.media.get('title') or '').strip()
			if season is None or episode is None or not title: return []
			payload = self._lookup('/shows/search/%s' % quote(title, safe=''), 'lookup')
			if payload is None: return []
			shows = [show for show in payload.get('shows', []) if isinstance(show, dict)]
			tmdb = str(self.media.get('tmdb_id') or '')
			matches = [show for show in shows if tmdb and str(show.get('tmdbId') or '') == tmdb]
			if not matches: matches = [show for show in shows if core._addic7ed_title(show.get('name')) == core._addic7ed_title(title)]
			if len(matches) != 1: return []
			show_id = str(matches[0].get('id') or '')
			if not re.fullmatch(r'[A-Za-z0-9_-]+', show_id): return []
			results = []
			for language in ('en', 'vi'):
				payload = self._lookup('/subtitles/get/%s/%d/%d/%s' % (show_id, season, episode, language), 'search')
				if payload is None: continue
				for item in payload.get('matchingSubtitles', []):
					if not isinstance(item, dict) or item.get('completed') is not True: continue
					subtitle_id = str(item.get('subtitleId') or '')
					if not re.fullmatch(r'[A-Za-z0-9_-]+', subtitle_id): continue
					candidate = core._candidate(self.name, subtitle_id, item.get('language') or language, (item.get('version'), item.get('release')), season=season, episode=episode, hearing_impaired=item.get('hearingImpaired'), downloads=item.get('downloadCount'), extension='srt')
					if candidate: results.append(candidate)
			return results

		def download(self, candidate):
			subtitle_id = str(candidate.get('id') or '')
			if not re.fullmatch(r'[A-Za-z0-9_-]+', subtitle_id): return None
			try:
				content = core._download_binary('%s/subtitles/download/%s' % (self.api_url, subtitle_id), self.name, core.MAX_SUBTITLE_BYTES, self.cancelled)
				if b'-->' not in content: raise core.ProviderError('invalid_subtitle')
				return {'content': content, 'extension': 'srt'}
			except core.ProviderError as error:
				core._log('download', 'failed', self.name, str(error))
				return None

	return GestdownProvider


_provider_core = SimpleNamespace(**globals())
PROVIDER_CLASSES = {'opensubtitles': OpenSubtitlesProvider, 'subdl': SubDLProvider, 'addic7ed': Addic7edProvider, 'subf2m': _make_subf2m_provider(_provider_core), 'subtitledb': _make_tsdb_provider(_provider_core), 'gestdown': _make_gestdown_provider(_provider_core)}


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


def candidate_is_synced(candidate, media):
	if candidate.get('hash_match') is True: return True
	if (candidate.get('match_score') or 0.0) >= 0.8: return True
	return release_match_score(media.get('release_name'), candidate.get('release_names')) >= 0.8


def rank_candidates(candidates, media):
	valid = []
	for candidate in candidates:
		if not isinstance(candidate, dict) or candidate.get('lang') not in LANGUAGES: continue
		if media.get('season') is not None and candidate.get('episode') is not None:
			if (candidate.get('season'), candidate.get('episode')) != (_as_int(media['season']), _as_int(media['episode'])): continue
		item = candidate.copy()
		item['score'] = candidate_score(item, media)
		item['sync'] = candidate_is_synced(item, media)
		valid.append(item)
	return sorted(valid, key=lambda item: (LANGUAGES.index(item['lang']), ('full', 'unknown', 'partial').index(subtitle_coverage(item)), -item['score'], item['provider'], item['id']))


class ProviderManager:
	def __init__(self, media, cancelled=None, config=None, provider_classes=None, *, preferred_language=None, try_early_match=None):
		self.media, self.cancelled = media, cancelled
		self.preferred_language, self.try_early_match = preferred_language, try_early_match
		if config is None: config, self.config_error = _load_provider_config()
		else: self.config_error = ''
		classes = PROVIDER_CLASSES if provider_classes is None else provider_classes
		self.providers = {name: classes[name](provider_config, media, cancelled) for name, provider_config in config.items() if name in classes}
		self.failed_providers = []

	def search(self):
		"""Return ranked results, or None when automatic playback attached an early match."""
		if not self.providers: return []
		results = []
		try_early_match, grace_deadline = self.try_early_match, None
		executor = ThreadPoolExecutor(max_workers=len(self.providers), thread_name_prefix='subtitle-provider')
		try:
			futures = {executor.submit(provider.search): name for name, provider in self.providers.items()}
			while futures:
				timeout = EARLY_MATCH_GRACE if self.try_early_match else None
				if grace_deadline is not None: timeout = max(0, grace_deadline - monotonic())
				done, _ = wait(futures, timeout=timeout, return_when=FIRST_COMPLETED)
				if self.try_early_match: _cancelled(self.cancelled)
				for future in done:
					name = futures.pop(future)
					try:
						results.extend(future.result() or ())
						if getattr(self.providers[name], 'failed', False): self.failed_providers.append(name)
					except ProviderCancelled: raise
					except Exception as error:
						self.failed_providers.append(name)
						_log('search', 'failed', name, type(error).__name__)
				if not futures or try_early_match is None or not done and grace_deadline is None: continue
				if grace_deadline is not None and monotonic() < grace_deadline: continue
				candidate = next((item for item in rank_candidates(results, self.media)
					if item['lang'] == self.preferred_language and item['sync'] and subtitle_coverage(item) == 'full'), None)
				if candidate is None: continue
				if grace_deadline is None:
					grace_deadline = monotonic() + EARLY_MATCH_GRACE
					continue
				_cancelled(self.cancelled)
				if try_early_match(candidate): return None
				# Keep the pending provider results for the ordinary fallback.
				try_early_match, grace_deadline = None, None
		finally: executor.shutdown(wait=False, cancel_futures=True)
		_cancelled(self.cancelled)
		return rank_candidates(results, self.media)

	def diagnostics(self):
		return {'config': self.config_error, 'providers': tuple(sorted(set(self.failed_providers)))}

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
