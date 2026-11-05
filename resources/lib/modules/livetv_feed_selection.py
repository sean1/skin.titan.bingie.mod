"""Load the matching-feed picker without blocking its Kodi directory."""
import hashlib
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


def _timing(phase, created):
	if phase not in ('request', 'first_choices', 'refresh'): return
	try:
		elapsed = time.time() - created
		if not math.isfinite(elapsed) or not 0 <= elapsed <= _MAX_AGE: return
		ku.xbmc.log('BINGIE picker timing phase=%s elapsed_ms=%d' % (phase, round(elapsed * 1000)), ku.xbmc.LOGINFO)
	except (TypeError, ValueError, AttributeError, OverflowError): pass


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
				'unavailable': accepted['unavailable'], 'saved': accepted.get('saved', False), 'revision': accepted['revision'], 'request_id': request_id}
			encoded = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':'))
			if len(encoded.encode('utf-8')) > _MAX_RESULT: return ''
			ku.set_property(_HANDOFF, encoded)
			_handoff_cache = (cache_key, snapshot, groups)
		if not now < snapshot['expires'] or _fixture_key(sport, fixture) != (query['session'] if sport == 'f1' else ((query['home'], query['away']), query['scope'])): return ''
		matching = [row for row in snapshot['fixtures'] if isinstance(row, dict) and _fixture_key(sport, row) == _fixture_key(sport, fixture)]
		if len(matching) != 1 or not livetv._fixture_focus_identity(sport, fixture) or livetv._fixture_focus_identity(sport, matching[0]) != livetv._fixture_focus_identity(sport, fixture): return ''
		return snapshot['token']
	except (KeyError, TypeError, ValueError, AttributeError, OverflowError): return ''


def overview_handoff(params, fixture, groups):
	"""Bind a picker link to the current, short-lived overview snapshot."""
	from menus import livetv
	from modules import livetv_sports_overview as overview, livetv_sports_progress as progress
	try:
		sport = params.get('sport', '')
		if sport not in overview.SPORTS: return ''
		raw = ku.get_property(overview.PREFIX + sport)
		value = json.loads(raw or '{}')
		now = time.time()
		if value.get('accounts') != livetv._feed_accounts_fingerprint() or not 0 <= now - value['created'] < _HANDOFF_SECONDS or not _overview_fixture_fresh(value, now): return ''
		if progress._filter_guide_groups(progress._groups_from_json(value['groups']), now) != groups: return ''
		query = {key: params.get(key, '') for key in _FIELDS}
		key = query['session'] if sport == 'f1' else ((query['home'], query['away']), query['scope'])
		matching = [row for row in value['fixtures'] if isinstance(row, dict) and _fixture_key(sport, row) == key]
		identity = livetv._fixture_focus_identity(sport, fixture)
		if _fixture_key(sport, fixture) != key or len(matching) != 1 or matching[0].get('status') not in livetv._fixture_service.LIVE_STATUSES or not identity or livetv._fixture_focus_identity(sport, matching[0]) != identity: return ''
		return 'o' + hashlib.sha256(raw.encode('utf-8')).hexdigest()
	except (KeyError, TypeError, ValueError, AttributeError, OverflowError): return ''


def _overview_fixture_fresh(value, now):
	expires = value.get('fixture_expires')
	return isinstance(expires, (int, float)) and not isinstance(expires, bool) and math.isfinite(expires) and now < expires


def _overview_handoff(token, query, accounts):
	from menus import livetv
	from modules import livetv_sports_overview as overview
	try:
		if query['sport'] not in overview.SPORTS: return None
		raw = ku.get_property(overview.PREFIX + query['sport'])
		if token != 'o' + hashlib.sha256(raw.encode('utf-8')).hexdigest(): return None
		value = json.loads(raw or '{}')
		if value.get('accounts') != accounts or not 0 <= time.time() - value['created'] < _HANDOFF_SECONDS or not _overview_fixture_fresh(value, time.time()): return None
		choices = _selection(value, query)
		key = query['session'] if query['sport'] == 'f1' else ((query['home'], query['away']), query['scope'])
		matches = [fixture for fixture in value['fixtures'] if isinstance(fixture, dict) and _fixture_key(query['sport'], fixture) == key]
		identity = livetv._fixture_focus_identity(query['sport'], matches[0]) if len(matches) == 1 else ''
		return {'choices': choices, 'unavailable': value.get('unavailable'), 'saved': bool(value.get('stale')), 'fixture_identity': identity} if choices and identity else None
	except (KeyError, TypeError, ValueError, AttributeError, OverflowError): return None


