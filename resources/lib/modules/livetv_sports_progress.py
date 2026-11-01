"""Coordinate progressive sports feed matching across Kodi Python invocations."""
import json
import time
import uuid
from datetime import datetime
from queue import Empty, SimpleQueue
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from modules import kodi_utils as ku


_REQUEST = 'LiveTV.Sports.Progress.Request'
_RESULT = 'LiveTV.Sports.Progress.Result'
_CANCEL = 'LiveTV.Sports.Progress.Cancelled'
_HEARTBEAT = 'LiveTV.Sports.Progress.Heartbeat'
_CONSUME = 'LiveTV.Sports.Progress.Consume'
_ACK = 'LiveTV.Sports.Progress.Acknowledged'
_NEXT_START = 'LiveTV.Sports.NextStart'
_HAS_LIVE = 'LiveTV.Sports.HasLive'
_REQUEST_LIMIT = 1024 * 1024
_RESULT_LIMIT = 4 * 1024 * 1024
_MAX_AGE = 15 * 60


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


def invalidate(request_id=None):
	request_id = request_id or _read(_REQUEST).get('id', '')
	if not request_id: return
	ku.set_property(_CANCEL, request_id)
	ku.clear_property(_RESULT + '.' + request_id)


def _set_soccer_status(status, unavailable):
	message = ''
	if status:
		created, stale = status
		when = datetime.fromtimestamp(created).astimezone().strftime('%a %H:%M')
		message = ('Saved channels from %s; refresh failed' if stale else 'Updated %s') % when
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
		except (KeyError, TypeError, ValueError, OverflowError, OSError): pass
	ku.set_property(_NEXT_START, str(min(starts)) if starts else '')
	if not fixtures:
		invalidate()
		ku.set_property('LiveTV.Sports.CatalogUnavailable', '')
		if sport == 'soccer': ku.set_property('LiveTV.Soccer.Status', '')
		return fixtures, {}
	from menus import livetv
	def synchronous():
		invalidate()
		try:
			account = livetv._sports_primary()
			if not account: raise ValueError('Account unavailable')
			status = []
			groups, _ = livetv._sports_catalog_groups(account, sport, reload, fixtures=fixtures, on_status=lambda created, stale: status.append((created, stale)))
			if sport == 'soccer': _set_soccer_status(status[-1] if status else None, ku.get_property('LiveTV.Sports.CatalogUnavailable'))
			return fixtures, groups
		except Exception:
			ku.set_property('LiveTV.Sports.CatalogUnavailable', 'true')
			if sport == 'soccer': _set_soccer_status(None, True)
			return fixtures, {}
	try: alive = 0 <= time.time() - float(ku.get_property(_HEARTBEAT)) < 30
	except (TypeError, ValueError): alive = False
	if not alive: return synchronous()
	accounts = livetv._feed_accounts_fingerprint()
	request = _read(_REQUEST)
	result = _read(_RESULT + '.' + request.get('id', ''))
	consume = bool(not force_refresh and request.get('id') and ku.get_property(_CONSUME) == request['id'] and result.get('id') == request['id'])
	new_refresh = force_refresh or (reload == 'refresh' and not consume and (request.get('reload') != 'refresh' or result.get('id') == request.get('id')))
	fresh_fixtures = (fixtures == request.get('fixtures') and auxiliary == request.get('auxiliary')) or consume
	if _valid(request, sport) and ku.get_property(_CANCEL) != request.get('id') and request.get('accounts') == accounts and not new_refresh and fresh_fixtures:
		if result.get('id') == request.get('id'):
			try:
				if result.get('sync'): return synchronous()
				groups = _groups_from_json(result['groups'])
				ku.set_property('LiveTV.Sports.CatalogUnavailable', result.get('unavailable', ''))
				if sport == 'soccer': _set_soccer_status(result.get('status'), result.get('unavailable'))
				ku.set_property(_ACK, request['id'])
				ku.clear_property(_CONSUME)
				return request['fixtures'], groups
			except (KeyError, TypeError, ValueError): pass
		return request['fixtures'], None
	previous_id = request.get('id', '')
	request = {'id': uuid.uuid4().hex, 'sport': sport, 'reload': reload, 'fixtures': fixtures, 'auxiliary': auxiliary, 'created': time.time(), 'accounts': accounts}
	try: data = json.dumps(request, ensure_ascii=False, separators=(',', ':'))
	except (TypeError, ValueError): return synchronous()
	if len(data.encode('utf-8')) > _REQUEST_LIMIT: return synchronous()
	if previous_id: invalidate(previous_id)
	ku.set_property(_REQUEST, data)
	ku.set_property('LiveTV.Sports.CatalogUnavailable', '')
	if sport == 'soccer': ku.set_property('LiveTV.Soccer.Status', '')
	return fixtures, None


