"""Coordinate progressive sports feed matching across Kodi Python invocations."""
import json
import hashlib
import math
import time
import uuid
from datetime import datetime
from queue import Empty, SimpleQueue
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from modules import kodi_utils as ku
from modules.livetv_sports import record_timing


_REQUEST = 'LiveTV.Sports.Progress.Request'
_RESULT = 'LiveTV.Sports.Progress.Result'
_FALLBACK = 'LiveTV.Sports.Progress.Fallback'
_FALLBACK_EXPIRED = 'LiveTV.Sports.Progress.FallbackExpired'
_FALLBACK_DEADLINE = 'LiveTV.Sports.Progress.FallbackDeadline'
_CANCEL = 'LiveTV.Sports.Progress.Cancelled'
_HEARTBEAT = 'LiveTV.Sports.Progress.Heartbeat'
_CONSUME = 'LiveTV.Sports.Progress.Consume'
_ACK = 'LiveTV.Sports.Progress.Acknowledged'
_ACK_REVISION = 'LiveTV.Sports.Progress.AcknowledgedRevision'
_INCOMPLETE = 'LiveTV.Sports.Progress.Incomplete'
_WAIT_STATUS = 'LiveTV.Sports.Progress.WaitStatus'
_COUNTS_CACHE = 'LiveTV.Sports.LastCounts.'
_NEXT_START = 'LiveTV.Sports.NextStart'
_STARTS = 'LiveTV.Sports.Starts'
_HAS_LIVE = 'LiveTV.Sports.HasLive'
_FIXTURE_REQUEST = 'LiveTV.Sports.Fixtures.Request'
_FIXTURE_RESULT = 'LiveTV.Sports.Fixtures.Result'
_FIXTURE_ACK = 'LiveTV.Sports.Fixtures.Acknowledged'
_FIXTURE_CACHE = 'LiveTV.Sports.Fixtures.Cache.'
_REQUEST_LIMIT = 1024 * 1024
_RESULT_LIMIT = 4 * 1024 * 1024
_MAX_AGE = 15 * 60
_FALLBACK_SECONDS = 60
_PLAYBACK_RETENTION_SECONDS = 120


def _read(name):
	try:
		value = json.loads(ku.get_property(name) or '{}')
		return value if isinstance(value, dict) else {}
	except (ValueError, TypeError): return {}


def _valid(request, sport):
	try: return request.get('sport') == sport and isinstance(request.get('fixtures'), list) and 0 <= time.time() - request['created'] < _MAX_AGE
	except (KeyError, TypeError): return False


def _groups_from_json(items):
	groups = {}
	for key, feeds in items:
		if isinstance(key, list): key = (tuple(key[0]), key[1])
		groups[key] = feeds
	return groups


def _fresh_guide_groups(groups, now):
	for feeds in groups.values():
		if not isinstance(feeds, dict): return False
		for feed in feeds.values():
			if not isinstance(feed, dict): return False
			if not feed.get('_guide_match'): continue
			until = feed.get('_guide_until')
			try:
				if not isinstance(until, (int, float)) or isinstance(until, bool) or not math.isfinite(until) or until <= now: return False
			except OverflowError: return False
	return True


def _filter_guide_groups(groups, now):
	"""Keep current matches when a different guide entry has expired."""
	filtered = {}
	for key, feeds in groups.items():
		if not isinstance(feeds, dict): raise TypeError('Invalid feed group')
		filtered[key] = {}
		for ref, feed in feeds.items():
			if not isinstance(feed, dict): raise TypeError('Invalid feed')
			if _fresh_guide_groups({key: {ref: feed}}, now): filtered[key][ref] = feed
	return filtered


def _group_hash(value):
	return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()


def save_counts(sport, fixtures, groups, now=None):
	"""Retain display-only counts, never channel identities or playback tokens."""
	from menus import livetv
	now = time.time() if now is None else now
	try:
		if not _fresh_guide_groups(groups, now): return
		deadlines = [feed['_guide_until'] for feeds in groups.values() for feed in feeds.values() if feed.get('_guide_match')]
		cache = {'created': now, 'expires': min([now + 300] + deadlines), 'accounts': livetv._feed_accounts_fingerprint(), 'fixtures': _group_hash(fixtures),
			'counts': {_group_hash(key): len(feeds) for key, feeds in groups.items()}}
		ku.set_property(_COUNTS_CACHE + sport, json.dumps(cache, separators=(',', ':')))
	except (TypeError, ValueError, KeyError, OverflowError): return


def load_counts(sport, fixtures, now=None):
	"""Return still-current counts only for the identical account and fixtures."""
	from menus import livetv
	now = time.time() if now is None else now
	cache = _read(_COUNTS_CACHE + sport)
	try:
		created, expires = cache['created'], cache['expires']
		if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) for value in (created, expires)): return {}
		if not created <= now < min(expires, created + 300) or cache['accounts'] != livetv._feed_accounts_fingerprint() or cache['fixtures'] != _group_hash(fixtures): return {}
		return cache['counts'] if isinstance(cache.get('counts'), dict) else {}
	except (KeyError, TypeError, ValueError, OverflowError): return {}


