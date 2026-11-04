"""Refresh the curated news catalog in the background while its directory is open."""
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.client import HTTPException
from queue import Empty, SimpleQueue
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from modules import kodi_utils as ku

_REQUEST = 'LiveTV.News.Request'
_RESULT = 'LiveTV.News.Result'
_HEARTBEAT = 'LiveTV.News.Heartbeat'
_RELOAD = 'LiveTV.News.Reload'
_INTERVAL = 120
_MAX_AGE = 24 * 60 * 60
_MAX_BYTES = 4 * 1024 * 1024


def _read(key):
	try:
		value = ku.get_property(key) or '{}'
		if len(value.encode('utf-8')) > _MAX_BYTES: return {}
		result = json.loads(value)
		return result if isinstance(result, dict) else {}
	except (TypeError, ValueError): return {}


def prepare(params):
	"""Return (state, grouped feeds), requesting a background scan if needed."""
	from menus import livetv
	accounts = livetv._feed_accounts_fingerprint()
	now = time.time()
	request, result = _read(_REQUEST), _read(_RESULT)
	if result.get('accounts') != accounts or not 0 <= now - result.get('created', 0) < _MAX_AGE: result = {}
	groups = result.get('groups') if isinstance(result.get('groups'), dict) else {}
	provider_times = result.get('provider_times') if isinstance(result.get('provider_times'), dict) else {}
	def current(entry):
		if not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[0], str): return False
		provider = entry[0].partition(':')[0] if ':' in entry[0] else 'primary'
		created = provider_times.get(provider, 0)
		return isinstance(created, (int, float)) and 0 <= now - created < _MAX_AGE
	groups = {slug: [entry for entry in entries if current(entry)] for slug, entries in groups.items() if isinstance(entries, list)}
	groups = {slug: entries for slug, entries in groups.items() if entries}
	try: alive = 0 <= now - float(ku.get_property(_HEARTBEAT)) < 30
	except (TypeError, ValueError): alive = False
	if not alive: return ('stale' if groups else 'unavailable'), groups
	reload_token = str(params.get('reload') or '')[:128]
	force = bool(reload_token and ku.get_property(_RELOAD) != reload_token)
	if force: ku.set_property(_RELOAD, reload_token)
	active = request.get('accounts') == accounts and request.get('id') and 0 <= now - request.get('created', 0) < _MAX_AGE and (result.get('id') != request.get('id') or not result.get('complete'))
	due = not result or now - result.get('created', 0) >= _INTERVAL
	if force or (due and not active):
		request = {'id': uuid.uuid4().hex, 'accounts': accounts, 'created': now, 'force': force}
		ku.set_property(_REQUEST, json.dumps(request, separators=(',', ':')))
		return ('partial' if groups else 'loading'), groups
	if active: return ('partial' if groups else 'loading'), groups
	if not result: return 'loading', {}
	if not result.get('complete'): return 'partial', groups
	if result.get('failed') or result.get('stale'): return ('stale' if groups else 'unavailable'), groups
	return 'ready', groups


def _visible():
	if not ku.get_visibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.news)'): return False
	try: return parse_qs(urlsplit(ku.get_infolabel('Container.FolderPath')).query).get('mode') == ['livetv.news']
	except ValueError: return False


def _stable_grid():
	try:
		params = parse_qs(urlsplit(ku.get_infolabel('Container.FolderPath')).query, keep_blank_values=True)
		return params.get('mode') == ['livetv.news'] and 'channel' not in params and not ku.get_visibility('Container.Scrolling') and ku.get_visibility('System.IdleTime(1)')
	except ValueError: return False


