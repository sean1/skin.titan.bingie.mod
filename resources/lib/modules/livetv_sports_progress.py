"""Load and refresh sports schedules across Kodi Python invocations."""
import json
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
_HEARTBEAT = 'LiveTV.Sports.Progress.Heartbeat'
_NEXT_START = 'LiveTV.Sports.NextStart'
_STARTS = 'LiveTV.Sports.Starts'
_HAS_LIVE = 'LiveTV.Sports.HasLive'
_FIXTURE_REQUEST = 'LiveTV.Sports.Fixtures.Request'
_FIXTURE_RESULT = 'LiveTV.Sports.Fixtures.Result'
_FIXTURE_ACK = 'LiveTV.Sports.Fixtures.Acknowledged'
_FIXTURE_CACHE = 'LiveTV.Sports.Fixtures.Cache.'
_RESULT_LIMIT = 4 * 1024 * 1024
_MAX_AGE = 15 * 60


def _read(name):
	try:
		value = json.loads(ku.get_property(name) or '{}')
		return value if isinstance(value, dict) else {}
	except (ValueError, TypeError): return {}


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


def _fixture_revision(result):
	request_id = result['id']
	return request_id + ':primary' if result.get('complete') is False else request_id


def _current_primary_fixtures(result):
	expires = result.get('expires')
	return isinstance(result.get('fixtures'), list) and isinstance(expires, (int, float)) and not isinstance(expires, bool) and math.isfinite(expires) and time.time() < expires


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
	from menus import livetv
	updated = {'id': uuid.uuid4().hex, 'sport': sport, 'accounts': livetv._feed_accounts_fingerprint(), 'created': time.time(), 'reload': reload, 'url_refresh': request.get('url_refresh', False), 'background': True}
	ku.clear_property(_FIXTURE_RESULT)
	ku.set_property(_FIXTURE_REQUEST, json.dumps(updated, separators=(',', ':')))
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
		ku.clear_property(_HAS_LIVE)
		ku.clear_property(_NEXT_START)
		ku.clear_property(_STARTS)
		request = {'id': uuid.uuid4().hex, 'sport': sport, 'accounts': accounts, 'created': time.time(), 'reload': reload, 'url_refresh': url_refresh}
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
		if cached is None and _current_primary_fixtures(result): cached = result['fixtures'], result.get('auxiliary')
		return ('cached_unavailable', cached[0], cached[1]) if cached is not None else ('unavailable', None, None)
	if result.get('complete') is False:
		if not _current_primary_fixtures(result): return 'pending', None, None
		ku.set_property(_FIXTURE_ACK, _fixture_revision(result))
		return 'partial', result['fixtures'], result.get('auxiliary')
	return 'ready', result['fixtures'], result.get('auxiliary')


def fixture_reload():
	"""Consume the refresh intent once the fixture snapshot is ready."""
	request = _read(_FIXTURE_REQUEST)
	reload = request.pop('reload', '')
	if reload: ku.set_property(_FIXTURE_REQUEST, json.dumps(request, separators=(',', ':')))
	if request.get('id'): ku.set_property(_FIXTURE_ACK, request['id'])
	return reload


def _set_schedule_timers(sport, fixtures):
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


def has_live(sport):
	return ku.get_property(_HAS_LIVE) == sport


def _current(sport):
	if not ku.get_visibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.soccer)'): return False
	if ku.get_property('LiveTV.Sport') != sport: return False
	path = ku.get_infolabel('Container.FolderPath')
	query = parse_qs(urlsplit(path).query)
	return query.get('mode') == ['livetv.soccer'] and query.get('sport', ['soccer']) == [sport]


