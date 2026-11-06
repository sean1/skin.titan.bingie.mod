"""Find feeds in the background for the selected news channel."""
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


def prepare(channel, params):
	"""Return (state, feeds) for this channel, requesting a scan if needed."""
	from menus import livetv
	accounts = livetv._feed_accounts_fingerprint()
	now = time.time()
	request_key, result_key, reload_key = (key + '.' + channel for key in (_REQUEST, _RESULT, _RELOAD))
	request, result = _read(request_key), _read(result_key)
	if result.get('accounts') != accounts or not 0 <= now - result.get('created', 0) < _MAX_AGE: result = {}
	feeds = result.get('feeds') if isinstance(result.get('feeds'), list) else []
	provider_times = result.get('provider_times') if isinstance(result.get('provider_times'), dict) else {}
	saved_providers = result.get('saved_providers')
	if not isinstance(saved_providers, list): saved_providers = None
	try: alive = 0 <= now - float(ku.get_property(_HEARTBEAT)) < 30
	except (TypeError, ValueError): alive = False
	def current(entry):
		if not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[0], str) or not isinstance(entry[1], dict): return False
		provider = entry[0].partition(':')[0] if ':' in entry[0] else 'primary'
		created = provider_times.get(provider, 0)
		return isinstance(created, (int, float)) and 0 <= now - created < _MAX_AGE
	def saved(entry):
		provider = entry[0].partition(':')[0] if ':' in entry[0] else 'primary'
		return not alive or (provider in saved_providers if saved_providers is not None else bool(result.get('failed') or result.get('stale')))
	feeds = [[entry[0], {**entry[1], '_news_until': provider_times[entry[0].partition(':')[0] if ':' in entry[0] else 'primary'] + _MAX_AGE, '_news_accounts': accounts, '_news_saved': saved(entry)}]
		for entry in feeds if current(entry)]
	if not alive: return ('stale' if feeds else 'unavailable'), feeds
	reload_token = str(params.get('reload') or '')[:128]
	force = bool(reload_token and ku.get_property(reload_key) != reload_token)
	if force: ku.set_property(reload_key, reload_token)
	active = request.get('accounts') == accounts and request.get('id') and 0 <= now - request.get('created', 0) < _MAX_AGE and (result.get('id') != request.get('id') or not result.get('complete'))
	due = not result or now - result.get('created', 0) >= _INTERVAL
	if force or (due and not active):
		request = {'id': uuid.uuid4().hex, 'accounts': accounts, 'created': now, 'force': force}
		ku.set_property(request_key, json.dumps(request, separators=(',', ':')))
		return ('partial' if feeds else 'loading'), feeds
	if active: return ('partial' if feeds else 'loading'), feeds
	if not result: return 'loading', []
	if not result.get('complete'): return 'partial', feeds
	if result.get('failed') or result.get('stale'):
		if result.get('fresh'): return 'degraded', feeds
		return ('stale' if feeds else 'unavailable'), feeds
	return 'ready', feeds


def _visible_params():
	if not ku.get_visibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.news)'): return {}
	try:
		params = parse_qs(urlsplit(ku.get_infolabel('Container.FolderPath')).query, keep_blank_values=True)
		return params if params.get('mode') == ['livetv.news'] else {}
	except ValueError: return {}


