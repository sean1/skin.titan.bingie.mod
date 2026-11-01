"""Load the matching-feed picker without blocking its Kodi directory."""
import json
import time
import uuid
from queue import Empty, SimpleQueue
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from modules import kodi_utils as ku


_REQUEST = 'LiveTV.FeedSelection.Request'
_RESULT = 'LiveTV.FeedSelection.Result'
_HEARTBEAT = 'LiveTV.FeedSelection.Heartbeat'
_FIELDS = ('sport', 'home', 'away', 'scope', 'session')
_MAX_AGE = 15 * 60
_MAX_RESULT = 4 * 1024 * 1024


def _read(key):
	try:
		value = json.loads(ku.get_property(key) or '{}')
		return value if isinstance(value, dict) else {}
	except (TypeError, ValueError): return {}


def prepare(params):
	"""Return (state, choices) without performing any provider requests."""
	from menus import livetv
	try: alive = 0 <= time.time() - float(ku.get_property(_HEARTBEAT)) < 30
	except (TypeError, ValueError): alive = False
	if not alive: return 'unavailable', []
	query = {key: params.get(key, '') for key in _FIELDS}
	accounts = livetv._feed_accounts_fingerprint()
	request = _read(_REQUEST)
	try: valid = 0 <= time.time() - request['created'] < _MAX_AGE
	except (KeyError, TypeError): valid = False
	if not valid or request.get('query') != query or request.get('accounts') != accounts:
		request = {'id': uuid.uuid4().hex, 'created': time.time(), 'query': query, 'accounts': accounts}
		ku.clear_property(_RESULT)
		ku.set_property(_REQUEST, json.dumps(request, ensure_ascii=False, separators=(',', ':')))
		return 'loading', []
	result = _read(_RESULT)
	if result.get('id') != request.get('id'): return 'loading', []
	if result.get('error'): return 'unavailable', []
	choices = result.get('choices')
	return ('ready', choices) if isinstance(choices, list) else ('unavailable', [])


def _visible(request):
	if not ku.get_visibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.match_feeds)'): return False
	query = parse_qs(urlsplit(ku.get_infolabel('Container.FolderPath')).query, keep_blank_values=True)
	return query.get('mode') == ['livetv.match_feeds'] and all(query.get(key, ['']) == [request.get('query', {}).get(key, '')] for key in _FIELDS)


class FeedSelection:
	def __init__(self):
		self.results = SimpleQueue()
		self.workers = {}
		self.seen_id = ''
		self.refreshed_id = ''

	def _work(self, request):
		try:
			from menus import livetv
			if livetv._feed_accounts_fingerprint() != request['accounts']: raise ValueError('Account changed')
			account = livetv._sports_primary()
			if not account: raise ValueError('Account unavailable')
			query = request['query']
			sport = query['sport']
			if sport:
				groups, _ = livetv._sports_catalog_groups(account, sport, status_sink=lambda value: None, notify_stale=False)
			else:
				groups = livetv._soccer_groups(livetv._catalog(account, 'get_live_streams', notify_stale=False))
			key = query['session'] if sport == 'f1' else ((query['home'], query['away']), query['scope'])
			feeds = groups.get(key, {})
			secrets = [str(account.get(field) or '') for _, account in livetv._sports_accounts(livetv._load()) for field in ('username', 'password')]
			choices = []
			for ref, feed in feeds.items():
				name, stream_id = str(feed.get('name') or ''), feed.get('stream_id')
				if any(livetv._catalog_redact(value, secrets) != value for value in (str(ref), str(stream_id), name)): raise ValueError('Unsafe feed')
				item = {field: feed[field] for field in ('stream_id', '_provider', '_primary', '_guide_match') if field in feed}
				item.update({'name': name, 'stream_icon': livetv._catalog_icon(feed.get('stream_icon'), secrets)})
				choices.append([ref, item, livetv._unique_match(groups, ref, name)])
			encoded = json.dumps({'id': request['id'], 'choices': choices}, ensure_ascii=False, separators=(',', ':'))
			if len(encoded.encode('utf-8')) > _MAX_RESULT: raise ValueError('Result too large')
		except Exception:
			encoded = json.dumps({'id': request['id'], 'error': True})
		self.results.put((request['id'], encoded))

	def tick(self):
		ku.set_property(_HEARTBEAT, str(time.time()))
		request = _read(_REQUEST)
		request_id = request.get('id', '')
		visible = bool(request_id and _visible(request))
		if visible: self.seen_id = request_id
		elif request_id and self.seen_id == request_id:
			if not ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'):
				if _read(_REQUEST).get('id') == request_id:
					if _read(_RESULT).get('id') == request_id: ku.clear_property(_RESULT)
					if _read(_REQUEST).get('id') == request_id: ku.clear_property(_REQUEST)
				self.seen_id = ''
			return
		while True:
			try: result_id, encoded = self.results.get_nowait()
			except Empty: break
			if request_id == result_id and visible: ku.set_property(_RESULT, encoded)
		if not visible: return
		result = _read(_RESULT)
		if result.get('id') == request_id:
			if self.refreshed_id == request_id: return
			if ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'): return
			self.refreshed_id = request_id
			ku.execute_builtin('Container.Refresh')
			return
		self.workers = {key: worker for key, worker in self.workers.items() if worker.is_alive()}
		if request_id in self.workers: return
		if len(self.workers) >= 3:
			ku.set_property(_RESULT, json.dumps({'id': request_id, 'error': True}))
			return
		worker = Thread(target=self._work, args=(request,), daemon=True)
		self.workers[request_id] = worker
		worker.start()

	def close(self):
		ku.clear_property(_HEARTBEAT)
		ku.clear_property(_REQUEST)
		ku.clear_property(_RESULT)