def cached_count(snapshot, group_key):
	try:
		value = snapshot.get(_group_hash(group_key), 0)
		return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 100000 else 0
	except (AttributeError, TypeError, ValueError): return 0


def incomplete():
	return ku.get_property(_INCOMPLETE) == 'true'


def invalidate(request_id=None):
	request_id = request_id or _read(_REQUEST).get('id', '')
	if not request_id: return
	ku.set_property(_CANCEL, request_id)
	ku.clear_property(_RESULT + '.' + request_id)
	ku.clear_property(_FALLBACK + '.' + request_id)
	ku.clear_property(_FALLBACK_DEADLINE + '.' + request_id)
	ku.clear_property(_INCOMPLETE)


def _set_soccer_status(status, unavailable, complete=True):
	message = ''
	if status:
		created, stale = status
		when = datetime.fromtimestamp(created).astimezone().strftime('%a %H:%M')
		message = (('Saved channels from %s; refresh failed' if complete else 'Saved channels from %s; refreshing…') if stale else 'Updated %s') % when
	if unavailable: message = (message + ' · ' if message else '') + 'Some providers unavailable'
	ku.set_property('LiveTV.Soccer.Status', message)


def consume_state(sport, force_refresh=False):
	"""Return the original schedule inputs during the result refresh only."""
	if force_refresh: return None
	request = _read(_REQUEST)
	request_id = request.get('id', '')
	if not request_id or not _valid(request, sport) or ku.get_property(_CANCEL) == request_id or ku.get_property(_CONSUME) != request_id: return None
	if _read(_RESULT + '.' + request_id).get('id') != request_id: return None
	from menus import livetv
	if request.get('accounts') != livetv._feed_accounts_fingerprint(): return None
	return request['fixtures'], request.get('auxiliary')


def consume_fixtures(sport, force_refresh=False):
	state = consume_state(sport, force_refresh)
	return state[0] if state is not None else None


def _alive():
	try: return 0 <= time.time() - float(ku.get_property(_HEARTBEAT)) < 30
	except (TypeError, ValueError): return False


def url_refresh_requested(sport):
	request = _read(_FIXTURE_REQUEST)
	return request.get('sport') != sport or not request.get('url_refresh')


def _cached_fixtures(sport, accounts):
	if sport not in ('soccer', 'hockey', 'football', 'basketball', 'tennis', 'f1'): return None
	cache = _read(_FIXTURE_CACHE + sport)
	try:
		captured, expires = cache['captured'], cache['expires']
		if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) for value in (captured, expires)): return None
		if cache.get('accounts') != accounts or not captured <= time.time() < expires: return None
		if not isinstance(cache.get('fixtures'), list): return None
		if sport == 'soccer' and (not isinstance(cache.get('auxiliary'), dict) or not isinstance(cache['auxiliary'].get('sport_fixtures'), list) or not isinstance(cache['auxiliary'].get('status_text'), str) or cache['auxiliary']['status_text'].startswith('Live fixtures unavailable')): return None
		return cache['fixtures'], cache.get('auxiliary')
	except (KeyError, TypeError, OverflowError): return None


def _fixtures_unavailable(sport, result):
	if result.get('unavailable') or not isinstance(result.get('fixtures'), list): return True
	if sport != 'soccer': return False
	auxiliary = result.get('auxiliary')
	return not isinstance(auxiliary, dict) or not isinstance(auxiliary.get('sport_fixtures'), list) or not isinstance(auxiliary.get('status_text'), str) or auxiliary['status_text'].startswith('Live fixtures unavailable')


def next_half_hour(timestamp):
	local = datetime.fromtimestamp(timestamp).astimezone()
	seconds = (local.minute % 30) * 60 + local.second + local.microsecond / 1000000
	return timestamp + 1800 - seconds


def _focus_identity(position):
	base = 'Container(523).ListItemAbsolute(%d).Property(LiveTV.%%s)' % position
	event = ku.get_infolabel(base % 'EventIdentity')
	if event: return ('event', event)
	return ('legacy', *(ku.get_infolabel(base % name) for name in ('Match', 'League', 'Kickoff')))


def _fixture_focus():
	try:
		if ku.get_visibility('Control.HasFocus(523)'):
			position = int(ku.get_infolabel('Container(523).CurrentItem')) - 1
			if position < 0: return None
			identity = _focus_identity(position)
			if identity[1]: return {'position': position, 'identity': identity, 'started': time.monotonic()}
	except (AttributeError, RuntimeError, TypeError, ValueError): pass
	return None


def request_background(sport, reload):
	"""Refresh fixtures without replacing the currently displayed schedule."""
	if not _alive() or not _current(sport): return False
	request = _read(_FIXTURE_REQUEST)
	if request.get('sport') == sport and request.get('id') and (_read(_FIXTURE_RESULT).get('id') != request['id'] or ku.get_property(_FIXTURE_ACK) != request['id']): return False
	feed_request = _read(_REQUEST)
	if feed_request.get('sport') == sport and feed_request.get('id') and ku.get_property(_CANCEL) != feed_request['id'] and ku.get_property(_ACK) != feed_request['id']: return False
	from menus import livetv
	updated = {'id': uuid.uuid4().hex, 'sport': sport, 'accounts': livetv._feed_accounts_fingerprint(), 'created': time.time(), 'reload': reload, 'url_refresh': request.get('url_refresh', False), 'background': True}
	focus = _fixture_focus()
	if focus: updated['focus'] = focus
	ku.clear_property(_FIXTURE_RESULT)
	ku.set_property(_FIXTURE_REQUEST, json.dumps(updated, separators=(',', ':')))
	return True


