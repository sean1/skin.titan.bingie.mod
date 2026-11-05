"""Load each sports schedule independently for the combined sports view."""
import json
import time
from queue import Empty, SimpleQueue
from threading import Event, Lock, Thread

from modules import kodi_utils as ku

SPORTS = ('soccer', 'hockey', 'football', 'basketball', 'tennis', 'f1')
PREFIX = 'LiveTV.Sports.Overview.'
ROW_IDS = dict(zip(SPORTS, range(9501, 9507)))


def _next_refresh(wall):
	local = time.localtime(wall)
	return wall + (30 - local.tm_min % 30) * 60 - local.tm_sec - wall % 1


class _SharedCatalog:
	"""Coalesce each provider download within one overview refresh generation."""
	def __init__(self, accounts, force):
		self.accounts = accounts
		self.force = force
		self.lock = Lock()
		self.entries = {}

	def get(self, identity, account, *, on_status=None, _deadline=None, **kwargs):
		from menus import livetv
		with self.lock:
			entry = self.entries.get(identity)
			if entry is None:
				entry = {'ready': Event()}
				self.entries[identity] = entry
				owner = True
			else: owner = False
		if owner:
			try:
				if livetv._feed_accounts_fingerprint() != self.accounts: raise ValueError('Account changed')
				status = []
				while True:
					timeout = 0.1 if _deadline is None else min(0.1, max(0, _deadline - time.monotonic()))
					if _deadline is not None and timeout <= 0: raise OSError('Live TV catalog timed out')
					if livetv._CATALOG_PREFETCH_SLOTS.acquire(timeout=timeout): break
				try:
					entry['items'] = livetv._catalog(account, 'get_live_streams', force_refresh=self.force, notify_stale=False, on_status=lambda created, stale: status.append((created, stale)), _deadline=_deadline)
				finally: livetv._CATALOG_PREFETCH_SLOTS.release()
				if livetv._feed_accounts_fingerprint() != self.accounts: raise ValueError('Account changed')
				entry['status'] = status
			except Exception as error:
				entry['error'] = error
			finally:
				entry['ready'].set()
		else:
			timeout = None if _deadline is None else max(0, _deadline - time.monotonic())
			if not entry['ready'].wait(timeout): raise OSError('Live TV catalog timed out')
		if 'error' in entry: raise entry['error']
		if on_status:
			for created, stale in entry.get('status', []): on_status(created, stale)
		return entry['items']


def _load(sport, accounts, generation, catalog, results):
	from menus import livetv
	fixtures = None
	partial = False
	try:
		if sport == 'soccer':
			(fixtures, _, _, status), fixture_expires = livetv._live_soccer_fixtures(include_upcoming=True, with_expiry=True)
			if status.startswith('Live fixtures unavailable'): raise ValueError('Fixtures unavailable')
			partial = any(part in ('Canadian Premier League unavailable', 'Canadian Championship live status unavailable') for part in status.split(' · ')[1:])
		elif sport == 'f1': fixtures, fixture_expires = livetv._f1.get_schedule(with_expiry=True)
		else:
			profile = ku.translate_path(getattr(ku, 'profile_path', ''))
			fixtures, fixture_expires = livetv._tennis_fixtures_service.get_schedule(cache_dir=profile or None, with_expiry=True) if sport == 'tennis' else livetv._american_fixtures.get_schedule(sport, cache_dir=profile or None, with_expiry=True)
		if not isinstance(fixtures, list): raise ValueError('Invalid fixtures')
		try:
			previous = json.loads(ku.get_property(PREFIX + sport) or '{}')
			now = time.time()
			retain = previous.get('accounts') == accounts and previous.get('fixtures') == fixtures and 0 <= now - previous.get('created', 0) < 120 and now < previous.get('fixture_expires', 0)
		except (TypeError, ValueError): retain = False
		if retain:
			previous.update(matching=True, stale=True)
			results.put((sport, accounts, generation, json.dumps(previous, ensure_ascii=False, separators=(',', ':'))))
		else: results.put((sport, accounts, generation, json.dumps({'accounts': accounts, 'fixtures': fixtures, 'fixture_expires': fixture_expires, 'groups': [], 'matching': True, 'schedule_unavailable': partial, 'created': time.time()}, ensure_ascii=False, separators=(',', ':'))))
		failures, catalog_status = [], []
		secrets = [str(account.get(key) or '') for _, account in livetv._sports_accounts(livetv._load()) for key in ('username', 'password')]
		def publish(groups, unavailable, statuses, matching):
			clean = []
			for key, feeds in groups.items():
				items = {}
				for ref, feed in feeds.items():
					name = str(feed.get('name') or '')
					if any(livetv._catalog_redact(value, secrets) != value for value in (str(ref), str(feed.get('stream_id', '')), name)): continue
					item = {field: feed[field] for field in ('stream_id', '_provider', '_primary', '_guide_match', '_guide_until') if field in feed}
					item.update({'name': name, 'stream_icon': livetv._catalog_icon(feed.get('stream_icon'), secrets)})
					items[ref] = item
				clean.append((key, items))
			data = {'accounts': accounts, 'fixtures': fixtures, 'fixture_expires': fixture_expires, 'groups': clean, 'matching': matching, 'unavailable': bool(unavailable), 'schedule_unavailable': partial, 'stale': any(stale for _, stale in statuses), 'created': time.time()}
			encoded = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
			if len(encoded.encode('utf-8')) > 4 * 1024 * 1024: raise ValueError('Overview result too large')
			results.put((sport, accounts, generation, encoded))
		groups, _ = livetv._sports_catalog_groups(livetv._sports_primary(), sport, fixtures=fixtures, status_sink=failures.append, notify_stale=False, on_status=lambda created, stale: catalog_status.append((created, stale)), catalog_fetch=catalog.get, on_partial=lambda groups, unavailable, statuses: publish(groups, unavailable, statuses, True))
		publish(groups, failures and failures[-1], catalog_status, False)
	except Exception:
		results.put((sport, accounts, generation, json.dumps({'accounts': accounts, 'fixtures': fixtures if isinstance(fixtures, list) else [], 'unavailable': True, 'created': time.time()})))


