import json
from threading import Event, Lock, Thread
from time import monotonic
from debrids import all_debrid_api, real_debrid_api, torbox_api
from caches.debrid_cache import DebridCache
from indexers import metadata
from modules import kodi_utils, settings
# from modules.kodi_utils import logger

ls, get_setting, notification = kodi_utils.local_string, kodi_utils.get_setting, kodi_utils.notification
show_busy_dialog, hide_busy_dialog = kodi_utils.show_busy_dialog, kodi_utils.hide_busy_dialog
ok_dialog, confirm_dialog, select_dialog = kodi_utils.ok_dialog, kodi_utils.confirm_dialog, kodi_utils.select_dialog
default_internal_scrapers, enabled_debrids_check = settings.default_internal_scrapers, settings.enabled_debrids_check
default_external_scrapers = ('external',)
plswait_str, checking_debrid_str, remaining_debrid_str = ls(32577), ls(32578), ls(32579)

debrid_list = (
	('realdebrid', 'rd', real_debrid_api.RealDebridAPI),
	('alldebrid', 'ad', all_debrid_api.AllDebridAPI),
	('torbox', 'tb', torbox_api.TorBoxAPI),
)

def import_debrid(debrid_provider):
	cls = next((i[2] for i in debrid_list if i[0] == debrid_provider), None)
	return cls() if cls else cls

def debrid_enabled():
	return [i[0] for i in debrid_list if enabled_debrids_check(i[1])]

def debrid_type_enabled(debrid_type, enabled_debrids):
	return [i[0] for i in debrid_list if i[0] in enabled_debrids and get_setting('%s.%s.enabled' % (i[1], debrid_type)) == 'true']

def play_from_cloud(params):
	source = Source.fromcloud(params)
	url = source.resolve_internal_sources(source.direct_debrid_link)
	return kodi_utils.execute_builtin('PlayMedia(%s)' % url)

class Source:
	@classmethod
	def fromcloud(cls, params):
		self = cls(params)
		ddl = params.get('direct_debrid_link', True)
		if ddl in ('false', False): self.direct_debrid_link = False
		else: self.direct_debrid_link = True if ddl == 'true' else ddl
		self.url_dl = params['id'] if self.direct_debrid_link else ''
		return self

	def dumps(self, depth=1, width=172):
		from pprint import pformat
		return pformat(vars(self), depth=depth, width=width)

	def __init__(self, source_dict, meta=None):
		self.direct_debrid_link = False
		self.scrape_provider, self.url = '', ''
		for k, v in source_dict.items(): setattr(self, k, v)
		self.meta = meta or {}

	def resolve_sources(self):
		try:
			if self.scrape_provider in default_external_scrapers:
				if self.meta['mediatype'] == 'episode':
					title = self.meta.get('ep_name') or self.meta.get('title')
					season = self.meta.get('custom_season') or self.meta.get('season')
					episode = self.meta.get('custom_episode') or self.meta.get('episode')
				else: title, season, episode = metadata.get_title(self.meta), None, None
				return self.resolve_external_sources(title, season, episode)
			if self.scrape_provider in default_internal_scrapers:
				return self.resolve_internal_sources(self.direct_debrid_link)
			return self.url
		except: pass

	def resolve_external_sources(self, title, season, episode):
		from modules.source_utils import supported_video_extensions, seas_ep_filter, extras_filter
		api, files, torrent_id = None, [], ''
		try:
			extensions = supported_video_extensions()
			extras_filtering_list = tuple(i for i in extras_filter() if i not in title.lower())
			store_to_cloud = settings.store_resolved_torrent_to_cloud(self.debrid)
			args = self.url, self.hash, True
			api = import_debrid(self.debrid)
			files = api.parse_magnet_pack(*args)
			selected_files = []
			selected_files_append = selected_files.append
			for i in files or []:
				torrent_id, filename = i.get('torrent_id'), i['filename'].lower()
				if filename.endswith('.m2ts'): raise Exception('_m2ts_check failed')
				if not filename.endswith(tuple(extensions)): continue
				if season:
					if not seas_ep_filter(season, episode, filename): continue
				elif any(x in filename for x in extras_filtering_list): continue
				selected_files_append(i)
			if not selected_files: raise Exception('selected_files failed')
			if not season: selected_files.sort(key=lambda k: k['size'], reverse=True)
			file_key = next((i['link'] for i in selected_files), None)
			file_url = api.unrestrict_link(file_key)
			if not api.defaults_to_cloud:
				if store_to_cloud: Thread(target=api.create_transfer, args=(self.url,)).start()
			if api.defaults_to_cloud and not store_to_cloud and torrent_id: self._delete(api, torrent_id)
			return file_url
		except Exception as e:
			kodi_utils.logger('resolve_external_sources exception', f"{e}\n{self.dumps()}")
			if api and files and torrent_id: self._delete(api, torrent_id)

	def _delete(self, api, torrent_id):
		if not torrent_id: return
		Thread(target=api.delete_torrent, args=(torrent_id,)).start()

	def resolve_internal_sources(self, direct_debrid_link=False):
		try:
			if self.scrape_provider == 'rd_cloud':
				if direct_debrid_link: url = self.url_dl
				else: url = real_debrid_api.RealDebridAPI().unrestrict_link(self.id)
			else: url = self.url_dl
			return url
		except Exception as e:
			kodi_utils.logger('resolve_internal_sources exception', f"{e}\n{self.dumps()}")

	def browse_packs(self, highlight=None, download=False):
		from modules.source_utils import clean_file_name
		show_busy_dialog()
		api = import_debrid(self.debrid)
		pack_choices = api.parse_magnet_pack(self.url, self.hash)
		hide_busy_dialog()
		if not pack_choices: return None if download else notification(32574)
		pack_choices.sort(key=lambda k: k['filename'].lower())
		for item in pack_choices: item.update({
			'icon': self.meta.get('poster') or api.icon,
			'line1': clean_file_name(item['filename']),
			'line2': '%s: %.2f GB' % (ls(32584), float(item['size'])/1073741824)
		})
		if download: return pack_choices
		kwargs = {'items': json.dumps(pack_choices), 'heading': self.name, 'highlight': highlight}
		chosen_result = select_dialog(pack_choices, **kwargs)
		if chosen_result is None: return 'cancel'
		url_dl = chosen_result['link']
		return api.unrestrict_link(url_dl)

	def manual_add_magnet_to_cloud(self):
		if not confirm_dialog(text=ls(32831) % self.debrid.upper()): return
		show_busy_dialog()
		api = import_debrid(self.debrid)
		api.clear_cache()
		result = api.create_transfer(self.url)
		hide_busy_dialog()
		if result: notification(32576)
		else: notification(32575)

	def unchecked_magnet_status(self):
		show_busy_dialog()
		api = import_debrid(self.debrid)
		result = api.parse_magnet_pack(self.url, self.hash)
		hide_busy_dialog()
		if not result: return ok_dialog(text='Not Cached at [B]%s[/B]' % self.debrid.upper())
		torrent_id = next((i['torrent_id'] for i in result if 'torrent_id' in i), None)
		if torrent_id: Thread(target=api.delete_torrent, args=(torrent_id,)).start()
		ok_dialog(text='Cached at [B]%s[/B]' % self.debrid.upper())

