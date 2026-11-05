"""Build Live TV search results in the service while the directory stays usable."""
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.client import HTTPException
from queue import Empty, SimpleQueue
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from modules import kodi_utils as ku

_REQUEST = 'LiveTV.Search.Request'
_RESULT = 'LiveTV.Search.Result'
_FIRST_ROWS = 25


def _read(key):
	try:
		value = json.loads(ku.get_property(key) or '{}')
		return value if isinstance(value, dict) else {}
	except (TypeError, ValueError): return {}


def prepare(query, token):
	request = _read(_REQUEST)
	if request.get('query') != query or request.get('token') != token or not request.get('id'):
		request = {'id': uuid.uuid4().hex, 'query': query, 'token': token}
		ku.set_property(_REQUEST, json.dumps(request, ensure_ascii=False))
	result = _read(_RESULT)
	return result if result.get('id') == request['id'] else None


def _visible(request):
	if not ku.get_visibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.search_results)'): return False
	params = parse_qs(urlsplit(ku.get_infolabel('Container.FolderPath')).query, keep_blank_values=True)
	return params.get('mode') == ['livetv.search_results'] and params.get('query') == [request.get('query')] and params.get('search_id', ['']) == [request.get('token', '')]


def _work(request, queue):
	from menus import livetv
	account = livetv._load()
	accounts = livetv._sports_accounts(account) if account else []
	if not accounts:
		queue.put({'id': request['id'], 'rows': [], 'complete': True, 'unavailable': True, 'error': True})
		return
	deadline = time.monotonic() + livetv._SPORTS_SEARCH_SECONDS

	def fetch(entry):
		identity, provider = entry
		status = []
		try:
			try:
				catalog = livetv._catalog(provider, 'get_live_streams', notify_stale=False, on_status=lambda created, stale: status.append(stale), _deadline=deadline)
			except (OSError, HTTPException, ValueError, TypeError):
				if time.monotonic() < deadline: raise
				def expired(*args, **kwargs): raise OSError('Live TV catalog timed out')
				catalog = livetv._catalog_get(provider, 'get_live_streams', expired, ku.translate_path(ku.profile_path), on_status=lambda created, stale: status.append(stale))
			if not isinstance(catalog, list): raise ValueError('Invalid response')
			return identity, catalog, status
		except (OSError, HTTPException, ValueError, TypeError):
			return identity, None, status

	shown = []
	artwork_cache = {}
	published = False

	def publish(results, complete):
		nonlocal published
		streams, unavailable, stale = [], False, False
		for index, entry in enumerate(accounts):
			result = results.get(index)
			if result is None: continue
			identity, catalog, status = result
			if catalog is None:
				unavailable = True
				continue
			stale = stale or any(status)
			streams.extend(dict(item, _search_provider=identity, _search_provider_number=index + 1) for item in catalog if isinstance(item, dict) and str(item.get('stream_id') or '').isdecimal())
		rows = livetv._search_rows(request['query'], accounts, streams)
		limit = livetv._SEARCH_RESULT_LIMIT if complete else _FIRST_ROWS
		by_identity, occurrences = {}, {}
		previous = set(shown)
		for index, row in enumerate(rows):
			key = json.dumps(row[1], sort_keys=True, ensure_ascii=False)
			occurrences[key] = occurrences.get(key, 0) + 1
			identity = (key, occurrences[key])
			if index < livetv._SEARCH_RESULT_LIMIT or identity in previous: by_identity[identity] = row
		order = [identity for identity in shown if identity in by_identity]
		order.extend(identity for identity in by_identity if identity not in order)
		order = order[:limit]
		shown[:] = order
		selected = [by_identity[identity] for identity in order]

		def artwork(icon):
			try:
				parts = urlsplit(icon)
				if parts.scheme.lower() in ('http', 'https'): return livetv._vpn_register(icon)
				if parts.scheme.lower() in ('', 'special', 'resource') and (parts.scheme or not icon.startswith('//')): return icon
			except (OSError, HTTPException, ValueError, TypeError): pass
			return None

		icons = list(dict.fromkeys(row[3] for row in selected if row[3] and row[3] not in artwork_cache))
		with ThreadPoolExecutor(max_workers=8) as pool:
			for start in range(0, len(icons), 8):
				if _read(_REQUEST).get('id') != request['id']: return
				batch = icons[start:start + 8]
				for icon, registered in zip(batch, pool.map(artwork, batch)):
					if registered: artwork_cache[icon] = registered
					while len(artwork_cache) > livetv._SEARCH_RESULT_LIMIT + _FIRST_ROWS: artwork_cache.pop(next(iter(artwork_cache)))
		prepared = [(label, params, folder, artwork_cache.get(icon)) for label, params, folder, icon in selected]
		published = True
		queue.put({'id': request['id'], 'rows': prepared, 'complete': complete, 'unavailable': unavailable, 'has_streams': bool(streams), 'stale': stale, 'truncated': complete and len(rows) > livetv._SEARCH_RESULT_LIMIT})

	results = {}
	from modules.livetv_catalog import saved_streams
	with ThreadPoolExecutor(max_workers=min(4, len(accounts))) as pool:
		futures = {pool.submit(fetch, entry): index for index, entry in enumerate(accounts)}
		for index, (identity, provider) in enumerate(accounts):
			if _read(_REQUEST).get('id') != request['id']: return
			cached = saved_streams(provider, ku.translate_path(ku.profile_path))
			if cached:
				results[index] = (identity, cached[0], [True])
				publish(results, False)
		for completed, future in enumerate(as_completed(futures), 1):
			if _read(_REQUEST).get('id') != request['id']: return
			results[futures[future]] = future.result()
			if completed < len(accounts) or not published: publish(results, False)
		publish(results, True)