def accepted_result(sport, now=None):
	"""Return the currently displayed matching snapshot when it is still valid."""
	now = time.time() if now is None else now
	from menus import livetv
	request = _read(_REQUEST)
	request_id = request.get('id', '')
	if not request_id or not _current(sport) or not _valid(request, sport) or request.get('accounts') != livetv._feed_accounts_fingerprint() or ku.get_property(_CANCEL) == request_id: return None
	result = _read(_RESULT + '.' + request_id)
	fallback = _read(_FALLBACK + '.' + request_id)
	if not isinstance(fallback.get('fallback_expires'), (int, float)) or isinstance(fallback.get('fallback_expires'), bool) or not now < fallback['fallback_expires']: fallback = {}
	use_fallback = fallback and (result.get('id') != request_id or result.get('complete', True) is not True or result.get('unavailable'))
	try:
		if use_fallback:
			groups = _filter_guide_groups(_groups_from_json(fallback['groups']), now)
			published = fallback['published']
			if result.get('id') == request_id:
				for key, feeds in _filter_guide_groups(_groups_from_json(result['groups']), now).items(): groups.setdefault(key, {}).update(feeds)
				published = min(published, result['published'])
		else:
			if result.get('id') != request_id: return None
			groups = _filter_guide_groups(_groups_from_json(result['groups']), now)
			published = result['published']
		if not isinstance(published, (int, float)) or isinstance(published, bool) or not math.isfinite(published) or published > now: return None
		status = result.get('status')
		return {'request': request, 'groups': groups, 'published': published, 'unavailable': bool(result.get('unavailable')), 'saved': bool(use_fallback or isinstance(status, (list, tuple)) and len(status) == 2 and status[1]), 'revision': result.get('revision', 0)}
	except (KeyError, TypeError, ValueError): return None


def guide_fixture_stale(sport):
	"""Whether the current fixture snapshot needs refreshing before rematching."""
	from menus import livetv
	request = _read(_REQUEST)
	if not _current(sport) or not _valid(request, sport) or request.get('accounts') != livetv._feed_accounts_fingerprint(): return False
	cache = _read(_FIXTURE_CACHE + sport)
	if cache.get('accounts') != request['accounts'] or cache.get('fixtures') != request['fixtures']: return False
	expires = cache.get('source_expires')
	return not isinstance(expires, (int, float)) or isinstance(expires, bool) or not math.isfinite(expires) or expires <= time.time()


def request_guide_rematch(sport):
	"""Match the accepted fixture snapshot against newly saved guide data."""
	if not _alive() or not _current(sport) or pending(): return False
	request = _read(_REQUEST)
	from menus import livetv
	if not _valid(request, sport) or request.get('accounts') != livetv._feed_accounts_fingerprint(): return False
	cache = _read(_FIXTURE_CACHE + sport)
	try:
		expires = cache['source_expires']
		if not isinstance(expires, (int, float)) or isinstance(expires, bool) or not math.isfinite(expires) or expires <= time.time() or cache.get('accounts') != request.get('accounts') or cache.get('fixtures') != request['fixtures']: return False
	except (KeyError, TypeError, ValueError, OverflowError): return False
	result = _read(_RESULT + '.' + request['id'])
	if result.get('id') != request['id'] or result.get('complete', True) is not True or ku.get_property(_ACK) != request['id'] or (result.get('unavailable') and _read(_FALLBACK + '.' + request['id'])): return False
	_, groups = prepare(sport, '', request['fixtures'], force_refresh=True, auxiliary=request.get('auxiliary'))
	if groups is not None: return False
	new_id = _read(_REQUEST).get('id', '')
	if not new_id or new_id == request['id']: return False
	result['fallback_expires'] = min(expires, time.time() + _FALLBACK_SECONDS)
	ku.set_property(_FALLBACK + '.' + new_id, json.dumps(result, ensure_ascii=False, separators=(',', ':')))
	ku.set_property(_FALLBACK_DEADLINE + '.' + new_id, str(result['fallback_expires']))
	return True