class DebridCheck:
	_debrid_dict = {i[0]: i for i in debrid_list}

	@classmethod
	def request_context(cls, hash_list):
		hash_list = tuple(hash_list)
		with DebridCache() as cache: cached_hashes = tuple(cache.get_many(hash_list) or ())
		return hash_list, cached_hashes

	def __init__(self, meta, name, hash_list, cached_hashes):
		self.cached_list, self.checked_list = [], set()
		self.hash_list, self.cached_hashes = tuple(hash_list), tuple(cached_hashes)
		self.name, self.debrid, self.function = self._debrid_dict[name]
		self.imdb, self.season, self.episode = meta.get('imdb_id'), meta.get('season'), meta.get('episode')

	def result(self):
		if self.debrid in ('ad', 'tb'): return {'cached': self.cached_list, 'checked': self.checked_list}
		return self.cached_list

	def cache_write(self, hashes):
		with DebridCache() as cache: cache.set_many(hashes, self.debrid)

	def cache_check(self):
		try:
			self.cached_list.extend(i[0] for i in self.cached_hashes if i[1] == self.debrid and i[2] == 'True')
			self.checked_list.update(i[0] for i in self.cached_hashes if i[1] == self.debrid)
			# RD's auxiliary services can confirm availability, but an empty response is not an
			# authoritative negative. Ignore legacy false rows so they cannot suppress a recheck.
			unchecked_filter = {h[0] for h in self.cached_hashes if h[1] == self.debrid and (self.debrid != 'rd' or h[2] == 'True')}
			unchecked_hashes = [i for i in self.hash_list if i not in unchecked_filter]
			if not unchecked_hashes: return self.result()
			if self.debrid == 'rd':
				checked_hashes = self.external_check_cache(unchecked_hashes)
				checked_results = {item: item in checked_hashes for item in unchecked_hashes}
			else: checked_results = self.function().check_cache(unchecked_hashes)
			if not checked_results: return self.result()
			self.checked_list.update(checked_results)
			hashes_to_cache = []
			process_append = hashes_to_cache.append
			cached_append = self.cached_list.append
			for h, is_cached in checked_results.items():
				if is_cached:
					cached_append(h)
					process_append((h, 'True'))
				elif self.debrid != 'rd': process_append((h, 'False'))
			# The check already runs in a worker. Commit before returning so a fallback pass can
			# immediately reuse positives and replace any legacy false row.
			if hashes_to_cache: self.cache_write(hashes_to_cache)
		except: pass
		return self.result()

	def external_check_cache(self, unchecked_hashes):
		checked_hashes, tio_hashes, dmm_hashes = [], [], []
		threads = (
			Thread(target=lambda: tio_hashes.extend(_tio_results(self.imdb, self.season, self.episode))),
			Thread(target=lambda: dmm_hashes.extend(_dmm_results(unchecked_hashes, self.imdb)))
		)
		for i in threads: i.start()
		for i in threads: i.join()
		checked_hashes.extend(tio_hashes)
		checked_hashes.extend(dmm_hashes)
		return checked_hashes