def _handoff(params, query, accounts):
	from modules import livetv_sports_progress as progress
	try:
		token = params.get('handoff', '')
		if isinstance(token, str) and token.startswith('o'): return _overview_handoff(token, query, accounts)
		value = _read(_HANDOFF)
		if not params.get('handoff') or value.get('token') != params.get('handoff') or value.get('sport') != query['sport'] or value.get('accounts') != accounts or not time.time() < value['expires']: return None
		if progress._read(progress._REQUEST).get('id') != value.get('request_id'): return None
		cache = progress._read(progress._FIXTURE_CACHE + query['sport'])
		if cache.get('accounts') != accounts or not isinstance(cache.get('source_expires'), (int, float)) or isinstance(cache.get('source_expires'), bool) or not math.isfinite(cache['source_expires']) or not time.time() < cache['source_expires'] or cache.get('fixtures') != value['fixtures']: return None
		result = progress._read(progress._RESULT + '.' + value['request_id'])
		if result.get('id') == value['request_id'] and result.get('revision', 0) != value['revision']: return None
		choices = _selection(value, query)
		return {'choices': choices, 'unavailable': value.get('unavailable'), 'saved': bool(value.get('saved'))} if choices else None
	except (KeyError, TypeError, ValueError, AttributeError, OverflowError): return None



def _retention_identity(query, accounts):
	"""Identify a unique live event only while its fixture source is fresh."""
	from menus import livetv
	from modules import livetv_sports_progress as progress
	try:
		sport = query['sport']
		cached = progress._cached_fixtures(sport, accounts)
		cache = progress._read(progress._FIXTURE_CACHE + sport)
		expires = cache.get('source_expires')
		if cached is None or cache.get('accounts') != accounts or not isinstance(expires, (int, float)) or isinstance(expires, bool) or not math.isfinite(expires) or not time.time() < expires: return ''
		key = query['session'] if sport == 'f1' else ((query['home'], query['away']), query['scope'])
		matches = [fixture for fixture in cached[0] if isinstance(fixture, dict) and _fixture_key(sport, fixture) == key]
		if len(matches) != 1 or matches[0].get('status') not in livetv._fixture_service.LIVE_STATUSES: return ''
		return livetv._fixture_focus_identity(sport, matches[0])
	except (AttributeError, KeyError, TypeError, ValueError, OverflowError): return ''


def _overview_identity(query, accounts, token):
	value = _overview_handoff(token, query, accounts) if token else None
	return value.get('fixture_identity', '') if value else ''


def _request_current(request):
	from menus import livetv
	try: return bool(0 <= time.time() - request['created'] < _MAX_AGE and request['accounts'] == livetv._feed_accounts_fingerprint())
	except (KeyError, TypeError, ValueError, OverflowError): return False


def _retainable(request):
	try:
		identity = request.get('fixture_identity')
		return bool(identity and _request_current(request) and not _fresh_cache_conflicts(request) and (
			_retention_identity(request['query'], request['accounts']) == identity or _overview_identity(request['query'], request['accounts'], request.get('handoff', '')) == identity))
	except (KeyError, TypeError, ValueError, OverflowError): return False



def _fresh_cache_conflicts(request):
	from modules import livetv_sports_progress as progress
	try:
		sport, accounts = request['query']['sport'], request['accounts']
		cache = progress._read(progress._FIXTURE_CACHE + sport)
		expires = cache.get('source_expires')
		if progress._cached_fixtures(sport, accounts) is None or cache.get('accounts') != accounts or not isinstance(expires, (int, float)) or isinstance(expires, bool) or not math.isfinite(expires) or not time.time() < expires: return False
		return _retention_identity(request['query'], accounts) != request.get('fixture_identity')
	except (AttributeError, KeyError, TypeError, ValueError, OverflowError): return True