class NewsRefresh:
	def __init__(self):
		self.results = SimpleQueue()
		self.workers = {}
		self.finished = {}
		self.refreshed = ''
		self.last_auto = 0
		self.next_view_check = 0

	def _scan(self, request, snapshot):
		from menus import livetv
		accounts = livetv._sports_accounts(livetv._load())
		if livetv._feed_accounts_fingerprint() != request['accounts'] or not accounts:
			self.results.put((request['id'], {'id': request['id'], 'accounts': request['accounts'], 'created': time.time(), 'complete': True, 'failed': True, 'groups': {}}))
			return
		previous = snapshot.get('groups', {}) if isinstance(snapshot.get('groups'), dict) else {}
		provider_times = snapshot.get('provider_times', {}) if isinstance(snapshot.get('provider_times'), dict) else {}
		secrets = [str(account.get(field) or '') for _, account in accounts for field in ('username', 'password')]
		buckets = {identity: {} for identity, _ in accounts}
		for slug, entries in previous.items():
			if slug not in livetv._NEWS_CHANNELS or not isinstance(entries, list): continue
			for entry in entries:
				if not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[0], str) or not isinstance(entry[1], dict): continue
				identity = entry[0].partition(':')[0] if ':' in entry[0] else 'primary'
				created = provider_times.get(identity, 0)
				if identity in buckets and isinstance(created, (int, float)) and 0 <= time.time() - created < _MAX_AGE: buckets[identity].setdefault(slug, []).append(entry)
		def fetch(entry):
			identity, account = entry
			status = []
			try:
				streams = livetv._catalog(account, 'get_live_streams', force_refresh=bool(request.get('force')), notify_stale=False, on_status=lambda created, stale: status.append((created, stale)))
				if not isinstance(streams, list): raise ValueError('Invalid catalog')
				found = {}
				for stream in streams:
					if not isinstance(stream, dict): continue
					stream_id = str(stream.get('stream_id') or '')
					name = stream.get('name')
					if not stream_id.isascii() or not stream_id.isdecimal() or not isinstance(name, str) or livetv._catalog_redact(name, secrets) != name: continue
					match = livetv._news_name(name)
					for slug, aliases in livetv._NEWS_CHANNELS.items():
						if match not in aliases: continue
						ref = stream_id if identity == 'primary' else identity + ':' + stream_id
						icon = livetv._catalog_icon(stream.get('stream_icon'), secrets)
						found.setdefault(slug, []).append([ref, {'stream_id': stream_id, 'name': name, 'stream_icon': icon or ''}])
				return identity, found, status[-1] if status else (time.time(), False), False
			except (OSError, HTTPException, ValueError, TypeError): return identity, {}, (0, False), True
		failed = stale = False
		order = {slug: [row[0] for row in rows if isinstance(row, list) and len(row) == 2] for slug, rows in previous.items() if isinstance(rows, list)}
		with ThreadPoolExecutor(max_workers=min(4, len(accounts))) as pool:
			futures = [pool.submit(fetch, entry) for entry in accounts]
			for count, future in enumerate(as_completed(futures), 1):
				identity, found, status, error = future.result()
				created, was_stale = status
				if not error:
					buckets[identity] = found
					provider_times[identity] = created
				failed |= error
				stale |= was_stale
				groups = {}
				for provider, _ in accounts:
					for slug, rows in buckets[provider].items(): groups.setdefault(slug, []).extend(rows)
				for slug, rows in groups.items():
					ranks = {ref: index for index, ref in enumerate(order.get(slug, []))}
					rows.sort(key=lambda row: ranks.get(row[0], len(ranks)))
					order[slug] = [row[0] for row in rows]
				result = {'id': request['id'], 'accounts': request['accounts'], 'created': time.time(), 'complete': count == len(accounts), 'failed': failed, 'stale': stale, 'groups': groups, 'provider_times': provider_times}
				self.results.put((request['id'], result))

	def _work(self, request, snapshot):
		try: self._scan(request, snapshot)
		except Exception:
			self.results.put((request['id'], {'id': request['id'], 'accounts': request['accounts'], 'created': time.time(), 'complete': True, 'failed': True, 'fatal': True, 'groups': {}}))

	def tick(self):
		ku.set_property(_HEARTBEAT, str(time.time()))
		request = _read(_REQUEST)
		request_id = request.get('id', '')
		while True:
			try: result_id, result = self.results.get_nowait()
			except Empty: break
			if result_id != request_id: continue
			if result.get('fatal'):
				saved = _read(_RESULT)
				if saved.get('accounts') == request.get('accounts'):
					result['groups'] = saved.get('groups', {})
					result['provider_times'] = saved.get('provider_times', {})
			encoded = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
			if len(encoded.encode('utf-8')) > _MAX_BYTES:
				saved = _read(_RESULT)
				if saved.get('accounts') != request.get('accounts'): saved = {}
				encoded = json.dumps({'id': result_id, 'accounts': request.get('accounts'), 'created': time.time(), 'complete': True, 'failed': True, 'groups': saved.get('groups', {}), 'provider_times': saved.get('provider_times', {})}, separators=(',', ':'))
				if len(encoded.encode('utf-8')) > _MAX_BYTES: encoded = json.dumps({'id': result_id, 'accounts': request.get('accounts'), 'created': time.time(), 'complete': True, 'failed': True, 'groups': {}})
			ku.set_property(_RESULT, encoded)
		visible = _visible()
		if not visible: return
		# Kodi can restore the destination folder's saved view after the plugin
		# directory has completed, so correct the view only once loading settles.
		if time.monotonic() >= self.next_view_check and not ku.get_visibility('Control.IsVisible(524) | Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'):
			self.next_view_check = time.monotonic() + 2
			ku.execute_builtin('Container.SetViewMode(524)')
			return
		result = _read(_RESULT)
		if request_id and (result.get('id') != request_id or not result.get('complete')) and not ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'):
			for key, worker in list(self.workers.items()):
				if worker.is_alive():
					self.finished.pop(key, None)
				elif key not in self.finished: self.finished[key] = time.monotonic()
				elif time.monotonic() - self.finished[key] >= 1:
					self.workers.pop(key, None)
					self.finished.pop(key, None)
			if request_id not in self.workers and len(self.workers) >= 3:
				saved = result if result.get('accounts') == request.get('accounts') else {}
				ku.set_property(_RESULT, json.dumps({'id': request_id, 'accounts': request.get('accounts'), 'created': time.time(), 'complete': True, 'failed': True, 'groups': saved.get('groups', {}), 'provider_times': saved.get('provider_times', {})}))
			if request_id not in self.workers and len(self.workers) < 3:
				previous = result if result.get('accounts') == request.get('accounts') else {}
				worker = Thread(target=self._work, args=(request, previous), daemon=True)
				self.workers[request_id] = worker
				worker.start()
		if result.get('id') == request_id and request_id:
			version = str(hash(json.dumps(result, sort_keys=True, ensure_ascii=False)))
			if version != self.refreshed and not ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)') and (_stable_grid() or ku.get_visibility('System.IdleTime(2)')):
				self.refreshed = version
				ku.execute_builtin('Container.Refresh')
		if result.get('complete') and time.time() - result.get('created', 0) >= _INTERVAL and time.time() - self.last_auto >= _INTERVAL and not ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)') and ku.get_visibility('System.IdleTime(2)'):
			self.last_auto = time.time()
			ku.execute_builtin('Container.Refresh')

	def close(self):
		ku.clear_property(_HEARTBEAT)
