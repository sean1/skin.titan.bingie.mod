"""Load each sports schedule independently for the combined sports view."""
import json
import time
from queue import Empty, SimpleQueue
from threading import Thread

from modules import kodi_utils as ku

SPORTS = ('soccer', 'hockey', 'football', 'basketball', 'tennis', 'f1')
PREFIX = 'LiveTV.Sports.Overview.'
ROW_IDS = dict(zip(SPORTS, range(9501, 9507)))


def _next_refresh(wall):
	local = time.localtime(wall)
	return wall + (30 - local.tm_min % 30) * 60 - local.tm_sec - wall % 1


def _load(sport, accounts, generation, results):
	from menus import livetv
	fixtures = None
	partial = False
	early = None
	try:
		if sport == 'soccer':
			def publish_primary(result, expires):
				nonlocal early
				try:
					previous = json.loads(ku.get_property(PREFIX + sport) or '{}')
					if previous.get('accounts') == accounts and time.time() < previous.get('fixture_expires', 0) and previous.get('fixtures'): return
				except (TypeError, ValueError): pass
				data = {'accounts': accounts, 'fixtures': result[0], 'fixture_expires': expires, 'fixture_partial': True, 'created': time.time()}
				encoded = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
				if len(encoded.encode('utf-8')) > 4 * 1024 * 1024: return
				early = data
				results.put((sport, accounts, generation, encoded))
			(fixtures, _, _, status), fixture_expires = livetv._live_soccer_fixtures(include_upcoming=True, with_expiry=True, on_primary=publish_primary)
			if status.startswith('Live fixtures unavailable'): raise ValueError('Fixtures unavailable')
			partial = any(part in ('Canadian Premier League unavailable', 'Canadian Championship live status unavailable') for part in status.split(' · ')[1:])
		elif sport == 'f1': fixtures, fixture_expires = livetv._f1.get_schedule(with_expiry=True)
		else:
			profile = ku.translate_path(getattr(ku, 'profile_path', ''))
			fixtures, fixture_expires = livetv._tennis_fixtures_service.get_schedule(cache_dir=profile or None, with_expiry=True) if sport == 'tennis' else livetv._american_fixtures.get_schedule(sport, cache_dir=profile or None, with_expiry=True)
		if not isinstance(fixtures, list): raise ValueError('Invalid fixtures')
		data = {'accounts': accounts, 'fixtures': fixtures, 'fixture_expires': fixture_expires, 'schedule_unavailable': partial, 'created': time.time()}
		encoded = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
		if len(encoded.encode('utf-8')) > 4 * 1024 * 1024: raise ValueError('Overview result too large')
		results.put((sport, accounts, generation, encoded))
	except Exception:
		data = dict(early, unavailable=True, fixture_partial=True) if early and time.time() < early['fixture_expires'] else {'accounts': accounts, 'fixtures': fixtures if isinstance(fixtures, list) else [], 'unavailable': True, 'created': time.time()}
		results.put((sport, accounts, generation, json.dumps(data, ensure_ascii=False, separators=(',', ':'))))