def prepare(params):
	"""Return (state, choices) without performing any provider requests."""
	from menus import livetv
	try: alive = 0 <= time.time() - float(ku.get_property(_HEARTBEAT)) < 30
	except (TypeError, ValueError): alive = False
	if not alive: return 'unavailable', []
	query = {key: params.get(key, '') for key in _FIELDS}
	accounts = livetv._feed_accounts_fingerprint()
	expected_identity = params.get('fixture_identity', '')
	if expected_identity and (not isinstance(expected_identity, str) or len(expected_identity) != 64 or any(char not in '0123456789abcdef' for char in expected_identity)): return 'unavailable', []
	request = _read(_REQUEST)
	token = params.get('handoff', '')
	if isinstance(token, str) and token.startswith('o') and expected_identity and not _overview_handoff(token, query, accounts):
		token = ''
		params = dict(params, handoff='')
	try: valid = 0 <= time.time() - request['created'] < _MAX_AGE
	except (KeyError, TypeError): valid = False
	same_handoff = valid and not request.get('validation_failed') and request.get('handoff') == token and request.get('query') == query and request.get('accounts') == accounts
	if isinstance(token, str) and token.startswith('o') and not same_handoff:
		seed = _overview_handoff(token, query, accounts)
		if not seed: return 'unavailable', []
		identity = seed['fixture_identity']
		if _fresh_cache_conflicts({'query': query, 'accounts': accounts, 'fixture_identity': identity}): return 'unavailable', []
		if valid and request.get('query') == query and request.get('accounts') == accounts and request.get('fixture_identity') and request['fixture_identity'] != identity: return 'unavailable', []
	compatible = valid and not request.get('validation_failed') and request.get('query') == query and request.get('accounts') == accounts and (not expected_identity or request.get('fixture_identity') == expected_identity) and not (params.get('retry') and params.get('retry') == request.get('id'))
	if compatible and request.get('validated_until'):
		try: validated = isinstance(request['validated_until'], (int, float)) and not isinstance(request['validated_until'], bool) and math.isfinite(request['validated_until']) and time.time() < request['validated_until']
		except (TypeError, ValueError, OverflowError): validated = False
		if not validated or _fresh_cache_conflicts(request): request['retained'] = True
	if compatible and request.get('retained') and not _retainable(request):
		result = _read(_RESULT)
		if request.get('fixture_identity') and result.get('id') == request.get('id') and result.get('complete') is True and not result.get('seeded') and isinstance(result.get('choices'), list) and not request.get('validation_failed'):
			if not request.get('validating'):
				request.pop('validation_started', None)
				request['validating'] = True
				request['validation_token'] = uuid.uuid4().hex
				ku.set_property(_REQUEST, json.dumps(request, ensure_ascii=False, separators=(',', ':')))
			return 'loading', []
		compatible = False
	if not compatible:
		if isinstance(token, str) and token.startswith('o'):
			seed = _overview_handoff(token, query, accounts)
			if not seed or _fresh_cache_conflicts({'query': query, 'accounts': accounts, 'fixture_identity': seed['fixture_identity']}): return 'unavailable', []
		request = {'id': uuid.uuid4().hex, 'created': time.time(), 'query': query, 'accounts': accounts, 'handoff': params.get('handoff', ''), 'fixture_identity': expected_identity or _retention_identity(query, accounts)}
		_timing('request', request['created'])
		seed = None if params.get('retry') else _handoff(params, query, accounts)
		if seed and seed.get('fixture_identity') and request['fixture_identity'] and seed['fixture_identity'] != request['fixture_identity']: seed = None
		if seed and not request['fixture_identity']: request['fixture_identity'] = seed.get('fixture_identity', '')
		if not seed: request['handoff'] = ''
		ku.clear_property(_RESULT)
		ku.clear_property(_ORDER)
		ku.set_property(_REQUEST, json.dumps(request, ensure_ascii=False, separators=(',', ':')))
		if seed:
			ku.set_property(_RESULT, json.dumps({'id': request['id'], 'choices': seed['choices'], 'revision': 0, 'complete': False, 'incomplete_error': seed['unavailable'], 'saved': seed.get('saved', False), 'seeded': True}, ensure_ascii=False, separators=(',', ':')))
			_timing('first_choices', request['created'])
		else: return 'loading', []
	elif request.get('retained') and not ku.get_visibility('Player.HasMedia'):
		request.pop('retained', None)
		request.pop('validating', None)
		request.pop('validation_started', None)
		request.pop('validation_token', None)
		ku.set_property(_REQUEST, json.dumps(request, ensure_ascii=False, separators=(',', ':')))
	result = _read(_RESULT)
	if result.get('id') == request.get('id') and result.get('seeded') and not _handoff(params, query, accounts):
		result['choices'] = result.get('fresh_choices', [])
		result['saved'] = result.get('fresh_saved', False)
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
		if result.get('incomplete_error') or (expired and result.get('complete', True) is True): return ('partial_saved_unavailable' if result.get('saved') else 'partial_unavailable'), current
		state = 'ready' if result.get('complete', True) is True else 'partial'
		return (state + '_saved' if result.get('saved') else state), current
	return 'unavailable', []


