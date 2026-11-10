import sys

import xbmc



def navigate(direction, control_id, position, window, shelves, fallback):
	if control_id not in shelves or direction not in ('left', 'right'):
		return
	if window != xbmc.getInfoLabel('System.CurrentWindow'):
		return
	if not xbmc.getCondVisibility('Control.HasFocus(%d) + !Container(%d).IsUpdating' % (control_id, control_id)):
		return
	if xbmc.getInfoLabel('Container(%d).ListItem.CurrentItem' % control_id) != position:
		return
	index = shelves.index(control_id)
	others = shelves[index + 1:] if direction == 'right' else reversed(shelves[:index])
	for target in others:
		if not xbmc.getCondVisibility('Control.IsVisible(%d) + Control.IsEnabled(%d) + !Container(%d).IsUpdating' % (target, target, target)):
			continue
		try:
			count = int(xbmc.getInfoLabel('Container(%d).NumAllItems' % target))
		except ValueError:
			continue
		if count:
			item = 0 if direction == 'right' else count - 1
			xbmc.executebuiltin('SetFocus(%d,%d,absolute)' % (target, item))
			return
	if fallback == 'sports':
		xbmc.executebuiltin('SetProperty(ShowViewSubMenu,show,Home)')
		xbmc.executebuiltin('SetFocus(9002)')
	elif fallback.isdigit():
		xbmc.executebuiltin('SetFocus(%d)' % int(fallback))


if len(sys.argv) == 7:
	try:
		navigate(sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], tuple(int(value) for value in sys.argv[5].split('|')), sys.argv[6])
	except ValueError:
		pass
