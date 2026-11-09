"""Find and play debrid recordings of a selected Formula 1 session."""
import json
import re
import sys

import xbmcgui
import xbmcplugin
from menus.livetv import _finish, _handle, _item
from modules import f1_debrid, kodi_utils as ku, livetv_f1


def search(params):
	from modules import livetv_f1_search
	try:
		year, event_id, session_id = (params.get(key, '') for key in ('year', 'event_id', 'session_id'))
		if not isinstance(year, str) or not re.fullmatch(r'[0-9]{4}', year) or not event_id or not session_id: raise ValueError('Invalid session')
		fixtures = livetv_f1.get_season_schedule(int(year))
		matches = [row for row in fixtures if row['event_id'] == event_id and row['id'] == session_id]
		if len(matches) != 1: raise ValueError('Session unavailable')
		fixture = dict(matches[0])
		fixture['other_event_aliases'] = sorted({alias for row in fixtures if row['event_id'] != event_id for alias in row.get('aliases', [])})
		if not f1_debrid.enabled_providers():
			_item('Enable a debrid torrent service in settings to search', {}, False, playable=False)
			_finish()
			return
		candidates = livetv_f1_search.search(fixture)
		results = f1_debrid.cached_candidates(candidates)
		for candidate in results:
			name = str(candidate.get('name') or 'Recording')[:300]
			provider = {'realdebrid': 'Real-Debrid', 'alldebrid': 'AllDebrid', 'torbox': 'TorBox'}.get(candidate.get('debrid'), '')
			if not provider: continue
			availability = 'Cached' if candidate.get('availability') == 'cached' else 'Availability unconfirmed'
			data = {key: candidate.get(key, '') for key in ('name', 'hash', 'magnet', 'debrid')}
			_item('%s · %s · %s' % (name, provider, availability), {'mode': 'livetv.f1_debrid_play', 'candidate': json.dumps(data, ensure_ascii=True)}, False)
		if not results: _item('No matching recordings found', {}, False, playable=False)
		ku.set_category(_handle(), fixture['event'] + ' · ' + fixture['session_label'])
		_finish()
	except livetv_f1_search.SearchUnavailable:
		_item('Replay search is unavailable — select to retry', dict(params), True)
		_finish()
	except (OSError, ValueError, TypeError, KeyError):
		ku.dialog.notification('Formula 1', 'Could not search for this session. Try again.')
		_finish(False)


def play(params):
	files = []
	candidate = None
	resolved = False
	try:
		value = params.get('candidate', '')
		if not isinstance(value, str) or len(value) > 8192: raise ValueError('Invalid recording')
		candidate = json.loads(value)
		if not isinstance(candidate, dict) or candidate.get('debrid') not in f1_debrid.enabled_providers() or not re.fullmatch(r'[a-fA-F0-9]{40}', candidate.get('hash', '')) or not str(candidate.get('magnet', '')).startswith('magnet:?'): raise ValueError('Invalid recording')
		ku.show_busy_dialog()
		try: files = f1_debrid.prepare(candidate)
		finally: ku.hide_busy_dialog()
		if not files:
			ku.dialog.notification('Formula 1', 'This recording is unavailable from the selected service.')
			return
		index = 0
		if len(files) > 1:
			index = ku.dialog.select('Choose a recording', [str(item.get('filename') or 'Video') for item in files])
			if index < 0: return
		pending_files, files = files, []
		url = f1_debrid.resolve(candidate, pending_files, pending_files[index])
		if not url:
			ku.dialog.notification('Formula 1', 'Could not open this recording.')
			return
		xbmcplugin.setResolvedUrl(_handle(), True, xbmcgui.ListItem(path=url))
		resolved = True
	except Exception:
		ku.dialog.notification('Formula 1', 'Could not open this recording.')
	finally:
		if files and candidate: f1_debrid.discard(candidate, files)
		if not resolved: xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem())
