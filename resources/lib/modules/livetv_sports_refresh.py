"""Refresh the sports catalogue while the sports screen is left open."""
from time import monotonic, time
from queue import Empty, SimpleQueue
from threading import Event, Thread

from modules import kodi_utils as ku
from modules import livetv_sports_progress as progress


_SPORTS_WINDOW = 'Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.soccer)'
_INTERVAL = 15 * 60
_GUIDE_RETRY = 5 * 60
_START_RETRY = 60


class SportsRefresh:
	def __init__(self):
		self.progress = progress.SportsProgress()
		self.opened_at = None
		self.start_checked_at = 0
		self.guide_worker = None
		self.guide_cancel = None
		self.guide_results = SimpleQueue()
		self.guide_next_attempt = 0
		self.guide_pending = False

	def close(self):
		self.progress.close()
		if self.guide_cancel: self.guide_cancel.set()

	def _guide_tick(self, active, now):
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
			from modules.livetv_sports_guide import refresh
			self.guide_cancel = Event()
			self.guide_worker = Thread(target=refresh, args=(self.guide_cancel, lambda: self.guide_results.put(True)), daemon=True)
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
		visible = ku.get_visibility(_SPORTS_WINDOW)
		now = monotonic()
		self._guide_tick(visible and ku.get_property('LiveTV.Sport') == 'soccer' and progress.has_live('soccer'), now)
		if not visible:
			self.opened_at = None
			return
		if self.opened_at is None:
			self.opened_at = now
			return
		try: start = float(ku.get_property(progress._NEXT_START))
		except (TypeError, ValueError): start = 0
		start_due = start > 0 and 0 <= time() - start <= 15 * 60 and now - self.start_checked_at >= _START_RETRY
		if (now - self.opened_at < _INTERVAL and not start_due) or progress.pending(): return
		if ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'): return
		if not ku.get_visibility('System.IdleTime(15)') or ku.get_property('LiveTV.Soccer.Reload'): return
		self.opened_at = now
		if start_due: self.start_checked_at = now
		ku.set_property('LiveTV.Soccer.Reload', 'refresh')
		ku.execute_builtin('Container.Refresh')
