"""Find and play debrid recordings of a selected Formula 1 session."""
import json
import re
import sys
from datetime import datetime, timezone

import xbmcgui
import xbmcplugin
from menus.livetv import _finish, _handle, _item
from modules import f1_debrid, kodi_utils as ku, livetv_f1


def _session(params):
	year, event_id, session_id = (params.get(key, '') for key in ('year', 'event_id', 'session_id'))
	if not isinstance(year, str) or not re.fullmatch(r'[0-9]{4}', year) or not isinstance(event_id, str) or not event_id or not isinstance(session_id, str) or not session_id: raise ValueError('Invalid session')
	fixtures = livetv_f1.get_show_schedule(int(year))
	matches = [row for row in fixtures if row['event_id'] == event_id and row['id'] == session_id]
	if len(matches) != 1 or matches[0].get('status') != 'Finished' or datetime.fromisoformat(matches[0]['date'].replace('Z', '+00:00')) > datetime.now(timezone.utc): raise ValueError('Session unavailable')
	return dict(matches[0]), fixtures


def _enrich(fixture, fixtures):
	from modules import f1_calendar
	canonical = f1_calendar.aliases_by_event(fixtures)
	event_id = fixture['event_id']
	race = canonical.get(event_id, {})
	race_name, race_round = race.get('name'), race.get('round')
	fixture['aliases'] = list(fixture.get('aliases', []))
	if race_name:
		fixture['race_name'] = race_name
		fixture['aliases'].append(race_name)
	if isinstance(race_round, int) and not isinstance(race_round, bool) and 1 <= race_round <= 100: fixture['race_round'] = race_round
	fixture['other_event_aliases'] = sorted({alias for row in fixtures if row['event_id'] != event_id for alias in row.get('aliases', [])} | {details['name'] for identity, details in canonical.items() if identity != event_id and details.get('name')})
	return fixture


def search(params):
	from modules import livetv_f1_search
	try:
		fixture, fixtures = _session(params)
		if not f1_debrid.enabled_providers():
			_item('Enable a debrid torrent service in settings to search', {}, False, playable=False)
			_finish()
			return
		fixture = _enrich(fixture, fixtures)
		search_status = []
		candidates = livetv_f1_search.search(fixture, status=search_status)
		results = f1_debrid.cached_candidates(candidates)
		if 'partial' in search_status:
			_item('Some searches failed — select to retry', dict(params, mode='livetv.f1_replay_search'), True)
		for candidate in sorted(results, key=lambda row: row.get('availability') != 'cached'):
			name = str(candidate.get('name') or 'Recording')[:300]
			provider = {'realdebrid': 'Real-Debrid', 'alldebrid': 'AllDebrid', 'torbox': 'TorBox'}.get(candidate.get('debrid'), '')
			if not provider: continue
			availability = 'Cached' if candidate.get('availability') == 'cached' else 'Availability unconfirmed'
			data = {key: candidate.get(key, '') for key in ('name', 'hash', 'magnet', 'debrid')}
			pack = candidate.get('pack', '')
			if pack in ('weekend', 'season'):
				data.update({'pack': pack, 'year': params['year'], 'event_id': params['event_id'], 'session_id': params['session_id']})
				name = ('Weekend pack' if pack == 'weekend' else 'Season pack') + ' · ' + name
			_item('%s · %s · %s' % (name, provider, availability), {'mode': 'livetv.f1_debrid_play', 'candidate': json.dumps(data, ensure_ascii=True)}, False)
		if not results: _item('No matching recordings found in available searches' if 'partial' in search_status else 'No matching recordings found', {}, False, playable=False)
		ku.set_category(_handle(), fixture['event'] + ' · ' + fixture['session_label'])
		_finish()
	except livetv_f1_search.SearchUnavailable:
		_item('Replay search is unavailable — select to retry', dict(params, mode='livetv.f1_replay_search'), True)
		_finish()
	except (OSError, ValueError, TypeError, KeyError):
		ku.dialog.notification('Formula 1', 'Could not search for this session. Try again.')
		_finish(False)


def play(params):
	files = []
	candidate = None
	pack = ''
	resolved = False
	try:
		value = params.get('candidate', '')
		if not isinstance(value, str) or len(value) > 8192: raise ValueError('Invalid recording')
		candidate = json.loads(value)
		if not isinstance(candidate, dict) or candidate.get('debrid') not in f1_debrid.enabled_providers() or not re.fullmatch(r'[a-fA-F0-9]{40}', candidate.get('hash', '')) or not str(candidate.get('magnet', '')).startswith('magnet:?'): raise ValueError('Invalid recording')
		pack = candidate.get('pack', '')
		if pack:
			if pack not in ('weekend', 'season'): raise ValueError('Invalid pack')
			from modules import f1_pack_files, livetv_f1_search
			fixture, fixtures = _session(candidate)
			fixture = _enrich(fixture, fixtures)
			if livetv_f1_search.pack_type(candidate.get('name', ''), fixture) != pack: raise ValueError('Pack does not match session')
		ku.show_busy_dialog()
		try: files = f1_debrid.prepare(candidate)
		finally: ku.hide_busy_dialog()
		if not files:
			ku.dialog.notification('Formula 1', 'This recording is unavailable from the selected service.')
			return
		choices = f1_pack_files.matching_files(files, fixture, pack) if pack else files
		if pack: choices += f1_pack_files.unconfirmed_race_files(files, fixture, pack)
		if not choices:
			ku.dialog.notification('Formula 1', 'No matching session found in this pack.')
			return
		index = 0
		if len(choices) > 1 or any(item.get('_f1_unconfirmed') for item in choices):
			index = ku.dialog.select('Choose a recording', [('Race unconfirmed · ' if item.get('_f1_unconfirmed') else '') + str(item.get('filename') or 'Video') for item in choices])
			if not isinstance(index, int) or index < 0 or index >= len(choices): return
		selected = choices[index]
		pending_files, files = files, []
		url = f1_debrid.resolve(candidate, pending_files, selected, force_cleanup_on_failure=True) if pack else f1_debrid.resolve(candidate, pending_files, selected)
		if not url:
			ku.dialog.notification('Formula 1', 'Could not open this recording.')
			return
		xbmcplugin.setResolvedUrl(_handle(), True, xbmcgui.ListItem(path=url))
		resolved = True
	except Exception:
		ku.dialog.notification('Formula 1', 'Could not open this recording.')
	finally:
		if files and candidate: f1_debrid.discard(candidate, files, force=True) if pack else f1_debrid.discard(candidate, files)
		if not resolved: xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem())