def load_fixtures(sport, force_refresh=False, reload='', url_refresh=False):
	"""Request a fixture snapshot without waiting for network access."""
	if not _alive(): return 'unavailable', None, None
	from menus import livetv
	accounts = livetv._feed_accounts_fingerprint()
	request = _read(_FIXTURE_REQUEST)
	try: valid = request.get('sport') == sport and request.get('accounts') == accounts and 0 <= time.time() - request['created'] < _MAX_AGE
	except (KeyError, TypeError): valid = False
	if force_refresh or not valid:
		invalidate()
		ku.clear_property(_HAS_LIVE)
		ku.clear_property(_NEXT_START)
		ku.clear_property(_STARTS)
		request = {'id': uuid.uuid4().hex, 'sport': sport, 'accounts': accounts, 'created': time.time(), 'reload': reload, 'url_refresh': url_refresh}
		focus = _fixture_focus()
		if focus: request['focus'] = focus
		ku.clear_property(_FIXTURE_RESULT)
		ku.set_property(_FIXTURE_REQUEST, json.dumps(request, separators=(',', ':')))
		cached = None if force_refresh else _cached_fixtures(sport, accounts)
		if cached is not None: return 'cached', cached[0], cached[1]
		return 'pending', None, None
	result = _read(_FIXTURE_RESULT)
	if result.get('id') != request.get('id'):
		cached = None if request.get('reload') in ('refresh', 'schedule') and not request.get('background') else _cached_fixtures(sport, accounts)
		return ('cached', cached[0], cached[1]) if cached is not None else ('pending', None, None)
	if _fixtures_unavailable(sport, result):
		if request.pop('reload', ''): ku.set_property(_FIXTURE_REQUEST, json.dumps(request, separators=(',', ':')))
		ku.set_property(_FIXTURE_ACK, request['id'])
		cached = _cached_fixtures(sport, accounts)
		return ('cached_unavailable', cached[0], cached[1]) if cached is not None else ('unavailable', None, None)
	return 'ready', result['fixtures'], result.get('auxiliary')


def fixture_reload():
	"""Consume the refresh intent once the fixture snapshot is ready."""
	request = _read(_FIXTURE_REQUEST)
	reload = request.pop('reload', '')
	if reload: ku.set_property(_FIXTURE_REQUEST, json.dumps(request, separators=(',', ':')))
	if request.get('id'): ku.set_property(_FIXTURE_ACK, request['id'])
	return reload


def prepare(sport, reload, fixtures, force_refresh=False, auxiliary=None):
	"""Return the original fixture snapshot and either pending or matched feeds."""
	from modules.livetv_fixtures import LIVE_STATUSES
	ku.set_property(_HAS_LIVE, sport if any(isinstance(fixture, dict) and fixture.get('status') in LIVE_STATUSES for fixture in fixtures or []) else '')
	starts = []
	now = time.time()
	for fixture in fixtures or []:
		if not isinstance(fixture, dict) or fixture.get('status') != 'Scheduled': continue
		try:
			start = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00'))
			if start.utcoffset() is not None and start.timestamp() >= now - 15 * 60: starts.append(start.timestamp())
		except (AttributeError, KeyError, TypeError, ValueError, OverflowError, OSError): pass
	ku.set_property(_NEXT_START, str(min(starts)) if starts else '')
	ku.set_property(_STARTS, json.dumps(sorted(set(starts)), separators=(',', ':')))
	if not fixtures:
		invalidate()
		ku.set_property('LiveTV.Sports.CatalogUnavailable', '')
		if sport == 'soccer': ku.set_property('LiveTV.Soccer.Status', '')
		return fixtures, {}
	from menus import livetv
	def unavailable():
		invalidate()
		ku.set_property('LiveTV.Sports.CatalogUnavailable', 'true')
		if sport == 'soccer': _set_soccer_status(None, True)
		return fixtures, {}
	if not _alive(): return unavailable()
	accounts = livetv._feed_accounts_fingerprint()
	request = _read(_REQUEST)
	result = _read(_RESULT + '.' + request.get('id', ''))
	consume = bool(not force_refresh and request.get('id') and ku.get_property(_CONSUME) == request['id'] and result.get('id') == request['id'])
	new_refresh = force_refresh or (reload in ('refresh', 'schedule') and not consume and (request.get('reload') != reload or result.get('id') == request.get('id')))
	fresh_fixtures = (fixtures == request.get('fixtures') and auxiliary == request.get('auxiliary')) or consume
	if _valid(request, sport) and ku.get_property(_CANCEL) != request.get('id') and request.get('accounts') == accounts and not new_refresh and fresh_fixtures:
		fallback = _read(_FALLBACK + '.' + request['id'])
		if not isinstance(fallback.get('fallback_expires'), (int, float)) or isinstance(fallback.get('fallback_expires'), bool) or not time.time() < fallback['fallback_expires']: fallback = {}
		if fallback and (result.get('id') != request['id'] or result.get('complete', True) is not True or result.get('unavailable')):
			try:
				groups = _filter_guide_groups(_groups_from_json(fallback['groups']), time.time())
				if result.get('id') == request['id']:
					partial = _filter_guide_groups(_groups_from_json(result['groups']), time.time())
					for key, feeds in partial.items(): groups.setdefault(key, {}).update(feeds)
					ku.set_property(_ACK_REVISION, request['id'] + ':' + str(result.get('revision', 0)))
					if result.get('complete', True) is True:
						ku.set_property(_ACK, request['id'])
						ku.set_property(_INCOMPLETE, '')
						ku.set_property('LiveTV.Sports.CatalogUnavailable', 'true')
						if sport == 'soccer': _set_soccer_status(result.get('status'), True)
					ku.clear_property(_CONSUME)
				return request['fixtures'], groups
			except (KeyError, TypeError, ValueError): pass
		if result.get('id') == request.get('id'):
			try:
				if result.get('sync'): return unavailable()
				raw_groups = _groups_from_json(result['groups'])
				groups = _filter_guide_groups(raw_groups, time.time())
				if groups is not None:
					unavailable_flag = result.get('unavailable', '') or ('true' if groups != raw_groups else '')
					ku.set_property('LiveTV.Sports.CatalogUnavailable', unavailable_flag)
					complete = result.get('complete', True) is True
					if sport == 'soccer': _set_soccer_status(result.get('status'), unavailable_flag, complete)
					ku.set_property(_INCOMPLETE, '' if complete else 'true')
					ku.set_property(_ACK_REVISION, request['id'] + ':' + str(result.get('revision', 0)))
					if complete:
						ku.clear_property(_FALLBACK + '.' + request['id'])
						ku.clear_property(_FALLBACK_DEADLINE + '.' + request['id'])
						ku.set_property(_ACK, request['id'])
						published = result.get('published')
						if not unavailable_flag and isinstance(published, (int, float)) and not isinstance(published, bool) and math.isfinite(published) and published <= time.time(): save_counts(sport, request['fixtures'], groups, published)
					if isinstance(result.get('published'), (int, float)) and not isinstance(result.get('published'), bool): record_timing('ui_handoff', max(0, time.time() - result['published']), sport, 'ok' if complete else 'partial')
					ku.clear_property(_CONSUME)
					return request['fixtures'], groups
			except (KeyError, TypeError, ValueError): pass
		if ku.get_property(_FALLBACK_EXPIRED) in (request['id'], request['id'] + ':refreshing'):
			ku.set_property(_ACK_REVISION, request['id'] + ':0')
			ku.clear_property(_FALLBACK_EXPIRED)
		return request['fixtures'], None
	previous_id = request.get('id', '')
	request = {'id': uuid.uuid4().hex, 'sport': sport, 'reload': reload, 'fixtures': fixtures, 'auxiliary': auxiliary, 'created': time.time(), 'accounts': accounts}
	try: data = json.dumps(request, ensure_ascii=False, separators=(',', ':'))
	except (TypeError, ValueError): return unavailable()
	if len(data.encode('utf-8')) > _REQUEST_LIMIT: return unavailable()
	if previous_id: invalidate(previous_id)
	ku.set_property(_REQUEST, data)
	ku.set_property(_INCOMPLETE, 'true')
	ku.set_property(_WAIT_STATUS, 'Finding feeds…')
	ku.set_property('LiveTV.Sports.CatalogUnavailable', '')
	if sport == 'soccer': ku.set_property('LiveTV.Soccer.Status', '')
	return fixtures, None


