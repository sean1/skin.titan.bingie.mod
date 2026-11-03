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
_HANDOFF = 'LiveTV.FeedSelection.Handoffs'
_ORDER = 'LiveTV.FeedSelection.Order'
_HANDOFF_SECONDS = 120
_handoff_cache = None


def _read(key):
	try:
		value = json.loads(ku.get_property(key) or '{}')
		return value if isinstance(value, dict) else {}
	except (TypeError, ValueError): return {}


def _fixture_key(sport, row):
	from menus import livetv
	if sport == 'f1': return row['id']
	if sport == 'soccer': return livetv._soccer_fixture_key(row)
	return (tuple(' '.join(row[side]['name'].casefold().split()) for side in ('home', 'away')), str(row.get('league') or ''))


def _selection(snapshot, query):
	from menus import livetv
	from modules import livetv_sports_progress as progress
	sport = query['sport']
	key = query['session'] if sport == 'f1' else ((query['home'], query['away']), query['scope'])
	matching = [row for row in snapshot['fixtures'] if isinstance(row, dict) and _fixture_key(sport, row) == key]
	if len(matching) != 1 or matching[0].get('status') not in livetv._fixture_service.LIVE_STATUSES or not livetv._fixture_focus_identity(sport, matching[0]): return []
	groups = progress._filter_guide_groups(progress._groups_from_json(snapshot['groups']), time.time())
	return [[ref, feed, livetv._unique_match(groups, ref, str(feed.get('name') or ''))] for ref, feed in groups.get(key, {}).items()]


def handoff(params, fixture, groups):
	"""Save one short-lived snapshot for all event links on this sports page."""
	global _handoff_cache
	from menus import livetv
	from modules import livetv_sports_progress as progress
	try:
		now = time.time()
		query = {key: params.get(key, '') for key in _FIELDS}
		sport = query['sport']
		if _handoff_cache and _handoff_cache[2] is groups:
			snapshot = _handoff_cache[1]
			if not snapshot: return ''
			key = query['session'] if sport == 'f1' else ((query['home'], query['away']), query['scope'])
			matching = [row for row in snapshot['fixtures'] if isinstance(row, dict) and _fixture_key(sport, row) == key]
			identity = livetv._fixture_focus_identity(sport, fixture)
			if snapshot['sport'] == sport and now < snapshot['expires'] and _fixture_key(sport, fixture) == key and len(matching) == 1 and identity and livetv._fixture_focus_identity(sport, matching[0]) == identity: return snapshot['token']
			return ''
		_handoff_cache = (None, None, groups)
		request = progress._read(progress._REQUEST)
		request_id = request.get('id', '')
		accounts = livetv._feed_accounts_fingerprint()
		if not request_id or not progress._current(sport) or not progress._valid(request, sport) or request.get('accounts') != accounts or ku.get_property(progress._CANCEL) == request_id: return ''
		cache_key = (request_id, id(groups))
		if _handoff_cache and _handoff_cache[0] == cache_key and _handoff_cache[2] is groups:
			snapshot = _handoff_cache[1]
		else:
			accepted = progress.accepted_result(sport, now)
			if not accepted or accepted['request'].get('id') != request_id or accepted['groups'] != groups: return ''
			published = accepted['published']
			if not 0 <= now - published < _HANDOFF_SECONDS: return ''
			cache = progress._read(progress._FIXTURE_CACHE + sport)
			if cache.get('accounts') != accounts or cache.get('fixtures') != request['fixtures'] or not isinstance(cache.get('source_expires'), (int, float)) or isinstance(cache.get('source_expires'), bool) or not math.isfinite(cache['source_expires']) or not now < cache['source_expires']: return ''
			snapshot = {'token': uuid.uuid4().hex, 'sport': sport, 'accounts': accounts, 'fixtures': request['fixtures'], 'groups': list(groups.items()), 'expires': min(published + _HANDOFF_SECONDS, cache['source_expires']),
				'unavailable': accepted['unavailable'], 'revision': accepted['revision'], 'request_id': request_id}
			encoded = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':'))
			if len(encoded.encode('utf-8')) > _MAX_RESULT: return ''
			ku.set_property(_HANDOFF, encoded)
			_handoff_cache = (cache_key, snapshot, groups)
		if not now < snapshot['expires'] or _fixture_key(sport, fixture) != (query['session'] if sport == 'f1' else ((query['home'], query['away']), query['scope'])): return ''
		matching = [row for row in snapshot['fixtures'] if isinstance(row, dict) and _fixture_key(sport, row) == _fixture_key(sport, fixture)]
		if len(matching) != 1 or not livetv._fixture_focus_identity(sport, fixture) or livetv._fixture_focus_identity(sport, matching[0]) != livetv._fixture_focus_identity(sport, fixture): return ''
		return snapshot['token']
	except (KeyError, TypeError, ValueError, AttributeError, OverflowError): return ''


