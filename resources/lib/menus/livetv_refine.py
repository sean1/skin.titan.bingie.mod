"""Browse Live TV channels in a paged card view with simple refinements."""
import json

import xbmc
from modules import kodi_utils as ku
from menus import livetv

PAGE_SIZE = 100
STATE = 'Bingie.LiveTV.Refine'


def _state():
	try:
		value = json.loads(ku.get_property(STATE))
		if isinstance(value, dict): return {'category': str(value.get('category') or ''), 'category_name': str(value.get('category_name') or ''), 'name': str(value.get('name') or '')}
	except (TypeError, ValueError):
		pass
	return {'category': '', 'category_name': '', 'name': ''}


def _save(state):
	ku.set_property(STATE, json.dumps(state))
	ku.set_property('LiveTV.Refine.Category', state['category_name'] or 'All')
	ku.set_property('LiveTV.Refine.Name', state['name'] or 'Any')


def _url(state, page=0):
	return ku.build_url({'mode': 'livetv.refine', 'category': state['category'], 'category_name': state['category_name'], 'name': state['name'], 'page': str(page)})


def dispatch(params):
	action = params.get('action', '')
	if action:
		state = _state()
		if action == 'category':
			account = livetv._load()
			if not account: return livetv._error('No Live TV account is available.')
			try:
				categories = livetv._catalog(account, 'get_live_categories')
				if not isinstance(categories, list): raise ValueError('Invalid categories')
				options = [('', 'All')] + [(str(item['category_id']), str(item.get('category_name') or 'Other')) for item in categories if isinstance(item, dict) and str(item.get('category_id') or '').isdecimal()]
				choice = ku.dialog.select('Channel category', [item[1] for item in options])
				if choice < 0: return
				state['category'], state['category_name'] = options[choice]
			except (OSError, ValueError, TypeError):
				return livetv._error('Could not load Live TV categories.')
		elif action == 'name':
			value = ku.dialog.input('Channel name', defaultt=state['name'])
			if value is None: return
			state['name'] = value.strip()
		elif action == 'clear':
			state = {'category': '', 'category_name': '', 'name': ''}
		elif action != 'apply':
			return
		_save(state)
		if action in ('apply', 'clear'):
			xbmc.executebuiltin('ClearProperty(ShowViewSubMenu,Home)')
			xbmc.executebuiltin('SetFocus(523)')
			xbmc.executebuiltin('Container.Update(%s,replace)' % _url(state))
		return

	state = {'category': str(params.get('category') or ''), 'category_name': str(params.get('category_name') or ''), 'name': str(params.get('name') or '')}
	if state['category'] and not state['category'].isdecimal(): state['category'], state['category_name'] = '', ''
	_save(state)
	account = livetv._load()
	if not account:
		livetv._error('No Live TV account is available.')
		livetv._finish(False)
		return
	try:
		streams = livetv._catalog(account, 'get_live_streams', **({'category_id': state['category']} if state['category'] else {}))
		if not isinstance(streams, list): raise ValueError('Invalid channels')
		needle = state['name'].casefold()
		matches = [item for item in streams if isinstance(item, dict) and str(item.get('stream_id') or '').isdecimal() and needle in str(item.get('name') or 'Live channel').casefold()]
		try: page = max(0, int(params.get('page', 0)))
		except (ValueError, TypeError): page = 0
		page = min(page, max(0, (len(matches) - 1) // PAGE_SIZE))
		if not matches: livetv._item('No channels found — clear refinements', {'mode': 'livetv.refine'})
		if page: livetv._item('Previous page', {'mode': 'livetv.refine', **state, 'page': str(page - 1)})
		for stream in matches[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
			name = str(stream.get('name') or 'Live channel')
			livetv._item(name, livetv._play_params(str(stream['stream_id']), name), False, stream.get('stream_icon'))
		if (page + 1) * PAGE_SIZE < len(matches): livetv._item('Next page', {'mode': 'livetv.refine', **state, 'page': str(page + 1)})
	except (OSError, ValueError, TypeError):
		livetv._error('Could not load Live TV channels.')
		livetv._finish(False)
		return
	livetv._finish()
	if xbmc.getCondVisibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.refine) + !Control.IsVisible(523)'):
		xbmc.executebuiltin('Container.SetViewMode(523)')
