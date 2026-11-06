"""Refresh sports schedules and feeds while the sports screen is open."""
import json
import math
from time import monotonic, time
from queue import Empty, SimpleQueue
from threading import Event, Thread

from modules import kodi_utils as ku
from modules import livetv_sports_progress as progress
from modules.livetv_sports_overview import SportsOverview, SPORTS, PREFIX, _load as _load_overview
from modules.livetv_feed_selection import FeedSelection
from modules.livetv_search import LiveTVSearch
from modules.livetv_browse_refresh import BrowseRefresh


_SPORTS_WINDOW = 'Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.soccer)'
_LIVE_INTERVAL = 2 * 60
_START_RETRY = 60
_START_WINDOW = 15 * 60
_SCHEDULE_INTERVAL = 30 * 60
_WARM_SPACING = 30
_MATCH_WARM_SPACING = 45


def _warm_schedule(sport, fingerprint, results, canceled):
	from menus import livetv
	if canceled.is_set() or livetv._feed_accounts_fingerprint() != fingerprint: return
	_load_overview(sport, fingerprint, 0, results)


class SportsRefresh:
	def __init__(self):
		self.progress = progress.SportsProgress()
		self.overview = SportsOverview()
		self.feed_selection = FeedSelection()
		self.search = LiveTVSearch()
		self.browse_refresh = BrowseRefresh()
		self.sport = ''
		self.next_live_check = 0
		self.next_schedule_check = 0
		self.start_checks = {}
		self.warm_worker = None
		self.warm_cancel = Event()
		self.match_results = SimpleQueue()
		self.match_next = monotonic() + _WARM_SPACING
		self.match_index = 0
		self.match_baseline = None

	def close(self):
		self.warm_cancel.set()
		self.progress.close()
		self.feed_selection.close()

	def _warm_tick(self, now):
		from menus import livetv
		blocked = not ku.get_visibility('System.IdleTime(10)')
		blocked = blocked or ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel) | String.Contains(Container.FolderPath,mode=livetv)')
		while True:
			try: sport, accounts, generation, encoded = self.match_results.get_nowait()
			except Empty: break
			try:
				data = json.loads(encoded)
				baseline = self.match_baseline
				if (not blocked and baseline and baseline[:2] == (sport, accounts) and accounts == livetv._feed_accounts_fingerprint() and not data.get('matching')
						and (not data.get('unavailable') or data.get('fixtures')) and data.get('accounts') == accounts and 0 <= time() - data.get('created', 0) < 120
						and time() < data.get('fixture_expires', 0) and ku.get_property(PREFIX + sport) == baseline[2]):
					ku.set_property(PREFIX + sport, encoded)
					self.match_baseline = (sport, accounts, encoded)
			except (TypeError, ValueError): pass
		if blocked or (self.warm_worker and self.warm_worker.is_alive()): return
		if now < self.match_next: return
		identity = livetv._feed_accounts_fingerprint()
		if not livetv._sports_accounts(livetv._load()):
			self.match_next = now + _MATCH_WARM_SPACING
			return
		sport = SPORTS[self.match_index % len(SPORTS)]
		self.match_index += 1
		self.match_baseline = (sport, identity, ku.get_property(PREFIX + sport))
		self.warm_worker = Thread(target=_warm_schedule, args=(sport, identity, self.match_results, self.warm_cancel), daemon=True)
		self.warm_worker.start()
		self.match_next = now + _MATCH_WARM_SPACING

	def tick(self):
		self.progress.tick()
		self.feed_selection.tick()
		self.search.tick()
		self.browse_refresh.tick()
		visible = ku.get_visibility(_SPORTS_WINDOW)
		now = monotonic()
		self._warm_tick(now)
		sport = ku.get_property('LiveTV.Sport')
		self.overview.tick(visible and sport == 'all' and progress._current('all'), now)
		if sport == 'all' and visible: return
		if not visible:
			if self.sport:
				if ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'): return
				ku.clear_property(progress._HAS_LIVE)
				ku.clear_property(progress._NEXT_START)
				ku.clear_property(progress._STARTS)
			self.sport = ''
			self.start_checks = {}
			return
		wall = time()
		if sport != self.sport:
			self.sport = sport
			self.next_live_check = now + _LIVE_INTERVAL
			self.next_schedule_check = progress.next_half_hour(wall)
			self.start_checks = {}
		if not sport or not progress._current(sport) or ku.get_visibility('Player.HasMedia') or ku.get_property('LiveTV.Soccer.Reload'): return
		try: starts = json.loads(ku.get_property(progress._STARTS) or '[]')
		except (TypeError, ValueError): starts = []
		if not isinstance(starts, list): starts = []
		starts = [start for start in starts if isinstance(start, (int, float)) and not isinstance(start, bool) and math.isfinite(start)]
		self.start_checks = {start: checked for start, checked in self.start_checks.items() if start in starts and wall - start <= _START_WINDOW}
		due = [start for start in starts if 0 <= wall - start <= _START_WINDOW and (start not in self.start_checks or now - self.start_checks[start] >= _START_RETRY)]
		if self.next_schedule_check - wall > _SCHEDULE_INTERVAL: self.next_schedule_check = progress.next_half_hour(wall)
		schedule_due = wall >= self.next_schedule_check
		live_due = progress.has_live(sport) and now >= self.next_live_check
		retry_due = False
		if now >= self.next_live_check and not live_due:
			fixture_request = progress._read(progress._FIXTURE_REQUEST)
			fixture_result = progress._read(progress._FIXTURE_RESULT)
			request_id = fixture_request.get('id', '')
			retry_due = not request_id or fixture_request.get('sport') != sport or (fixture_result.get('id') == request_id and progress._fixtures_unavailable(sport, fixture_result))
			if not retry_due: self.next_live_check = now + _LIVE_INTERVAL
		if not (due or schedule_due or live_due or retry_due): return
		if not progress.request_background(sport, 'schedule'): return
		for start in due: self.start_checks[start] = now
		if schedule_due: self.next_schedule_check = progress.next_half_hour(wall)
		self.next_live_check = now + _LIVE_INTERVAL