class SportsProgress:
	def __init__(self):
		self.focus = None
		self.fixture_workers = {}
		self.fixture_results = SimpleQueue()
		self.fixture_refreshed = ''
		self.fixture_seen = ''

	def _fixture_work(self, request):
		started = time.monotonic()
		outcome = 'unavailable'
		def publish(fixtures, auxiliary, expires, complete=True):
			encoded = json.dumps({'id': request['id'], 'fixtures': fixtures, 'auxiliary': auxiliary, 'captured': time.time(), 'expires': expires, 'complete': complete}, ensure_ascii=False, separators=(',', ':'))
			if len(encoded.encode('utf-8')) > _RESULT_LIMIT:
				if complete: raise ValueError('Result too large')
				return
			self.fixture_results.put((request['id'], encoded))

		def primary(result, expires):
			fixtures, _, other, status = result
			publish(fixtures, {'sport_fixtures': other, 'status_text': status}, expires, complete=False)

		try:
			from menus import livetv
			if livetv._feed_accounts_fingerprint() != request['accounts']: raise ValueError('Account changed')
			sport = request['sport']
			auxiliary = None
			if sport == 'soccer':
				(fixtures, _, other, status), expires = livetv._live_soccer_fixtures(include_upcoming=True, with_expiry=True, on_primary=None if request.get('background') else primary)
				auxiliary = {'sport_fixtures': other, 'status_text': status}
			elif sport == 'f1': fixtures, expires = livetv._f1.get_schedule(with_expiry=True)
			else:
				profile = ku.translate_path(getattr(ku, 'profile_path', ''))
				fixtures, expires = livetv._tennis_fixtures_service.get_schedule(cache_dir=profile or None, with_expiry=True) if sport == 'tennis' else livetv._american_fixtures.get_schedule(sport, cache_dir=profile or None, with_expiry=True)
			publish(fixtures, auxiliary, expires)
			outcome = 'ok'
		except Exception: self.fixture_results.put((request['id'], json.dumps({'id': request['id'], 'unavailable': True})))
		record_timing('fixture_fetch', time.monotonic() - started, request.get('sport', ''), outcome)

	def _fixture_tick(self):
		request = _read(_FIXTURE_REQUEST)
		request_id = request.get('id', '')
		visible = bool(request_id and _current(request.get('sport')))
		while True:
			try: result_id, encoded = self.fixture_results.get_nowait()
			except Empty: break
			if visible and result_id == request_id and _read(_FIXTURE_REQUEST).get('id') == request_id:
				from menus import livetv
				if request.get('accounts') != livetv._feed_accounts_fingerprint(): continue
				result = json.loads(encoded)
				sport = request.get('sport')
				if result.get('complete') is False:
					if not _current_primary_fixtures(result) or _cached_fixtures(sport, request.get('accounts')) is not None: continue
				elif result.get('unavailable') and sport == 'soccer':
					previous = _read(_FIXTURE_RESULT)
					if previous.get('id') == request_id and previous.get('complete') is False and _current_primary_fixtures(previous):
						encoded = json.dumps(dict(previous, complete=True, unavailable=True), ensure_ascii=False, separators=(',', ':'))
				elif not _fixtures_unavailable(sport, result) and sport in ('soccer', 'hockey', 'football', 'basketball', 'tennis', 'f1'):
					captured = result.get('captured')
					if isinstance(captured, (int, float)) and not isinstance(captured, bool) and math.isfinite(captured):
						cache = {'captured': captured, 'expires': next_half_hour(captured), 'source_expires': result.get('expires'), 'accounts': request.get('accounts'), 'fixtures': result['fixtures'], 'auxiliary': result.get('auxiliary')}
						data = json.dumps(cache, ensure_ascii=False, separators=(',', ':'))
						if cache['expires'] > time.time() and len(data.encode('utf-8')) <= _RESULT_LIMIT: ku.set_property(_FIXTURE_CACHE + request['sport'], data)
				if not _fixtures_unavailable(sport, result): _set_schedule_timers(sport, result['fixtures'])
				ku.set_property(_FIXTURE_RESULT, encoded)
		if not visible:
			if request_id and self.fixture_seen == request_id and not ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)') and _read(_FIXTURE_REQUEST).get('id') == request_id:
				ku.clear_property(_FIXTURE_REQUEST)
				ku.clear_property(_FIXTURE_RESULT)
				self.fixture_seen = ''
			return
		self.fixture_seen = request_id
		result = _read(_FIXTURE_RESULT)
		if result.get('id') == request_id:
			revision = _fixture_revision(result)
			complete = result.get('complete') is not False
			if self.fixture_refreshed == revision or ku.get_property(_FIXTURE_ACK) == revision: return
			if not complete and not _current_primary_fixtures(result): return
			if ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'): return
			if ku.get_visibility('Container.Scrolling'): return
			if not ku.get_visibility('System.IdleTime(2)') and (not ku.get_visibility('Control.HasFocus(523)') or _fixture_focus()): return
			self.fixture_refreshed = revision
			self._capture_focus(request, revision)
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

	def _capture_focus(self, request, revision):
		self.focus = None
		try:
			if not ku.get_visibility('Control.HasFocus(523)'): return
			position = int(ku.get_infolabel('Container(523).CurrentItem')) - 1
			if position < 0: return
			identity = self._item_identity(position)
			if not identity[1]: return
			now = time.monotonic()
			self.focus = {'id': request['id'], 'sport': request['sport'], 'position': position, 'identity': identity, 'started': now, 'deadline': now + 90, 'revision': revision}
		except (AttributeError, RuntimeError, TypeError, ValueError): return

	def _restore_focus(self):
		focus = self.focus
		if not focus: return
		now = time.monotonic()
		idle_required = max(2, int(now - focus['started']) + 1)
		if focus['id'] != _read(_FIXTURE_REQUEST).get('id') or now >= focus['deadline'] or not ku.get_visibility('System.IdleTime(%d)' % idle_required):
			self.focus = None
			return
		if ku.get_visibility('Container.IsUpdating | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'): return
		if ku.get_visibility('Player.HasMedia | System.HasActiveModalDialog'):
			self.focus = None
			return
		if not _current(focus['sport']):
			self.focus = None
			return
		acknowledged = ku.get_property(_FIXTURE_ACK)
		if acknowledged == focus['id'] and focus['revision'] == focus['id'] + ':primary': acknowledged = focus['revision']
		if acknowledged != focus['revision']: return
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

	def tick(self):
		ku.set_property(_HEARTBEAT, str(time.time()))
		self._fixture_tick()
		self._restore_focus()

	def close(self):
		ku.clear_property(_HEARTBEAT)
		ku.clear_property(_FIXTURE_REQUEST)
		ku.clear_property(_FIXTURE_RESULT)