def _refresh_guides(cancel, results, generation):
	from modules.livetv_sports_guide import refresh
	try:
		for permissive in (False, True):
			if cancel.is_set(): return
			refresh(cancel, lambda: None, permissive)
	finally:
		if not cancel.is_set(): results.put(generation)


class SportsOverview:
	def __init__(self):
		self.workers = {}
		self.results = SimpleQueue()
		self.accounts = ''
		self.next_refresh = 0
		self.pending_refresh = set()
		self.focus = None
		self.generation = 0
		self.due = set()
		self.guide_worker = None
		self.guide_cancel = None
		self.guide_results = SimpleQueue()
		self.next_guide = 0
		self.active = False
		self.forced = set()
		self.catalog_generation = None
		self.catalog = None

	def tick(self, active, now):
		from menus import livetv
		if not active:
			if self.active:
				self.generation += 1
				if self.guide_cancel: self.guide_cancel.set()
			self.active = False
			self.next_guide = 0
			self.focus = None
			self.pending_refresh = set()
			return
		if not self.active:
			ku.clear_property('LiveTV.Sports.OverviewEmpty')
			self.active = True
			self.due = set(SPORTS)
			self.next_refresh = _next_refresh(time.time())
		accounts = livetv._feed_accounts_fingerprint()
		if accounts != self.accounts:
			self.accounts = accounts
			if self.guide_cancel: self.guide_cancel.set()
			self.next_guide = 0
			self.generation += 1
			self.due = set(SPORTS)
			self.next_refresh = _next_refresh(time.time())
			ku.clear_property('LiveTV.Sports.OverviewEmpty')
			for sport in SPORTS:
				try:
					data = json.loads(ku.get_property(PREFIX + sport) or '{}')
					wall = time.time()
					valid = data.get('accounts') == accounts and 0 <= wall - data.get('created', 0) < 120 and wall < data.get('fixture_expires', 0)
				except (TypeError, ValueError): valid = False
				if not valid: ku.clear_property(PREFIX + sport)
		if ku.get_property(PREFIX + 'Refresh'):
			ku.clear_property(PREFIX + 'Refresh')
			self.forced = set(SPORTS)
			self.generation += 1
			self.due = set(SPORTS)
			if self.guide_cancel: self.guide_cancel.set()
			self.next_guide = 0
		wall = time.time()
		next_refresh = _next_refresh(wall)
		if wall >= self.next_refresh:
			self.generation += 1
			self.due = set(SPORTS)
			if self.guide_cancel: self.guide_cancel.set()
			self.next_guide = 0
		self.next_refresh = min(self.next_refresh, next_refresh) if wall < self.next_refresh else next_refresh
		while True:
			try: guide_generation = self.guide_results.get_nowait()
			except Empty: break
			if guide_generation == self.generation: self.due.update(SPORTS)
		if now >= self.next_guide and (self.guide_worker is None or not self.guide_worker.is_alive()) and not ku.get_visibility('Player.HasMedia'):
			self.guide_cancel = Event()
			self.guide_worker = Thread(target=_refresh_guides, args=(self.guide_cancel, self.guide_results, self.generation), daemon=True)
			self.guide_worker.start()
			self.next_guide = float('inf')
		while True:
			try: sport, result_accounts, generation, encoded = self.results.get_nowait()
			except Empty: break
			if result_accounts == accounts and generation == self.generation:
				ku.set_property(PREFIX + sport, encoded)
				self.pending_refresh.add(sport)
		if self.focus:
			control, revision, identity, started = self.focus
			idle_required = max(2, int(time.monotonic() - started) + 1)
			if time.monotonic() - started >= 90 or ku.get_visibility('Player.HasMedia | System.HasActiveModalDialog') or not ku.get_visibility('Control.HasFocus(%d)' % control) or not ku.get_visibility('System.IdleTime(%d)' % idle_required): self.focus = None
			elif not ku.get_visibility('Container(%d).IsUpdating' % control) and ku.get_infolabel('Container(%d).ListItemAbsolute(0).Property(LiveTV.OverviewRevision)' % control) == revision:
				try:
					count = min(10000, max(0, int(ku.get_infolabel('Container(%d).NumItems' % control))))
					matches = [index for index in range(count) if ku.get_infolabel('Container(%d).ListItemAbsolute(%d).Property(LiveTV.EventIdentity)' % (control, index)) == identity]
					position = int(ku.get_infolabel('Container(%d).CurrentItem' % control)) - 1
					if identity and len(matches) == 1 and position != matches[0]: ku.execute_builtin('Control.SetFocus(%d,%d,absolute)' % (control, matches[0]))
				except (TypeError, ValueError): pass
				self.focus = None
		if not ku.get_visibility('Player.HasMedia'):
			if self.catalog_generation != self.generation:
				self.catalog = _SharedCatalog(accounts, bool(self.forced))
				self.catalog_generation = self.generation
			for sport in tuple(self.due):
				if sport not in self.workers or not self.workers[sport].is_alive():
					worker = Thread(target=_load, args=(sport, accounts, self.generation, self.catalog, self.results), daemon=True)
					self.forced.discard(sport)
					self.workers[sport] = worker
					self.due.remove(sport)
					worker.start()
		if self.pending_refresh and not ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)') and ku.get_visibility('System.IdleTime(2)'):
			loading, unavailable, events = False, False, False
			for sport in SPORTS:
				try: data = json.loads(ku.get_property(PREFIX + sport) or '{}')
				except (TypeError, ValueError): data = {}
				if not data or data.get('accounts') != accounts or data.get('matching'): loading = True
				if data.get('accounts') == accounts and (data.get('unavailable') or data.get('schedule_unavailable')): unavailable = True
				if data.get('accounts') == accounts and any(livetv._schedule_state(fixture, time.time())[0] == 'Live' for fixture in data.get('fixtures', []) if isinstance(fixture, dict)): events = True
			status = ' · '.join(part for part in ('Updating sports information…' if loading else '', 'Some sports unavailable' if unavailable else '') if part)
			ku.set_property('LiveTV.Sports.OverviewEmpty', 'true' if not loading and not unavailable and not events else '')
			ku.set_property('LiveTV.Sports.OverviewStatus', status)
			for sport in tuple(self.pending_refresh):
				control = ROW_IDS[sport]
				revision = str(time.time_ns())
				if ku.get_visibility('Control.HasFocus(%d)' % control):
					identity = ku.get_infolabel('Container(%d).ListItem.Property(LiveTV.EventIdentity)' % control)
					self.focus = (control, revision, identity, time.monotonic()) if identity else None
				ku.set_property('LiveTV.Sports.OverviewRevision.' + sport, revision)
				self.pending_refresh.discard(sport)