def _handoff(params, query, accounts):
	from modules import livetv_sports_progress as progress
	try:
		value = _read(_HANDOFF)
		if not params.get('handoff') or value.get('token') != params.get('handoff') or value.get('sport') != query['sport'] or value.get('accounts') != accounts or not time.time() < value['expires']: return None
		if progress._read(progress._REQUEST).get('id') != value.get('request_id'): return None
		cache = progress._read(progress._FIXTURE_CACHE + query['sport'])
		if cache.get('accounts') != accounts or not isinstance(cache.get('source_expires'), (int, float)) or isinstance(cache.get('source_expires'), bool) or not math.isfinite(cache['source_expires']) or not time.time() < cache['source_expires'] or cache.get('fixtures') != value['fixtures']: return None
		result = progress._read(progress._RESULT + '.' + value['request_id'])
		if result.get('id') == value['request_id'] and result.get('revision', 0) != value['revision']: return None
		choices = _selection(value, query)
		return {'choices': choices, 'unavailable': value.get('unavailable')} if choices else None
	except (KeyError, TypeError, ValueError, AttributeError, OverflowError): return None


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
		request = {'id': uuid.uuid4().hex, 'created': time.time(), 'query': query, 'accounts': accounts, 'handoff': params.get('handoff', '')}
		seed = None if params.get('retry') else _handoff(params, query, accounts)
		if not seed: request['handoff'] = ''
		ku.clear_property(_RESULT)
		ku.clear_property(_ORDER)
		ku.set_property(_REQUEST, json.dumps(request, ensure_ascii=False, separators=(',', ':')))
		if seed:
			ku.set_property(_RESULT, json.dumps({'id': request['id'], 'choices': seed['choices'], 'revision': 0, 'complete': False, 'incomplete_error': seed['unavailable'], 'seeded': True}, ensure_ascii=False, separators=(',', ':')))
		else: return 'loading', []
	result = _read(_RESULT)
	if result.get('id') == request.get('id') and result.get('seeded') and not _handoff(params, query, accounts):
		result['choices'] = result.get('fresh_choices', [])
		result['seeded'] = False
		result['incomplete_error'] = True
		ku.set_property(_RESULT, json.dumps(result, ensure_ascii=False, separators=(',', ':')))
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


