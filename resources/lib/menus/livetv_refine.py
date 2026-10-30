"""Browse Live TV channels in a paged card view with simple refinements."""
import json
import hashlib
import time
import xml.etree.ElementTree as ET
import zlib
from urllib.parse import parse_qs, urlsplit

import xbmc
from modules import kodi_utils as ku
from menus import livetv

PAGE_SIZE = 100
STATE = 'Bingie.LiveTV.Refine'
GUIDE_STATE = 'Bingie.LiveTV.Refine.Guide'


def _view(params):
	try: page = max(0, int(params.get('page', 0)))
	except (ValueError, TypeError): page = 0
	return {'category': str(params.get('category') or ''), 'category_name': str(params.get('category_name') or ''), 'name': str(params.get('name') or ''), 'page': str(page)}


def _visible(account, state):
	streams = livetv._catalog(account, 'get_live_streams', **({'category_id': state['category']} if state['category'] else {}))
	if not isinstance(streams, list): raise ValueError('Invalid channels')
	needle = state['name'].casefold()
	matches = [item for item in streams if isinstance(item, dict) and str(item.get('stream_id') or '').isdecimal() and needle in str(item.get('name') or 'Live channel').casefold()]
	page = min(int(state['page']), max(0, (len(matches) - 1) // PAGE_SIZE))
	return matches, page, matches[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]


def _cached_guide(account, wanted, max_age=livetv._GUIDE_FRESH_SECONDS):
	try:
		with open(livetv._guide_cache_path(), encoding='utf-8') as source: cache = json.load(source)
		key = hashlib.sha256(livetv._guide_url(account).encode('utf-8')).hexdigest()
		if cache.get('key') != key or not 0 <= time.time() - cache['created'] < max_age or not isinstance(cache.get('programmes'), dict): return None
		covered = cache.get('wanted')
		if covered is not None and not wanted.issubset(covered): return None
		return cache['programmes']
	except (OSError, ValueError, TypeError, KeyError, AttributeError):
		return None


def _active_view(state):
	path = xbmc.getInfoLabel('Container.FolderPath')
	query = {key: values[-1] for key, values in parse_qs(urlsplit(path).query, keep_blank_values=True).items()}
	return xbmc.getCondVisibility('Window.IsActive(Videos)') and query.get('mode') == 'livetv.refine' and _view(query) == state


def _guide_background(params):
	state = _view(params)
	marker = json.dumps(state, sort_keys=True)
	monitor = xbmc.Monitor()
	for _ in range(20):
		if _active_view(state): break
		if monitor.waitForAbort(0.25): return
	else:
		if ku.get_property(GUIDE_STATE) == 'pending:' + marker: ku.set_property(GUIDE_STATE, '')
		return
	try:
		account = livetv._load()
		if not account: raise ValueError('Missing account')
		_, _, visible = _visible(account, state)
		wanted = {str(item['epg_channel_id']) for item in visible if item.get('epg_channel_id')}
		if wanted: livetv._guide_data(account, wanted)
		fresh = not wanted or _cached_guide(account, wanted) is not None
		if not fresh and _cached_guide(account, wanted, livetv._GUIDE_STALE_SECONDS) is None: raise ValueError('Guide unavailable')
	except (OSError, ValueError, TypeError, ET.ParseError, EOFError, zlib.error):
		if ku.get_property(GUIDE_STATE) == 'pending:' + marker: ku.set_property(GUIDE_STATE, 'failed:%d:%s' % (int(time.time()), marker))
		return
	if ku.get_property(GUIDE_STATE) != 'pending:' + marker: return
	ku.set_property(GUIDE_STATE, 'done:' + marker if fresh else 'failed:%d:%s' % (int(time.time()), marker))
	if _active_view(state): xbmc.executebuiltin('Container.Refresh')


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
	if action == 'guide_background': return _guide_background(params)
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
		view = _view({**state, 'page': params.get('page', 0)})
		matches, page, visible = _visible(account, view)
		wanted = {str(item['epg_channel_id']) for item in visible if item.get('epg_channel_id')}
		programmes = _cached_guide(account, wanted) if wanted else {}
		missing_guide = programmes is None
		if missing_guide: programmes = _cached_guide(account, wanted, livetv._GUIDE_STALE_SECONDS)
		programmes = programmes or {}
		now = time.time()
		if not matches: livetv._item('No channels found — clear refinements', {'mode': 'livetv.refine'})
		if page: livetv._item('Previous page', {'mode': 'livetv.refine', **state, 'page': str(page - 1)})
		for stream in visible:
			name = str(stream.get('name') or 'Live channel')
			entries = programmes.get(str(stream.get('epg_channel_id') or ''), [])
			current = next((entry for entry in entries if entry[0] <= now < entry[1]), None)
			upcoming = next((entry for entry in entries if entry[0] > now), None)
			properties = {}
			if current: properties['LiveTV.Now'] = 'Now: ' + current[2]
			if upcoming: properties['LiveTV.Next'] = 'Next: ' + livetv._guide_time(upcoming[0]) + ' ' + upcoming[2]
			if properties: livetv._item(name, livetv._play_params(str(stream['stream_id']), name), False, stream.get('stream_icon'), extra_properties=properties)
			else: livetv._item(name, livetv._play_params(str(stream['stream_id']), name), False, stream.get('stream_icon'))
		if (page + 1) * PAGE_SIZE < len(matches): livetv._item('Next page', {'mode': 'livetv.refine', **state, 'page': str(page + 1)})
	except (OSError, ValueError, TypeError):
		livetv._error('Could not load Live TV channels.')
		livetv._finish(False)
		return
	livetv._finish()
	if missing_guide:
		marker = json.dumps(view, sort_keys=True)
		status = ku.get_property(GUIDE_STATE)
		try:
			prefix, timestamp, failed_marker = status.split(':', 2)
			cooling_down = prefix == 'failed' and failed_marker == marker and 0 <= time.time() - int(timestamp) < 60
		except (ValueError, AttributeError): cooling_down = False
		if status != 'pending:' + marker and not cooling_down:
			ku.set_property(GUIDE_STATE, 'pending:' + marker)
			xbmc.executebuiltin('RunPlugin(%s)' % ku.build_url({'mode': 'livetv.refine', 'action': 'guide_background', **view}))
	if xbmc.getCondVisibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.refine) + !Control.IsVisible(523)'):
		xbmc.executebuiltin('Container.SetViewMode(523)')