class NewsRefresh:
	def __init__(self):
		self.results = SimpleQueue()
		self.workers = {}
		self.refreshed = ''
		self.last_auto = 0
		self.next_view_check = 0

	def _scan(self, channel, request, snapshot):
		from menus import livetv
		accounts = livetv._sports_accounts(livetv._load())
		if livetv._feed_accounts_fingerprint() != request['accounts'] or not accounts:
			self.results.put((channel, {'id': request['id'], 'accounts': request['accounts'], 'created': time.time(), 'complete': True, 'failed': True, 'feeds': []}))
			return
		aliases = livetv._NEWS_CHANNELS[channel]
		previous = snapshot.get('feeds', []) if isinstance(snapshot.get('feeds'), list) else []
		provider_times = snapshot.get('provider_times', {}) if isinstance(snapshot.get('provider_times'), dict) else {}
		secrets = [str(account.get(field) or '') for _, account in accounts for field in ('username', 'password')]
		buckets = {identity: [] for identity, _ in accounts}
		for entry in previous:
			if not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[0], str) or not isinstance(entry[1], dict): continue
			identity = entry[0].partition(':')[0] if ':' in entry[0] else 'primary'
			created = provider_times.get(identity, 0)
			if identity in buckets and isinstance(created, (int, float)) and 0 <= time.time() - created < _MAX_AGE: buckets[identity].append(entry)
		def fetch(entry):
			identity, account = entry
			status = []
			try:
				streams = livetv._catalog(account, 'get_live_streams', force_refresh=bool(request.get('force')), notify_stale=False, on_status=lambda created, stale: status.append((created, stale)))
				if not isinstance(streams, list): raise ValueError('Invalid catalog')
				found = []
				for stream in streams:
					if not isinstance(stream, dict): continue
					stream_id = str(stream.get('stream_id') or '')
					name = stream.get('name')
					if not stream_id.isascii() or not stream_id.isdecimal() or not isinstance(name, str) or livetv._catalog_redact(name, secrets) != name: continue
					if livetv._news_name(name) not in aliases: continue
					ref = stream_id if identity == 'primary' else identity + ':' + stream_id
					icon = livetv._catalog_icon(stream.get('stream_icon'), secrets)
					found.append([ref, {'stream_id': stream_id, 'name': name, 'stream_icon': icon or ''}])
				return identity, found, status[-1] if status else (time.time(), False), False
			except (OSError, HTTPException, ValueError, TypeError): return identity, [], (0, False), True
		failed = stale = fresh = False
		saved_providers = {identity for identity, _ in accounts}
		order = [row[0] for rows in buckets.values() for row in rows]
		with ThreadPoolExecutor(max_workers=min(4, len(accounts))) as pool:
			futures = [pool.submit(fetch, entry) for entry in accounts]
			for count, future in enumerate(as_completed(futures), 1):
				identity, found, status, error = future.result()
				created, was_stale = status
				if not error:
					buckets[identity] = found
					provider_times[identity] = created
					if not was_stale: saved_providers.discard(identity)
				failed |= error
				stale |= was_stale
				fresh |= not error and not was_stale
				feeds = [row for provider, _ in accounts for row in buckets[provider]]
				ranks = {ref: index for index, ref in enumerate(order)}
				feeds.sort(key=lambda row: ranks.get(row[0], len(ranks)))
				order = [row[0] for row in feeds]
				result = {'id': request['id'], 'accounts': request['accounts'], 'created': time.time(), 'complete': count == len(accounts), 'failed': failed, 'stale': stale, 'fresh': fresh, 'saved_providers': sorted(saved_providers), 'feeds': feeds, 'provider_times': provider_times}
				self.results.put((channel, result))

	def _work(self, channel, request, snapshot):
		try: self._scan(channel, request, snapshot)
		except Exception:
			self.results.put((channel, {'id': request['id'], 'accounts': request['accounts'], 'created': time.time(), 'complete': True, 'failed': True, 'fatal': True, 'feeds': []}))

	def tick(self):
		ku.set_property(_HEARTBEAT, str(time.time()))
		# Retire finished workers before applying their final results.
		self.workers = {key: worker for key, worker in self.workers.items() if worker.is_alive()}
		while True:
			try: channel, result = self.results.get_nowait()
			except Empty: break
			request = _read(_REQUEST + '.' + channel)
			result_id = result['id']
			if result_id != request.get('id'): continue
			result_key = _RESULT + '.' + channel
			if result.get('fatal'):
				saved = _read(result_key)
				if saved.get('accounts') == request.get('accounts'):
					result['feeds'] = saved.get('feeds', [])
					result['provider_times'] = saved.get('provider_times', {})
			encoded = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
			if len(encoded.encode('utf-8')) > _MAX_BYTES:
				saved = _read(result_key)
				if saved.get('accounts') != request.get('accounts'): saved = {}
				encoded = json.dumps({'id': result_id, 'accounts': request.get('accounts'), 'created': time.time(), 'complete': True, 'failed': True, 'feeds': saved.get('feeds', []), 'provider_times': saved.get('provider_times', {})}, separators=(',', ':'))
				if len(encoded.encode('utf-8')) > _MAX_BYTES: encoded = json.dumps({'id': result_id, 'accounts': request.get('accounts'), 'created': time.time(), 'complete': True, 'failed': True, 'feeds': []})
			ku.set_property(result_key, encoded)
		params = _visible_params()
		if not params: return
		channel = params.get('channel', [''])[0]
		# Kodi can restore the destination folder's saved view after the plugin
		# directory has completed, so correct the view only once loading settles.
		view = 50 if channel else 524
		busy = 'Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'
		if ku.get_visibility(busy): return
		if time.monotonic() >= self.next_view_check and not ku.get_visibility('Control.IsVisible(%d)' % view):
			self.next_view_check = time.monotonic() + 2
			ku.execute_builtin('Container.SetViewMode(%d)' % view)
			return
		if not channel: return
		request = _read(_REQUEST + '.' + channel)
		request_id = request.get('id', '')
		if not request_id: return
		result_key = _RESULT + '.' + channel
		result = _read(result_key)
		if result.get('id') != request_id or not result.get('complete'):
			if request_id not in self.workers and len(self.workers) < 3:
				previous = result if result.get('accounts') == request.get('accounts') else {}
				worker = Thread(target=self._work, args=(channel, request, previous), daemon=True)
				self.workers[request_id] = worker
				worker.start()
		if result.get('id') == request_id:
			version = str(hash(json.dumps(result, sort_keys=True, ensure_ascii=False)))
			if version != self.refreshed and ku.get_visibility('System.IdleTime(2)'):
				self.refreshed = version
				ku.execute_builtin('Container.Refresh')
				return
		now = time.time()
		if result.get('complete') and now - result.get('created', 0) >= _INTERVAL and now - self.last_auto >= _INTERVAL and ku.get_visibility('System.IdleTime(2)'):
			self.last_auto = now
			ku.execute_builtin('Container.Refresh')

	def close(self):
		ku.clear_property(_HEARTBEAT)
