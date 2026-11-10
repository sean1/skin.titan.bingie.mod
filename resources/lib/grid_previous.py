import sys

import xbmc


if len(sys.argv) == 3 and xbmc.getCondVisibility('Window.IsActive(videos) + Control.HasFocus(523) + !Container.IsUpdating'):
	try:
		current = int(xbmc.getInfoLabel('Container(523).CurrentItem'))
		count = int(xbmc.getInfoLabel('Container(523).NumAllItems'))
		visible_count = int(xbmc.getInfoLabel('Container(523).NumItems'))
		# Kodi excludes a parent folder from CurrentItem and NumItems, but not NumAllItems.
		index = current - 1 + (count - visible_count)
		if 0 < index < count and int(sys.argv[1]) == index + 1 and sys.argv[2] == xbmc.getInfoLabel('Container.FolderPath'):
			xbmc.executebuiltin('SetFocus(523,%d,absolute)' % (index - 1))
	except ValueError:
		pass
