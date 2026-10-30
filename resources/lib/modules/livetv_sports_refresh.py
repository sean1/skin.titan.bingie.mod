"""Refresh the sports catalogue while the sports screen is left open."""
from time import monotonic

from modules import kodi_utils as ku


_SPORTS_WINDOW = 'Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.soccer)'
_INTERVAL = 15 * 60


class SportsRefresh:
	def __init__(self):
		self.opened_at = None

	def tick(self):
		if not ku.get_visibility(_SPORTS_WINDOW):
			self.opened_at = None
			return
		now = monotonic()
		if self.opened_at is None:
			self.opened_at = now
			return
		if now - self.opened_at < _INTERVAL: return
		if ku.get_visibility('Player.HasMedia | Container.IsUpdating | System.HasActiveModalDialog | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)'): return
		if not ku.get_visibility('System.IdleTime(15)') or ku.get_property('LiveTV.Soccer.Reload'): return
		self.opened_at = now
		ku.set_property('LiveTV.Soccer.Reload', 'refresh')
		ku.execute_builtin('Container.Refresh')