class SportsOverview:
	def __init__(self):
		self.workers = {}
		self.results = SimpleQueue()
		self.accounts = ''
		self.next_refresh = 0
		self.pending_refresh = set()
		self.generation = 0
		self.due = set()
		self.active = False
		self.snapshots = {}
		self.snapshot_started = {}
		self.deferred = set()

	def tick(self, active, now):
		from menus import livetv
		if not active:
			if self.active:
				self.generation += 1
				self.deferred.clear()
			self.active = False
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
			self.generation += 1
			self.due = set(SPORTS)
		wall = time.time()
		next_refresh = _next_refresh(wall)
		if wall >= self.next_refresh:
			self.generation += 1
			self.due = set(SPORTS)
		self.next_refresh = min(self.next_refresh, next_refresh) if wall < self.next_refresh else next_refresh
		while True:
			try: sport, result_accounts, generation, encoded = self.results.get_nowait()
			except Empty: break
			if result_accounts == accounts and generation == self.generation:
				ku.set_property(PREFIX + sport, encoded)
				self.pending_refresh.add(sport)
		for sport in tuple(self.deferred):
			if not ku.get_visibility('Control.HasFocus(%d)' % ROW_IDS[sport]):
				self.deferred.discard(sport)
				self.pending_refresh.add(sport)
		for sport, keys in self.snapshots.items():
			if keys and not ku.get_property(keys[-1].replace('Snapshot.', 'Completed.', 1)) and time.monotonic() - self.snapshot_started.get(sport, 0) >= 5 and not ku.get_visibility('Container(%d).IsUpdating' % ROW_IDS[sport]):
				self.pending_refresh.add(sport)
			if len(keys) > 1 and ku.get_property(keys[-1].replace('Snapshot.', 'Completed.', 1)):
				for key in keys[:-1]:
					ku.clear_property(key)
					ku.clear_property(key.replace('Snapshot.', 'Completed.', 1))
				del keys[:-1]
		if not ku.get_visibility('Player.HasMedia'):
			for sport in tuple(self.due):
				if sport not in self.workers or not self.workers[sport].is_alive():
					worker = Thread(target=_load, args=(sport, accounts, self.generation, self.results), daemon=True)
					self.workers[sport] = worker
					self.due.remove(sport)
					worker.start()
		if self.pending_refresh and not ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'):
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
				keys = self.snapshots.get(sport, [])
				if keys:
					if ku.get_visibility('Container(%d).IsUpdating' % control): continue
					if not ku.get_property(keys[-1].replace('Snapshot.', 'Completed.', 1)) and time.monotonic() - self.snapshot_started.get(sport, 0) < 5: continue
					while len(keys) >= 2:
						key = keys.pop(0)
						ku.clear_property(key)
						ku.clear_property(key.replace('Snapshot.', 'Completed.', 1))
				revision = str(time.time_ns())
				encoded = ku.get_property(PREFIX + sport)
				if ku.get_visibility('Control.HasFocus(%d)' % control):
					identity = ku.get_infolabel('Container(%d).ListItem.Property(LiveTV.EventIdentity)' % control)
					if identity and not ku.get_visibility('System.IdleTime(2)'): continue
					if identity:
						try:
							data = json.loads(encoded or '{}')
							entries = [entry for entry in livetv._sports_overview_entries(sport, data.get('fixtures', [])) if entry[0] == 'Live']
							position = int(ku.get_infolabel('Container(%d).CurrentItem' % control)) - 1
							if position < 0: continue
							if not any(livetv._fixture_focus_identity(sport, entry[2]) == identity for entry in entries):
								previous = json.loads(ku.get_property(self.snapshots[sport][-1]) or '{}')
								held = [fixture for fixture in previous.get('fixtures', []) if livetv._fixture_focus_identity(sport, fixture) == identity]
								if len(held) != 1: continue
								data['fixtures'] = [fixture for fixture in data.get('fixtures', []) if livetv._fixture_focus_identity(sport, fixture) != identity] + held
								data['held_identity'] = identity
								self.deferred.add(sport)
							data.update(pin_identity=identity, pin_position=position)
							encoded = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
						except (TypeError, ValueError, KeyError): continue
				snapshot = PREFIX + 'Snapshot.' + sport + '.' + revision
				ku.set_property(snapshot, encoded)
				self.snapshots.setdefault(sport, []).append(snapshot)
				self.snapshot_started[sport] = time.monotonic()
				ku.set_property('LiveTV.Sports.OverviewURL.' + sport, ku.build_url({'mode': 'livetv.sports_overview_row', 'sport': sport, 'revision': revision}))
				self.pending_refresh.discard(sport)
		if ku.get_visibility('Control.HasFocus(523) + !System.HasActiveModalDialog'):
			for control in ROW_IDS.values():
				if ku.get_infolabel('Container(%d).ListItemAbsolute(0).Property(LiveTV.EventIdentity)' % control):
					ku.execute_builtin('SetFocus(%d)' % control)
					break
			else:
				if ku.get_visibility('Control.IsVisible(9507)'): ku.execute_builtin('SetFocus(9507)')

	def pending(self):
		if not self.active: return False
		if self.due or not self.results.empty() or any(worker.is_alive() for worker in self.workers.values()): return True
		return any(not ku.get_visibility('Control.HasFocus(%d)' % ROW_IDS[sport]) for sport in self.pending_refresh)