import re, random, requests
from fenom.client import randomagent

session = requests.session()
session.headers.update({'User-Agent': randomagent(), 'Accept': 'application/json'})

_AUXILIARY_CACHE_SECONDS = 60.0
_AUXILIARY_CACHE_MAX_ENTRIES = 32
_auxiliary_cache, _auxiliary_inflight = {}, {}
_auxiliary_lock = Lock()

def _coalesced_auxiliary_results(key, check):
	"""Briefly reuse successful identical checks without persisting auxiliary negatives."""
	now = monotonic()
	with _auxiliary_lock:
		for old_key, (created, _results) in tuple(_auxiliary_cache.items()):
			if now - created > _AUXILIARY_CACHE_SECONDS: _auxiliary_cache.pop(old_key, None)
		cached = _auxiliary_cache.get(key)
		if cached: return list(cached[1])
		waiter = _auxiliary_inflight.get(key)
		if waiter is None:
			waiter, owner = Event(), True
			_auxiliary_inflight[key] = waiter
		else: owner = False
	if not owner:
		waiter.wait(8.0)
		with _auxiliary_lock:
			cached = _auxiliary_cache.get(key)
		return list(cached[1]) if cached else []
	results = []
	try:
		succeeded = check(results)
		if succeeded:
			with _auxiliary_lock:
				_auxiliary_cache[key] = (monotonic(), tuple(dict.fromkeys(results)))
				while len(_auxiliary_cache) > _AUXILIARY_CACHE_MAX_ENTRIES:
					oldest_key = min(_auxiliary_cache, key=lambda item: _auxiliary_cache[item][0])
					_auxiliary_cache.pop(oldest_key, None)
		return results
	finally:
		with _auxiliary_lock:
			_auxiliary_inflight.pop(key, None)
			waiter.set()

def _tio_results(imdb, season, episode):
	key = ('torrentio', imdb, season, episode)
	return _coalesced_auxiliary_results(key, lambda collector: tio_check_cache(imdb, season, episode, collector))

def _dmm_results(unchecked_hashes, imdb):
	hashes = tuple(sorted(set(unchecked_hashes)))
	key = ('dmm', imdb, hashes)
	return _coalesced_auxiliary_results(key, lambda collector: dmm_check_cache(hashes, imdb, collector))

def tio_check_cache(imdb, season, episode, collector):
	if str(season).isdigit(): url = 'series/%s:%s:%s.json' % (imdb, season, episode)
	else: url = 'movie/%s.json' % (imdb)
	params = 'realdebrid=T2iZoymNCCD1T5c2sX5u8tIZVcgcFWlCsCJ72rCmrU2mDdmvgieM'
	url = 'https://torrentio.strem.fun/debridoptions=nodownloadlinks,nocatalog|%s/stream/%s' % (params, url)
	pattern = re.compile(r'\b\w{40}\b')
	try:
		results = session.get(url, timeout=7.05)
		results.raise_for_status()
		payload = results.json()
		files = payload.get('streams') if isinstance(payload, dict) else None
		if not isinstance(files, list) or not all(isinstance(file, dict) for file in files): raise ValueError('invalid Torrentio response schema')
		collector.extend(pattern.findall(file['url'])[-1] for file in files if '+' in file['name'] and 'url' in file)
		return True
	except Exception as e:
		kodi_utils.logger('tio error', str(e))
		return False

def dmm_check_cache(unchecked_hashes_chunk, imdb, collector): # DMM API Allows max 100 hashes per request.
	""" do not thread multiple calls, abusing the api will get it turned off
		100 sample size should be enough """
	from magneto.dmm import get_secret
	unchecked_hashes_chunk = [i for i in unchecked_hashes_chunk if len(i) == 40]
	if len(unchecked_hashes_chunk) > 100: unchecked_hashes_chunk = random.sample(unchecked_hashes_chunk, 100)
	url = 'https://debridmediamanager.com/api/availability/check'
	dmmProblemKey, solution = get_secret()
	data = {'dmmProblemKey': dmmProblemKey, 'solution': solution, 'imdbId': imdb, 'hashes': unchecked_hashes_chunk}
	try:
		results = session.post(url, json=data, timeout=7.05)
		results.raise_for_status()
		payload = results.json()
		files = payload.get('available') if isinstance(payload, dict) else None
		if not isinstance(files, list) or not all(isinstance(file, dict) and isinstance(file.get('hash'), str) and len(file['hash']) == 40 for file in files): raise ValueError('invalid DMM response schema')
		collector.extend(file['hash'] for file in files)
		return True
	except Exception as e:
		kodi_utils.logger('dmm error', str(e))
		return False