def order_choices(choices):
	"""Keep shown rows in place, disabling removed feeds until the picker is left."""
	request_id = _read(_REQUEST).get('id', '')
	if not request_id: return choices
	by_ref = dict(choices)
	previous = _read(_ORDER)
	order = previous.get('refs', []) if previous.get('id') == request_id else []
	if not isinstance(order, list): order = []
	refs = []
	seen = set()
	for ref in order + [ref for ref, _ in choices]:
		if isinstance(ref, str) and ref not in seen:
			refs.append(ref)
			seen.add(ref)
	ku.set_property(_ORDER, json.dumps({'id': request_id, 'refs': refs}, ensure_ascii=False, separators=(',', ':')))
	return [(ref, by_ref.get(ref, {'_unavailable': True})) for ref in refs]


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
		self.quick_pending = ''
		self.validations = SimpleQueue()
		self.validation_worker = None
		self.validating_id = ''
		self.timed_refresh_id = ''

	def pending(self):
		request = _read(_REQUEST)
		if not request.get('id') or not _visible(request) or ku.get_visibility('Player.HasMedia'): return False
		result = _read(_RESULT)
		return result.get('id') != request['id'] or result.get('complete', True) is not True

	def _work(self, request):
		revision = 0
		seed = _handoff(request, request['query'], request['accounts']) if request.get('handoff') else None
		last_choices = [[ref, feed, False] for ref, feed, _ in seed['choices']] if seed else []
		last_fresh = []
		last_saved = bool(seed and seed.get('saved'))
		timed_choices = bool(seed and seed.get('choices'))
		observed_identity = ''
		try:
			from menus import livetv
			if livetv._feed_accounts_fingerprint() != request['accounts']: raise ValueError('Account changed')
			account = livetv._sports_primary()
			if not account: raise ValueError('Account unavailable')
			query = request['query']
			sport = query['sport']
			key = query['session'] if sport == 'f1' else ((query['home'], query['away']), query['scope'])
			def observe_fixtures(fixtures):
				nonlocal observed_identity
				matches = [fixture for fixture in fixtures if isinstance(fixture, dict) and _fixture_key(sport, fixture) == key]
				if len(matches) != 1 or matches[0].get('status') not in livetv._fixture_service.LIVE_STATUSES: return
				identity = livetv._fixture_focus_identity(sport, matches[0])
				observed_identity = identity
				if request.get('fixture_identity') and request['fixture_identity'] != identity: raise ValueError('Fixture changed')
			secrets = [str(account.get(field) or '') for _, account in livetv._sports_accounts(livetv._load()) for field in ('username', 'password')]
			def publish(groups, complete, unavailable=False, saved=False):
				nonlocal revision, last_choices, last_fresh, timed_choices
				fresh_saved = saved
				choices = []
				for ref, feed in groups.get(key, {}).items():
					name, stream_id = str(feed.get('name') or ''), feed.get('stream_id')
					if any(livetv._catalog_redact(value, secrets) != value for value in (str(ref), str(stream_id), name)): raise ValueError('Unsafe feed')
					item = {field: feed[field] for field in ('stream_id', '_provider', '_primary', '_guide_match', '_guide_until') if field in feed}
					item.update({'name': name, 'stream_icon': livetv._catalog_icon(feed.get('stream_icon'), secrets)})
					choices.append([ref, item, complete and not unavailable and livetv._unique_match(groups, ref, name)])
				last_fresh = list(choices)
				using_seed = False
				if not complete or unavailable:
					current_seed = _handoff(request, query, request['accounts']) if request.get('handoff') else None
					if current_seed:
						known = {choice[0] for choice in choices}
						additional = [[ref, feed, False] for ref, feed, _ in current_seed['choices'] if ref not in known]
						choices.extend(additional)
						using_seed = bool(additional)
						if using_seed and current_seed.get('saved'): saved = True
				revision += 1
				encoded = json.dumps({'id': request['id'], 'choices': choices, 'revision': revision, 'complete': complete, 'incomplete_error': bool(unavailable), 'saved': bool(saved), 'seeded': using_seed, 'fresh_choices': last_fresh if using_seed else [], 'fresh_saved': bool(fresh_saved), 'fixture_identity': observed_identity}, ensure_ascii=False, separators=(',', ':'))
				if len(encoded.encode('utf-8')) > _MAX_RESULT: raise ValueError('Result too large')
				last_choices = choices
				if choices and not timed_choices:
					_timing('first_choices', request['created'])
					timed_choices = True
				self.results.put((request['id'], encoded))
			unavailable = []
			if sport:
				def publish_partial(groups, partial_unavailable, statuses):
					nonlocal last_saved
					last_saved = any(stale for _, stale in statuses)
					publish(groups, False, partial_unavailable, last_saved)
				from modules import livetv_sports_progress as progress
				cached = progress._cached_fixtures(sport, request['accounts'])
				fixture_cache = progress._read(progress._FIXTURE_CACHE + sport)
				try:
					source_expiry = fixture_cache.get('source_expires')
					source_fresh = isinstance(source_expiry, (int, float)) and not isinstance(source_expiry, bool) and math.isfinite(source_expiry) and time.time() < source_expiry
				except (TypeError, ValueError, OverflowError): source_fresh = False
				if not source_fresh or cached is None or fixture_cache.get('accounts') != request['accounts'] or fixture_cache.get('fixtures') != cached[0]: cached = None
				groups, _ = livetv._sports_catalog_groups(account, sport, fixtures=cached[0] if cached is not None else None, overlap_catalogs=cached is None, status_sink=unavailable.append, notify_stale=False, picker_priority=True, selected_fixture=key,
					on_partial=publish_partial, on_fixtures=observe_fixtures)
			else:
				groups = livetv._soccer_groups(livetv._catalog(account, 'get_live_streams', notify_stale=False))
			publish(groups, True, unavailable[-1] if unavailable else False, last_saved)
		except Exception:
			revision += 1
			if observed_identity and request.get('fixture_identity') and observed_identity != request['fixture_identity']:
				self.results.put((request['id'], json.dumps({'id': request['id'], 'fixture_identity': observed_identity, 'revision': revision, 'complete': True})))
				return
			current_seed = _handoff(request, request['query'], request['accounts']) if request.get('handoff') else None
			known = {choice[0] for choice in last_fresh}
			additional = [[ref, feed, False] for ref, feed, _ in current_seed['choices'] if ref not in known] if current_seed else []
			last_choices = last_fresh + additional
			self.results.put((request['id'], json.dumps({'id': request['id'], 'choices': last_choices, 'incomplete_error': True, 'saved': last_saved or bool(additional and current_seed.get('saved')), 'revision': revision, 'complete': True, 'seeded': bool(additional), 'fresh_choices': last_fresh if additional else [], 'fresh_saved': last_saved})))

	def _validate_fixture(self, request):
		"""Recheck the event without fetching the provider catalogs again."""
		expires = 0
		try:
			from menus import livetv
			if not _request_current(request): raise ValueError('Account changed')
			query = request['query']
			sport = query['sport']
			if sport == 'soccer':
				(fixtures, _, _, _), expires = livetv._live_soccer_fixtures(include_upcoming=True, with_expiry=True)
			elif sport == 'f1': fixtures, expires = livetv._f1.get_schedule(with_expiry=True)
			elif sport in ('hockey', 'football', 'basketball', 'tennis'):
				profile = ku.translate_path(getattr(ku, 'profile_path', ''))
				service = livetv._tennis_fixtures_service if sport == 'tennis' else livetv._american_fixtures
				fixtures, expires = service.get_schedule(cache_dir=profile or None, with_expiry=True) if sport == 'tennis' else service.get_schedule(sport, cache_dir=profile or None, with_expiry=True)
			else: raise ValueError('Unknown sport')
			if not isinstance(expires, (int, float)) or isinstance(expires, bool) or not math.isfinite(expires) or not time.time() < expires: raise ValueError('Stale fixtures')
			key = query['session'] if sport == 'f1' else ((query['home'], query['away']), query['scope'])
			matches = [fixture for fixture in fixtures if isinstance(fixture, dict) and _fixture_key(sport, fixture) == key]
			if len(matches) != 1 or matches[0].get('status') not in livetv._fixture_service.LIVE_STATUSES or livetv._fixture_focus_identity(sport, matches[0]) != request.get('fixture_identity') or not _request_current(request): raise ValueError('Fixture changed')
		except Exception: expires = 0
		self.validations.put((request['id'], request.get('validation_token'), expires))

	def tick(self):
		ku.set_property(_HEARTBEAT, str(time.time()))
		request = _read(_REQUEST)
		request_id = request.get('id', '')
		visible = bool(request_id and _visible(request))
		seed = _read(_RESULT)
		quick_play = bool(request_id and not visible and self.seen_id != request_id and request.get('handoff') and seed.get('id') == request_id and seed.get('seeded') and ku.get_visibility('Player.HasMedia') and _retainable(request) and 0 <= time.time() - request['created'] < 30)
		if quick_play:
			self.seen_id = request_id
			self.quick_pending = request_id
		playing = bool(request_id and self.seen_id == request_id and ku.get_visibility('Player.HasMedia'))
		if playing and not visible and not request.get('retained') and (_retainable(request) or (request.get('fixture_observed') and _request_current(request) and not _fresh_cache_conflicts(request))) and _read(_REQUEST).get('id') == request_id:
			request['retained'] = True
			ku.set_property(_REQUEST, json.dumps(request, ensure_ascii=False, separators=(',', ':')))
		retaining = bool(request_id and request.get('retained') and _request_current(request))
		background = bool(playing and _request_current(request))
		if not visible and self.seen_id == request_id and not ku.get_visibility('Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'): ku.clear_property(_ORDER)
		if visible: self.seen_id = request_id
		elif request_id and self.seen_id == request_id and not retaining and not background:
			if not ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'):
				if _read(_REQUEST).get('id') == request_id:
					if _read(_RESULT).get('id') == request_id: ku.clear_property(_RESULT)
					if _read(_REQUEST).get('id') == request_id: ku.clear_property(_REQUEST)
				self.seen_id = ''
			return
		while True:
			try: result_id, encoded = self.results.get_nowait()
			except Empty: break
			if request_id != result_id or request.get('identity_conflict') or not (visible or retaining or background) or not _request_current(request) or _read(_REQUEST).get('id') != request_id: continue
			try: identity = json.loads(encoded).get('fixture_identity')
			except (TypeError, ValueError, AttributeError): identity = ''
			candidate = dict(request, fixture_identity=identity or request.get('fixture_identity'))
			if (identity and request.get('fixture_identity') and request['fixture_identity'] != identity) or (candidate.get('fixture_identity') and _fresh_cache_conflicts(candidate)):
				request['identity_conflict'] = True
				ku.set_property(_REQUEST, json.dumps(request, ensure_ascii=False, separators=(',', ':')))
				ku.set_property(_RESULT, json.dumps({'id': request_id, 'error': True, 'complete': True, 'revision': -1}))
				continue
			if identity:
				request['fixture_identity'] = identity
				request['fixture_observed'] = True
				if background: request['retained'] = True
				ku.set_property(_REQUEST, json.dumps(request, ensure_ascii=False, separators=(',', ':')))
				retaining = bool(request.get('retained'))
			if visible or retaining: ku.set_property(_RESULT, encoded)
		if not visible:
			# Playback may begin from a handed-off seed before the picker gets a service tick.
			if self.quick_pending == request_id and retaining and self.seed_started != request_id:
				self.workers = {key: worker for key, worker in self.workers.items() if worker.is_alive()}
				if len(self.workers) < 3:
					worker = Thread(target=self._work, args=(request,), daemon=True)
					self.workers[request_id] = worker
					self.seed_started = request_id
					self.quick_pending = ''
					worker.start()
			return
		while True:
			try: validation_id, token, expires = self.validations.get_nowait()
			except Empty: break
			if validation_id != request_id or token != request.get('validation_token') or not request.get('validating') or _read(_REQUEST).get('id') != request_id: continue
			request.pop('validating', None)
			request.pop('validation_started', None)
			request.pop('validation_token', None)
			if expires and _request_current(request) and time.time() < expires and not _fresh_cache_conflicts(request):
				request.pop('retained', None)
				request['validated_until'] = expires
			else: request['validation_failed'] = True
			ku.set_property(_REQUEST, json.dumps(request, ensure_ascii=False, separators=(',', ':')))
			self.validating_id = ''
			self.refreshed_id = ''
			if not ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'):
				ku.execute_builtin('Container.Refresh')
			return
		if request.get('validating'):
			started = request.get('validation_started')
			if started is None and not ku.get_visibility('Player.HasMedia'):
				started = request['validation_started'] = time.time()
				if _read(_REQUEST).get('id') != request_id: return
				ku.set_property(_REQUEST, json.dumps(request, ensure_ascii=False, separators=(',', ':')))
			if isinstance(started, (int, float)) and not isinstance(started, bool) and time.time() - started >= _SLOW_SECONDS:
				request.pop('validating', None)
				request.pop('validation_started', None)
				request.pop('validation_token', None)
				request['validation_failed'] = True
				if _read(_REQUEST).get('id') == request_id: ku.set_property(_REQUEST, json.dumps(request, ensure_ascii=False, separators=(',', ':')))
				self.refreshed_id = ''
				if not ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'): ku.execute_builtin('Container.Refresh')
				return
			if ku.get_visibility('Player.HasMedia'): return
			if self.validating_id != request.get('validation_token') and (self.validation_worker is None or not self.validation_worker.is_alive()):
				self.validation_worker = Thread(target=self._validate_fixture, args=(request,), daemon=True)
				self.validating_id = request.get('validation_token')
				self.validation_worker.start()
			return
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
			order = _read(_ORDER)
			focused_label = ku.get_infolabel('Container.ListItem.Label') or ''
			first_choices = bool(result.get('choices')) and (order.get('id') != request_id or not order.get('refs')) and not is_slow() and 'select to retry' not in focused_label.casefold()
			if ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)') or (not first_choices and not ku.get_visibility('System.IdleTime(2)')): return
			self.refreshed_id = result_key
			if slow_refresh: self.slow_refreshed_id = request_id
			if result.get('choices') and self.timed_refresh_id != request_id:
				_timing('refresh', request.get('created'))
				self.timed_refresh_id = request_id
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