def pending():
	fixture_request = _read(_FIXTURE_REQUEST)
	fixture_id = fixture_request.get('id', '')
	if fixture_id and _current(fixture_request.get('sport')) and _read(_FIXTURE_RESULT).get('id') != fixture_id: return True
	request = _read(_REQUEST)
	request_id = request.get('id', '')
	if not request_id or ku.get_property(_CANCEL) == request_id: return False
	if ku.get_property(_ACK) == request_id: return False
	return True


def has_live(sport):
	return ku.get_property(_HAS_LIVE) == sport


def _current(sport):
	if not ku.get_visibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.soccer)'): return False
	if ku.get_property('LiveTV.Sport') != sport: return False
	path = ku.get_infolabel('Container.FolderPath')
	query = parse_qs(urlsplit(path).query)
	return query.get('mode') == ['livetv.soccer'] and query.get('sport', ['soccer']) == [sport]


def _matching_picker(request):
	"""Recognize a picker for a fixture in the active sports search."""
	if not ku.get_visibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.match_feeds)'): return False
	try:
		from modules import livetv_feed_selection
		query = parse_qs(urlsplit(ku.get_infolabel('Container.FolderPath')).query, keep_blank_values=True)
		sport = request.get('sport')
		if query.get('mode') != ['livetv.match_feeds'] or query.get('sport', ['']) != [sport]: return False
		key = query.get('session', [''])[0] if sport == 'f1' else ((query.get('home', [''])[0], query.get('away', [''])[0]), query.get('scope', [''])[0])
		matches = [fixture for fixture in request['fixtures'] if isinstance(fixture, dict) and livetv_feed_selection._fixture_key(sport, fixture) == key]
		return len(matches) == 1
	except (AttributeError, KeyError, TypeError, ValueError, IndexError): return False