class LiveTVSearch:
	def __init__(self):
		self.results = SimpleQueue()
		self.workers = {}
		self.refreshed = ''
		self.visible_id = ''
		self.completed_id = ''

	def pending(self):
		request = _read(_REQUEST)
		request_id = request.get('id')
		if not request_id or request_id == self.completed_id or not _visible(request): return False
		result = _read(_RESULT)
		return result.get('id') != request_id or not result.get('complete') or result.get('revision') != self.refreshed

	def tick(self):
		request = _read(_REQUEST)
		request_id = request.get('id', '')
		visible = bool(request_id and _visible(request))
		if not visible or request_id == self.completed_id: return
		if self.visible_id != request_id: self.refreshed = ''
		self.visible_id = request_id
		while True:
			try: result = self.results.get_nowait()
			except Empty: break
			if result.get('id') != request_id or _read(_REQUEST).get('id') != request_id: continue
			if result.get('error'):
				previous = _read(_RESULT)
				if previous.get('id') == request_id: result.update({key: previous[key] for key in ('rows', 'stale', 'has_streams', 'truncated') if key in previous})
			result['revision'] = uuid.uuid4().hex
			ku.set_property(_RESULT, json.dumps(result, ensure_ascii=False, separators=(',', ':')))
		result = _read(_RESULT)
		if result.get('id') == request_id and result.get('revision') != self.refreshed:
			if ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'): return
			if self.refreshed and not ku.get_visibility('System.IdleTime(2)'): return
			if not _visible(request) or _read(_REQUEST).get('id') != request_id: return
			self.refreshed = result['revision']
			if result.get('complete'): self.completed_id = request_id
			ku.execute_builtin('Container.Refresh')
			return
		self.workers = {key: worker for key, worker in self.workers.items() if worker.is_alive()}
		if request_id not in self.workers and (result.get('id') != request_id or not result.get('complete')) and len(self.workers) < 3:
			worker = Thread(target=self._run, args=(request,), daemon=True)
			self.workers[request_id] = worker
			worker.start()

	def _run(self, request):
		try: _work(request, self.results)
		except Exception:
			self.results.put({'id': request['id'], 'rows': [], 'complete': True, 'unavailable': True, 'error': True})