def pending():
	request = _read(_REQUEST)
	request_id = request.get('id', '')
	if not request_id or ku.get_property(_CANCEL) == request_id: return False
	if ku.get_property(_ACK) == request_id: return False
	result = _read(_RESULT + '.' + request_id)
	return result.get('id') != request_id


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
		self.workers = {}
		self.seen_id = ''
		self.refreshed_id = ''
		self.refreshed_at = 0
		self.results = SimpleQueue()
		self.focus = None

	@staticmethod
	def _item_identity(position):
		return tuple(ku.get_infolabel('Container(523).ListItemAbsolute(%d).Property(LiveTV.%s)' % (position, name)) for name in ('Match', 'League', 'Kickoff'))

	def _capture_focus(self, request):
		self.focus = None
		try:
			if not ku.get_visibility('Control.HasFocus(523)'): return
			position = int(ku.get_infolabel('Container(523).CurrentItem')) - 1
			if position < 0: return
			identity = self._item_identity(position)
			if not identity[0]: return
			now = time.monotonic()
			self.focus = {'id': request['id'], 'sport': request['sport'], 'position': position, 'identity': identity, 'started': now, 'deadline': now + 90}
		except (AttributeError, RuntimeError, TypeError, ValueError): return

	def _restore_focus(self, request, visible):
		focus = self.focus
		if not focus: return
		now = time.monotonic()
		idle_required = max(2, int(now - focus['started']) + 1)
		if focus['id'] != request.get('id') or now >= focus['deadline'] or not ku.get_visibility('System.IdleTime(%d)' % idle_required):
			self.focus = None
			return
		if ku.get_visibility('Container.IsUpdating | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'): return
		if ku.get_visibility('Player.HasMedia | System.HasActiveModalDialog'):
			self.focus = None
			return
		if not visible:
			self.focus = None
			return
		if ku.get_property(_ACK) != focus['id']: return
		try:
			if not ku.get_visibility('Control.HasFocus(523)'):
				self.focus = None
				return
			position = int(ku.get_infolabel('Container(523).CurrentItem')) - 1
			if self._item_identity(focus['position']) == focus['identity'] and position != focus['position']:
				ku.execute_builtin('Control.SetFocus(523,%d,absolute)' % focus['position'])
		except (AttributeError, RuntimeError, TypeError, ValueError): pass
		self.focus = None

	def _work(self, request):
		try:
			from menus import livetv
			statuses, unavailable = [], []
			account = livetv._sports_primary()
			if not account or livetv._feed_accounts_fingerprint() != request['accounts']: raise ValueError('Account changed')
			groups, _ = livetv._sports_catalog_groups(account, request['sport'], request['reload'], on_status=lambda created, stale: statuses.append((created, stale)), fixtures=request['fixtures'], status_sink=unavailable.append, notify_stale=False)
			secrets = [str(a.get(key) or '') for _, a in livetv._sports_accounts(livetv._load()) for key in ('username', 'password')]
			cleaned = []
			for key, feeds in groups.items():
				safe_feeds = {}
				for ref, feed in feeds.items():
					stream_id = feed.get('stream_id')
					name = str(feed.get('name') or '')
					if any(livetv._catalog_redact(value, secrets) != value for value in (str(ref), str(stream_id), name)):
						self.results.put((request['id'], json.dumps({'id': request['id'], 'sync': True})))
						return
					item = {name: feed[name] for name in ('stream_id', '_provider', '_primary', '_guide_match') if name in feed}
					item['name'] = name
					item['stream_icon'] = livetv._catalog_icon(feed.get('stream_icon'), secrets)
					safe_feeds[ref] = item
				cleaned.append((key, safe_feeds))
			result = {'id': request['id'], 'groups': cleaned, 'unavailable': unavailable[-1] if unavailable else '', 'status': statuses[-1] if statuses else None}
			encoded = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
			if len(encoded.encode('utf-8')) > _RESULT_LIMIT: raise ValueError('Result too large')
		except Exception:
			encoded = json.dumps({'id': request['id'], 'groups': [], 'unavailable': 'true', 'status': None})
		self.results.put((request['id'], encoded))

	def tick(self):
		ku.set_property(_HEARTBEAT, str(time.time()))
		request = _read(_REQUEST)
		request_id = request.get('id', '')
		visible = bool(request_id and ku.get_property(_CANCEL) != request_id and _valid(request, request.get('sport')) and _current(request.get('sport')))
		self._restore_focus(request, visible)
		if not visible and self.seen_id and request_id == self.seen_id:
			if ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'): return
			invalidate(request_id)
			self.seen_id = ''
			return
		if visible:
			self.seen_id = request_id
		while True:
			try: result_id, encoded = self.results.get_nowait()
			except Empty: break
			if visible and request_id == result_id and ku.get_property(_CANCEL) != result_id:
				ku.set_property(_RESULT + '.' + result_id, encoded)
				result = json.loads(encoded)
				status = result.get('status')
				if status and status[1]: ku.dialog.notification('Live TV', 'Showing saved channels; refresh failed.')
		if not visible: return
		if ku.get_property(_ACK) == request_id: return
		result = _read(_RESULT + '.' + request_id)
		if result.get('id') == request_id:
			if self.refreshed_id == request_id and time.monotonic() - self.refreshed_at < 10: return
			if ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'): return
			if not ku.get_visibility('System.IdleTime(2)'): return
			self.refreshed_id = request_id
			self.refreshed_at = time.monotonic()
			self._capture_focus(request)
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
		invalidate()