def order_choices(choices):
	"""Keep shown feeds in place as new providers finish, dropping removed feeds."""
	request_id = _read(_REQUEST).get('id', '')
	if not request_id: return choices
	by_ref = dict(choices)
	previous = _read(_ORDER)
	order = previous.get('refs', []) if previous.get('id') == request_id else []
	if not isinstance(order, list): order = []
	refs = []
	seen = set()
	for ref in order + [ref for ref, _ in choices]:
		if isinstance(ref, str) and ref in by_ref and ref not in seen:
			refs.append(ref)
			seen.add(ref)
	ku.set_property(_ORDER, json.dumps({'id': request_id, 'refs': refs}, ensure_ascii=False, separators=(',', ':')))
	return [(ref, by_ref[ref]) for ref in refs]


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
		self.seed_started = ''

	def _work(self, request):
		revision = 0
		seed = _handoff(request, request['query'], request['accounts']) if request.get('handoff') else None
		last_choices = [[ref, feed, False] for ref, feed, _ in seed['choices']] if seed else []
		last_fresh = []
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
				nonlocal revision, last_choices, last_fresh
				choices = []
				for ref, feed in groups.get(key, {}).items():
					name, stream_id = str(feed.get('name') or ''), feed.get('stream_id')
					if any(livetv._catalog_redact(value, secrets) != value for value in (str(ref), str(stream_id), name)): raise ValueError('Unsafe feed')
					item = {field: feed[field] for field in ('stream_id', '_provider', '_primary', '_guide_match', '_guide_until') if field in feed}
					item.update({'name': name, 'stream_icon': livetv._catalog_icon(feed.get('stream_icon'), secrets)})
					choices.append([ref, item, livetv._unique_match(groups, ref, name)])
				last_fresh = list(choices)
				using_seed = False
				if not complete or unavailable:
					current_seed = _handoff(request, query, request['accounts']) if request.get('handoff') else None
					if current_seed:
						known = {choice[0] for choice in choices}
						additional = [[ref, feed, False] for ref, feed, _ in current_seed['choices'] if ref not in known]
						choices.extend(additional)
						using_seed = bool(additional)
				revision += 1
				encoded = json.dumps({'id': request['id'], 'choices': choices, 'revision': revision, 'complete': complete, 'incomplete_error': bool(unavailable), 'seeded': using_seed, 'fresh_choices': last_fresh if using_seed else []}, ensure_ascii=False, separators=(',', ':'))
				if len(encoded.encode('utf-8')) > _MAX_RESULT: raise ValueError('Result too large')
				last_choices = choices
				self.results.put((request['id'], encoded))
			unavailable = []
			if sport:
				from modules import livetv_sports_progress as progress
				cached = progress._cached_fixtures(sport, request['accounts'])
				fixture_cache = progress._read(progress._FIXTURE_CACHE + sport)
				try:
					source_expiry = fixture_cache.get('source_expires')
					source_fresh = isinstance(source_expiry, (int, float)) and not isinstance(source_expiry, bool) and math.isfinite(source_expiry) and time.time() < source_expiry
				except (TypeError, ValueError, OverflowError): source_fresh = False
				if not source_fresh or cached is None or fixture_cache.get('accounts') != request['accounts'] or fixture_cache.get('fixtures') != cached[0]: cached = None
				groups, _ = livetv._sports_catalog_groups(account, sport, fixtures=cached[0] if cached is not None else None, overlap_catalogs=cached is None, status_sink=unavailable.append, notify_stale=False,
					on_partial=lambda groups, unavailable, statuses: publish(groups, False))
			else:
				groups = livetv._soccer_groups(livetv._catalog(account, 'get_live_streams', notify_stale=False))
			publish(groups, True, unavailable[-1] if unavailable else False)
		except Exception:
			revision += 1
			current_seed = _handoff(request, request['query'], request['accounts']) if request.get('handoff') else None
			known = {choice[0] for choice in last_fresh}
			additional = [[ref, feed, False] for ref, feed, _ in current_seed['choices'] if ref not in known] if current_seed else []
			last_choices = last_fresh + additional
			self.results.put((request['id'], json.dumps({'id': request['id'], 'choices': last_choices, 'incomplete_error': True, 'revision': revision, 'complete': True, 'seeded': bool(additional), 'fresh_choices': last_fresh if additional else []})))

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
		if result.get('id') == request_id and (result.get('seeded') or (request.get('handoff') and not result.get('complete', True))) and self.seed_started != request_id:
			self.workers = {key: worker for key, worker in self.workers.items() if worker.is_alive()}
			if len(self.workers) < 3:
				worker = Thread(target=self._work, args=(request,), daemon=True)
				self.workers[request_id] = worker
				self.seed_started = request_id
				worker.start()
		if result.get('id') == request_id:
			result_key = request_id + ':' + str(result.get('revision', 0))
			slow_refresh = result.get('complete', True) is not True and is_slow() and self.slow_refreshed_id != request_id
			if self.refreshed_id == result_key and not slow_refresh: return
			if ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)') or not ku.get_visibility('System.IdleTime(2)'): return
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
		ku.clear_property(_ORDER)
		ku.clear_property(_REQUEST)
		ku.clear_property(_RESULT)
