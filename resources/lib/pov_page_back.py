import xbmc
import xbmcgui

from modules import kodi_utils
from modules.dialogs import pov_page_back

if not kodi_utils.get_property('PovPageBackTransition'):
	window = xbmcgui.getCurrentWindowId()
	pov_page_back()
	while xbmcgui.getCurrentWindowId() == window and xbmc.getCondVisibility('System.HasActiveModalDialog'):
		if kodi_utils.monitor.waitForAbort(0.1): break
	if xbmcgui.getCurrentWindowId() == window and xbmc.getCondVisibility('Control.HasFocus(8990) | Control.HasFocus(8991)'):
		controls = (610, 620, 630, 699) if window == 11122 else (80, 51)
		for control in controls:
			if xbmc.getCondVisibility('Control.IsVisible(%s) + Control.IsEnabled(%s)' % (control, control)):
				xbmc.executebuiltin('SetFocus(%s)' % control)
				break