class SportsProgress:
	def __init__(self):
		self.workers = {}
		self.seen_id = ''
		self.playback_id = ''
		self.playback_until = 0
		self.refreshed_id = ''
		self.refreshed_at = 0
		self.results = SimpleQueue()
		self.focus = None
		self.fixture_workers = {}
		self.fixture_results = SimpleQueue()
		self.fixture_refreshed = ''
		self.fixture_seen = ''
		self.fixture_restored = ''
		self.fixture_matching = {}

	def _fixture_work(self, request):
		started = time.monotonic()
		outcome = 'unavailable'
		try:
			from menus import livetv
			if livetv._feed_accounts_fingerprint() != request['accounts']: raise ValueError('Account changed')
			sport = request['sport']
			auxiliary = None
			if sport == 'soccer':
				(fixtures, _, other, status), expires = livetv._live_soccer_fixtures(include_upcoming=True, with_expiry=True)
				auxiliary = {'sport_fixtures': other, 'status_text': status}
			elif sport == 'f1': fixtures, expires = livetv._f1.get_schedule(with_expiry=True)
			else:
				profile = ku.translate_path(getattr(ku, 'profile_path', ''))
				fixtures, expires = livetv._tennis_fixtures_service.get_schedule(cache_dir=profile or None, with_expiry=True) if sport == 'tennis' else livetv._american_fixtures.get_schedule(sport, cache_dir=profile or None, with_expiry=True)
			encoded = json.dumps({'id': request['id'], 'fixtures': fixtures, 'auxiliary': auxiliary, 'captured': time.time(), 'expires': expires}, ensure_ascii=False, separators=(',', ':'))
			if len(encoded.encode('utf-8')) > _RESULT_LIMIT: raise ValueError('Result too large')
			outcome = 'ok'
		except Exception: encoded = json.dumps({'id': request['id'], 'unavailable': True})
		record_timing('fixture_fetch', time.monotonic() - started, request.get('sport', ''), outcome)
		self.fixture_results.put((request['id'], encoded))

	def _fixture_tick(self):
		request = _read(_FIXTURE_REQUEST)
		request_id = request.get('id', '')
		visible = bool(request_id and _current(request.get('sport')))
		while True:
			try: result_id, encoded = self.fixture_results.get_nowait()
			except Empty: break
			if visible and result_id == request_id:
				result = json.loads(encoded)
				if not _fixtures_unavailable(request.get('sport'), result) and request.get('sport') in ('soccer', 'hockey', 'football', 'basketball', 'tennis', 'f1') and _read(_FIXTURE_REQUEST).get('id') == request_id:
					captured = result.get('captured')
					if isinstance(captured, (int, float)) and not isinstance(captured, bool) and math.isfinite(captured):
						cache = {'captured': captured, 'expires': next_half_hour(captured), 'source_expires': result.get('expires'), 'accounts': request.get('accounts'), 'fixtures': result['fixtures'], 'auxiliary': result.get('auxiliary')}
						data = json.dumps(cache, ensure_ascii=False, separators=(',', ':'))
						if cache['expires'] > time.time() and len(data.encode('utf-8')) <= _RESULT_LIMIT: ku.set_property(_FIXTURE_CACHE + request['sport'], data)
				ku.set_property(_FIXTURE_RESULT, encoded)
		if not visible:
			if request_id and self.fixture_seen == request_id and not ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)') and _read(_FIXTURE_REQUEST).get('id') == request_id:
				ku.clear_property(_FIXTURE_REQUEST)
				ku.clear_property(_FIXTURE_RESULT)
				self.fixture_seen = ''
			return
		self.fixture_seen = request_id
		if _read(_FIXTURE_RESULT).get('id') == request_id:
			result = _read(_FIXTURE_RESULT)
			# Keep the completed fixture snapshot until playback ends; preparing it
			# can replace the current feed search and start new provider requests.
			if ku.get_visibility('Player.HasMedia'): return
			if (self.fixture_refreshed != request_id and not request.get('background') and not request.get('reload') and request_id not in self.fixture_matching
					and not _fixtures_unavailable(request.get('sport'), result) and not ku.get_visibility('Player.HasMedia')):
				from menus import livetv
				if request.get('accounts') == livetv._feed_accounts_fingerprint():
					_, groups = prepare(request['sport'], '', result['fixtures'], auxiliary=result.get('auxiliary'))
					self.fixture_matching = {request_id: _read(_REQUEST).get('id', '') if groups is None else ''}
			if self.fixture_refreshed != request_id and request.get('background') and not _fixtures_unavailable(request.get('sport'), result):
				if request_id not in self.fixture_matching:
					_, groups = prepare(request['sport'], request.get('reload', 'schedule'), result['fixtures'], auxiliary=result.get('auxiliary'))
					self.fixture_matching = {request_id: _read(_REQUEST).get('id', '') if groups is None else ''}
				feed_id = self.fixture_matching[request_id]
				if feed_id:
					if _read(_RESULT + '.' + feed_id).get('id') != feed_id: return
					ku.set_property(_CONSUME, feed_id)
			if self.fixture_refreshed == request_id: return
			if ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'): return
			if not ku.get_visibility('System.IdleTime(2)') and (not ku.get_visibility('Control.HasFocus(523)') or _fixture_focus()): return
			self.fixture_refreshed = request_id
			self._capture_focus(request, _FIXTURE_ACK)
			ku.execute_builtin('Container.Refresh')
			return
		if ku.get_visibility('Player.HasMedia'): return
		self.fixture_workers = {key: worker for key, worker in self.fixture_workers.items() if worker.is_alive()}
		if request_id in self.fixture_workers: return
		if len(self.fixture_workers) >= 3:
			self.fixture_results.put((request_id, json.dumps({'id': request_id, 'unavailable': True})))
			return
		worker = Thread(target=self._fixture_work, args=(request,), daemon=True)
		self.fixture_workers[request_id] = worker
		worker.start()

	@staticmethod
	def _item_identity(position):
		return _focus_identity(position)

	def _capture_focus(self, request, ack=_ACK, expected=None):
		self.focus = None
		try:
			if not ku.get_visibility('Control.HasFocus(523)'): return
			position = int(ku.get_infolabel('Container(523).CurrentItem')) - 1
			if position < 0: return
			identity = self._item_identity(position)
			if not identity[1]: return
			now = time.monotonic()
			self.focus = {'id': request['id'], 'sport': request['sport'], 'position': position, 'identity': identity, 'started': now, 'deadline': now + 90, 'ack': ack, 'expected': expected or request['id']}
		except (AttributeError, RuntimeError, TypeError, ValueError): return

	def _restore_focus(self, request, visible):
		focus = self.focus
		if not focus: return
		now = time.monotonic()
		idle_required = max(2, int(now - focus['started']) + 1)
		current = _read(_FIXTURE_REQUEST) if focus['ack'] == _FIXTURE_ACK else request
		if focus['id'] != current.get('id') or now >= focus['deadline'] or not ku.get_visibility('System.IdleTime(%d)' % idle_required):
			self.focus = None
			return
		if ku.get_visibility('Container.IsUpdating | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'): return
		if ku.get_visibility('Player.HasMedia | System.HasActiveModalDialog'):
			self.focus = None
			return
		if not _current(focus['sport']):
			self.focus = None
			return
		acknowledged = ku.get_property(focus['ack'])
		if acknowledged != focus['expected']:
			if focus['ack'] != _ACK_REVISION: return
			try:
				ack_id, ack_revision = acknowledged.rsplit(':', 1)
				_, expected_revision = focus['expected'].rsplit(':', 1)
				if ack_id != focus['id'] or int(ack_revision) < int(expected_revision): return
			except (AttributeError, TypeError, ValueError): return
		try:
			if not ku.get_visibility('Control.HasFocus(523)'):
				self.focus = None
				return
			position = int(ku.get_infolabel('Container(523).CurrentItem')) - 1
			count = min(10000, max(0, int(ku.get_infolabel('Container(523).NumItems'))))
			matches = [index for index in range(count) if self._item_identity(index) == focus['identity']]
			if len(matches) == 1 and position != matches[0]: ku.execute_builtin('Control.SetFocus(523,%d,absolute)' % matches[0])
		except (AttributeError, RuntimeError, TypeError, ValueError): pass
		self.focus = None

	def _work(self, request):
		started = time.monotonic()
		outcome = 'unavailable'
		revision = 0
		last_cleaned = []
		try:
			from menus import livetv
			statuses, unavailable = [], []
			account = livetv._sports_primary()
			if not account or livetv._feed_accounts_fingerprint() != request['accounts']: raise ValueError('Account changed')
			secrets = [str(a.get(key) or '') for _, a in livetv._sports_accounts(livetv._load()) for key in ('username', 'password')]
			def publish(groups, unavailable_value, status_values, complete):
				nonlocal revision, last_cleaned
				cleaned = []
				for key, feeds in groups.items():
					safe_feeds = {}
					for ref, feed in feeds.items():
						stream_id, name = feed.get('stream_id'), str(feed.get('name') or '')
						if any(livetv._catalog_redact(value, secrets) != value for value in (str(ref), str(stream_id), name)): raise ValueError('Unsafe feed')
						item = {field: feed[field] for field in ('stream_id', '_provider', '_primary', '_guide_match', '_guide_until') if field in feed}
						item.update({'name': name, 'stream_icon': livetv._catalog_icon(feed.get('stream_icon'), secrets)})
						safe_feeds[ref] = item
					cleaned.append((key, safe_feeds))
				revision += 1
				result = {'id': request['id'], 'groups': cleaned, 'unavailable': unavailable_value, 'status': status_values[-1] if status_values else None,
					'revision': revision, 'complete': complete, 'published': time.time()}
				encoded = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
				if len(encoded.encode('utf-8')) > _RESULT_LIMIT: raise ValueError('Result too large')
				last_cleaned = cleaned
				self.results.put((request['id'], encoded))
			groups, _ = livetv._sports_catalog_groups(account, request['sport'], request['reload'], on_status=lambda created, stale: statuses.append((created, stale)), fixtures=request['fixtures'],
				status_sink=unavailable.append, notify_stale=False, on_partial=lambda groups, unavailable_value, status_values: publish(groups, unavailable_value, status_values, False))
			publish(groups, unavailable[-1] if unavailable else '', statuses, True)
			outcome = 'ok' if not unavailable or not unavailable[-1] else 'unavailable'
		except Exception:
			revision += 1
			self.results.put((request['id'], json.dumps({'id': request['id'], 'groups': last_cleaned, 'unavailable': 'true', 'status': None, 'revision': revision, 'complete': True, 'published': time.time()})))
		record_timing('lookup', time.monotonic() - started, request.get('sport', ''), outcome)

	def tick(self):
		ku.set_property(_HEARTBEAT, str(time.time()))
		self._fixture_tick()
		request = _read(_REQUEST)
		request_id = request.get('id', '')
		valid = bool(request_id and ku.get_property(_CANCEL) != request_id and _valid(request, request.get('sport')))
		visible = bool(valid and _current(request.get('sport')))
		playing = ku.get_visibility('Player.HasMedia')
		if request_id != self.playback_id:
			self.playback_id = ''
			self.playback_until = 0
		collecting = visible
		retained = self.playback_id == request_id and time.monotonic() < self.playback_until
		if valid and self.seen_id == request_id and (playing or (not visible and (_matching_picker(request) or retained))):
			from menus import livetv
			matching_accounts = request.get('accounts') == livetv._feed_accounts_fingerprint()
			if playing and matching_accounts:
				self.playback_id = request_id
				self.playback_until = time.monotonic() + _PLAYBACK_RETENTION_SECONDS
			if not visible: collecting = matching_accounts
		self._restore_focus(request, visible)
		if not collecting and self.seen_id and request_id == self.seen_id:
			if ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'): return
			invalidate(request_id)
			self.seen_id = ''
			return
		if visible:
			self.seen_id = request_id
		else:
			ku.clear_property(_WAIT_STATUS)
		while True:
			try: result_id, encoded = self.results.get_nowait()
			except Empty: break
			if collecting and request_id == result_id and ku.get_property(_CANCEL) != result_id:
				ku.set_property(_RESULT + '.' + result_id, encoded)
				result = json.loads(encoded)
				status = result.get('status')
				if visible and not playing and status and status[1] and result.get('complete', True) is True: ku.dialog.notification('Live TV', 'Showing saved channels; refresh failed.')
		if not visible: return
		deadline = ku.get_property(_FALLBACK_DEADLINE + '.' + request_id)
		if deadline:
			try: expired = time.time() >= float(deadline)
			except (TypeError, ValueError): expired = True
			if expired:
				ku.clear_property(_FALLBACK + '.' + request_id)
				ku.clear_property(_FALLBACK_DEADLINE + '.' + request_id)
				ku.set_property(_FALLBACK_EXPIRED, request_id)
				if ku.get_property(_ACK) == request_id: ku.clear_property(_ACK)
				ku.clear_property(_ACK_REVISION)
		if ku.get_property(_ACK) == request_id:
			ku.clear_property(_WAIT_STATUS)
			return
		result = _read(_RESULT + '.' + request_id)
		blocked = ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)')
		try: long_wait = time.time() - request['created'] >= 30
		except (KeyError, TypeError): long_wait = False
		waiting = result.get('id') == request_id and blocked
		ku.set_property(_WAIT_STATUS, 'Waiting for Kodi…' if waiting else 'Still searching…' if long_wait else 'Finding feeds…')
		if ku.get_property(_FALLBACK_EXPIRED) == request_id and result.get('id') != request_id:
			if blocked: return
			if not ku.get_visibility('System.IdleTime(2)') and (not ku.get_visibility('Control.HasFocus(523)') or _fixture_focus()): return
			self._capture_focus(request, _ACK_REVISION, request_id + ':0')
			ku.set_property(_FALLBACK_EXPIRED, request_id + ':refreshing')
			ku.execute_builtin('Container.Refresh')
			return
		if result.get('id') == request_id:
			expiry_refresh = ku.get_property(_FALLBACK_EXPIRED) == request_id
			revision_key = request_id + ':' + str(result.get('revision', 0))
			if ku.get_property(_ACK_REVISION) == revision_key: return
			fixture_request = _read(_FIXTURE_REQUEST)
			fixture_id = fixture_request.get('id', '')
			if fixture_id and _current(fixture_request.get('sport')) and (_read(_FIXTURE_RESULT).get('id') != fixture_id or ku.get_property(_FIXTURE_ACK) != fixture_id or fixture_request.get('reload') in ('refresh', 'schedule')): return
			if not expiry_refresh and self.refreshed_id == revision_key and time.monotonic() - self.refreshed_at < 10: return
			if blocked: return
			if not ku.get_visibility('System.IdleTime(2)') and (not ku.get_visibility('Control.HasFocus(523)') or _fixture_focus()): return
			if expiry_refresh: ku.clear_property(_FALLBACK_EXPIRED)
			self.refreshed_id = revision_key
			self.refreshed_at = time.monotonic()
			self._capture_focus(request, _ACK_REVISION, revision_key)
			ku.set_property(_CONSUME, request_id)
			ku.execute_builtin('Container.Refresh')
			return
		self.workers = {key: worker for key, worker in self.workers.items() if worker.is_alive()}
		if request_id in self.workers: return
		if len(self.workers) >= 3:
			self.results.put((request_id, json.dumps({'id': request_id, 'groups': [], 'unavailable': 'true', 'status': None})))
			return
		worker = Thread(target=self._work, args=(request,), daemon=True)
		self.workers[request_id] = worker
		worker.start()

	def close(self):
		ku.clear_property(_HEARTBEAT)
		ku.clear_property(_FIXTURE_REQUEST)
		ku.clear_property(_FIXTURE_RESULT)
		ku.clear_property(_WAIT_STATUS)
		ku.clear_property(_INCOMPLETE)
		invalidate()
