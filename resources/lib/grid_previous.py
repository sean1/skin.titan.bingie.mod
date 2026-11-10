import sys

import xbmc


def move_previous(control_id, position, folder, window=None):
	if not xbmc.getCondVisibility('Control.HasFocus(%d) + !Container(%d).IsUpdating' % (control_id, control_id)):
		return
	if window is not None and window != xbmc.getInfoLabel('System.CurrentWindow'):
		return
	try:
		current = int(xbmc.getInfoLabel('Container(%d).CurrentItem' % control_id))
		count = int(xbmc.getInfoLabel('Container(%d).NumAllItems' % control_id))
		visible_count = int(xbmc.getInfoLabel('Container(%d).NumItems' % control_id))
		# Kodi excludes a parent folder from CurrentItem and NumItems, but not NumAllItems.
		index = current - 1 + (count - visible_count)
		if 0 < index < count and int(position) == index + 1 and folder == xbmc.getInfoLabel('Container.FolderPath'):
			xbmc.executebuiltin('SetFocus(%d,%d,absolute)' % (control_id, index - 1))
	except ValueError:
		pass


if len(sys.argv) == 3 and xbmc.getCondVisibility('Window.IsActive(videos)'):
	move_previous(523, sys.argv[1], sys.argv[2])
elif len(sys.argv) == 5:
	try:
		move_previous(int(sys.argv[1]), sys.argv[2], sys.argv[4], sys.argv[3])
	except ValueError:
		pass
