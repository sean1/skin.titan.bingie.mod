"""Refresh sports schedules and feeds while the sports screen is open."""
import json
import math
from time import monotonic, time
from queue import Empty, SimpleQueue
from threading import Event, Thread

from modules import kodi_utils as ku
from modules import livetv_sports_progress as progress
from modules.livetv_feed_selection import FeedSelection
from modules.livetv_sports import record_timing


_SPORTS_WINDOW = 'Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.soccer)'
_LIVE_INTERVAL = 2 * 60
_GUIDE_RETRY = 5 * 60
_START_RETRY = 60
_START_WINDOW = 15 * 60
_SCHEDULE_INTERVAL = 30 * 60


def _refresh_guide(canceled, on_update, permissive, sport):
	from modules.livetv_sports_guide import refresh
	started = monotonic()
	try: refresh(canceled, on_update, permissive)
	finally: record_timing('guide_refresh', monotonic() - started, sport, 'cancelled' if canceled.is_set() else '')


class SportsRefresh:
	def __init__(self):
		self.progress = progress.SportsProgress()
		self.feed_selection = FeedSelection()
		self.sport = ''
		self.next_live_check = 0
		self.next_schedule_check = 0
		self.start_checks = {}
		self.guide_worker = None
		self.guide_cancel = None
		self.guide_results = SimpleQueue()
		self.guide_next_attempt = 0
		self.guide_pending = False
		self.guide_mode = None

	def close(self):
		self.progress.close()
		self.feed_selection.close()
		if self.guide_cancel: self.guide_cancel.set()

	def _guide_tick(self, active, now, permissive=False, sport=''):
		if active and self.guide_mode != permissive:
			if self.guide_cancel: self.guide_cancel.set()
			self.guide_mode = permissive
			self.guide_pending = False
			self.guide_next_attempt = 0
		while True:
			try: self.guide_results.get_nowait()
			except Empty: break
			if active: self.guide_pending = True
		if not active:
			if self.guide_cancel: self.guide_cancel.set()
			self.guide_pending = False
			self.guide_next_attempt = 0
			return
		if (self.guide_worker is None or not self.guide_worker.is_alive()) and now >= self.guide_next_attempt:
			self.guide_cancel = Event()
			self.guide_worker = Thread(target=_refresh_guide, args=(self.guide_cancel, lambda: self.guide_results.put(True), permissive, sport), daemon=True)
			self.guide_worker.start()
			self.guide_next_attempt = now + _GUIDE_RETRY
		if not self.guide_pending or progress.pending(): return
		if ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'): return
		if not ku.get_visibility('System.IdleTime(15)') or ku.get_property('LiveTV.Soccer.Reload'): return
		self.guide_pending = False
		progress.invalidate()
		ku.execute_builtin('Container.Refresh')

	def tick(self):
		self.progress.tick()
		self.feed_selection.tick()
		visible = ku.get_visibility(_SPORTS_WINDOW)
		now = monotonic()
		sport = ku.get_property('LiveTV.Sport')
		self._guide_tick(visible and bool(sport) and progress.has_live(sport) and not ku.get_visibility('Player.HasMedia'), now, sport != 'soccer', sport)
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
