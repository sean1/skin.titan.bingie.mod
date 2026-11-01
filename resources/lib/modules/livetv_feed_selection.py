"""Load the matching-feed picker without blocking its Kodi directory."""
import json
import math
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
_SLOW_SECONDS = 30


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
	if not valid or request.get('query') != query or request.get('accounts') != accounts or (params.get('retry') and params.get('retry') == request.get('id')):
		request = {'id': uuid.uuid4().hex, 'created': time.time(), 'query': query, 'accounts': accounts}
		ku.clear_property(_RESULT)
		ku.set_property(_REQUEST, json.dumps(request, ensure_ascii=False, separators=(',', ':')))
		return 'loading', []
	result = _read(_RESULT)
	if result.get('id') != request.get('id'):
		return ('slow' if time.time() - request['created'] >= _SLOW_SECONDS else 'loading'), []
	if result.get('error'): return 'unavailable', []
	choices = result.get('choices')
	if isinstance(choices, list):
		now = time.time()
		current = []
		expired = False
		for choice in choices:
			if not isinstance(choice, list) or len(choice) < 2 or not isinstance(choice[1], dict):
				expired = True
				continue
			feed = choice[1]
			if feed.get('_guide_match'):
				until = feed.get('_guide_until')
				try: valid_until = isinstance(until, (int, float)) and not isinstance(until, bool) and math.isfinite(until) and until > now
				except OverflowError: valid_until = False
				if not valid_until:
					expired = True
					continue
			current.append(choice)
		if result.get('incomplete_error') or (expired and result.get('complete', True) is True): return 'partial_unavailable', current
		return ('ready' if result.get('complete', True) is True else 'partial'), current
	return 'unavailable', []


def retry_request():
	return _read(_REQUEST).get('id', '')


def is_slow():
	try: return time.time() - _read(_REQUEST)['created'] >= _SLOW_SECONDS
	except (KeyError, TypeError): return False


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
		self.slow_refreshed_id = ''

	def _work(self, request):
		revision = 0
		last_choices = []
		try:
			from menus import livetv
			if livetv._feed_accounts_fingerprint() != request['accounts']: raise ValueError('Account changed')
			account = livetv._sports_primary()
			if not account: raise ValueError('Account unavailable')
			query = request['query']
			sport = query['sport']
			key = query['session'] if sport == 'f1' else ((query['home'], query['away']), query['scope'])
			secrets = [str(account.get(field) or '') for _, account in livetv._sports_accounts(livetv._load()) for field in ('username', 'password')]
			def publish(groups, complete, unavailable=False):
				nonlocal revision, last_choices
				choices = []
				for ref, feed in groups.get(key, {}).items():
					name, stream_id = str(feed.get('name') or ''), feed.get('stream_id')
					if any(livetv._catalog_redact(value, secrets) != value for value in (str(ref), str(stream_id), name)): raise ValueError('Unsafe feed')
					item = {field: feed[field] for field in ('stream_id', '_provider', '_primary', '_guide_match', '_guide_until') if field in feed}
					item.update({'name': name, 'stream_icon': livetv._catalog_icon(feed.get('stream_icon'), secrets)})
					choices.append([ref, item, livetv._unique_match(groups, ref, name)])
				revision += 1
				encoded = json.dumps({'id': request['id'], 'choices': choices, 'revision': revision, 'complete': complete, 'incomplete_error': bool(unavailable)}, ensure_ascii=False, separators=(',', ':'))
				if len(encoded.encode('utf-8')) > _MAX_RESULT: raise ValueError('Result too large')
				last_choices = choices
				self.results.put((request['id'], encoded))
			unavailable = []
			if sport:
				groups, _ = livetv._sports_catalog_groups(account, sport, status_sink=unavailable.append, notify_stale=False,
					on_partial=lambda groups, unavailable, statuses: publish(groups, False))
			else:
				groups = livetv._soccer_groups(livetv._catalog(account, 'get_live_streams', notify_stale=False))
			publish(groups, True, unavailable[-1] if unavailable else False)
		except Exception:
			revision += 1
			self.results.put((request['id'], json.dumps({'id': request['id'], 'choices': last_choices, 'incomplete_error': True, 'revision': revision, 'complete': True})))

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
			result_key = request_id + ':' + str(result.get('revision', 0))
			slow_refresh = result.get('complete', True) is not True and is_slow() and self.slow_refreshed_id != request_id
			if self.refreshed_id == result_key and not slow_refresh: return
			if ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'): return
			self.refreshed_id = result_key
			if slow_refresh: self.slow_refreshed_id = request_id
			ku.execute_builtin('Container.Refresh')
			return
		try: slow = time.time() - request['created'] >= _SLOW_SECONDS
		except (KeyError, TypeError): slow = False
		if slow and self.slow_refreshed_id != request_id and not ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'):
			self.slow_refreshed_id = request_id
			ku.execute_builtin('Container.Refresh')
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
