"""Season and session listings for the Formula 1 show view."""
import json
import time
from collections import Counter
from datetime import datetime, timezone
from http.client import HTTPException
from urllib.parse import quote

import xbmc

from menus import livetv
from modules import kodi_utils as ku, livetv_f1
from modules.livetv_timezone import get_timezone


_SEASON_PROPERTY = 'BingieF1.Season'
_SCHEDULE_PROPERTY = 'BingieF1.SeasonSchedule'
_SCHEDULE_LIMIT = 256 * 1024


def _year(value):
	current = datetime.now(timezone.utc).year
	if not value: return current
	if not isinstance(value, str) or len(value) != 4 or not value.isascii() or not value.isdecimal(): raise ValueError('Invalid Formula 1 season')
	year = int(value)
	if not 1950 <= year <= current: raise ValueError('Invalid Formula 1 season')
	return year


def season(params):
	"""Choose a season without changing the selection when cancelled."""
	current = datetime.now(timezone.utc).year
	years = [str(year) for year in range(current, 1949, -1)]
	selected = ku.get_property(_SEASON_PROPERTY)
	try: preselect = years.index(selected)
	except ValueError: preselect = 0
	choice = ku.dialog.select('Formula 1 season', years, preselect=preselect)
	if choice is None or choice < 0 or choice >= len(years): return
	if years[choice] != selected:
		ku.set_property(_SEASON_PROPERTY, years[choice])
		xbmc.executebuiltin('Container.Refresh')


def _live_snapshot():
	"""Use the same fresh fixture snapshot as the live-feed picker."""
	try:
		from modules import livetv_sports_progress
		state, fixtures, _ = livetv_sports_progress.load_fixtures('f1')
		if state != 'ready' or not isinstance(fixtures, list): return []
		result = livetv_sports_progress._read(livetv_sports_progress._FIXTURE_RESULT)
		if result.get('fixtures') != fixtures or not livetv_sports_progress._current_primary_fixtures(result): return []
		return [row for row in fixtures if isinstance(row, dict)]
	except (OSError, HTTPException, ValueError, TypeError, AttributeError): return []


def _valid_schedule(fixtures):
	if not isinstance(fixtures, list) or len(fixtures) > 500: return False
	for row in fixtures:
		if not isinstance(row, dict): return False
		if any(not isinstance(row.get(key), str) or not row[key] or len(row[key]) > 300 for key in ('id', 'event_id', 'event', 'session_label', 'session_code', 'date', 'status')): return False
		if row['status'] not in ('LIVE', 'Scheduled', 'Finished'): return False
		try:
			when = datetime.fromisoformat(row['date'].replace('Z', '+00:00'))
			if when.utcoffset() is None: return False
		except (ValueError, TypeError, OverflowError): return False
	return True


def _season_schedule(year):
	"""Share a bounded, short-lived public schedule across widget invocations."""
	now = time.time()
	ttl = 30 if year == datetime.now(timezone.utc).year else 24 * 60 * 60
	revision = ku.get_property('LiveTV.Sports.Fixtures.Acknowledged') if year == datetime.now(timezone.utc).year else ''
	try:
		raw = ku.get_property(_SCHEDULE_PROPERTY)
		if len(raw) > _SCHEDULE_LIMIT: raise ValueError('Schedule cache too large')
		cached = json.loads(raw or '{}')
		created = cached.get('created')
		if cached.get('year') == year and cached.get('revision') == revision and isinstance(created, (int, float)) and not isinstance(created, bool) and 0 <= now - created < ttl and cached.get('fixtures') and _valid_schedule(cached['fixtures']): return cached['fixtures']
	except (ValueError, TypeError, AttributeError, RecursionError): pass
	fixtures = livetv_f1.get_show_schedule(year)
	if not _valid_schedule(fixtures): raise ValueError('Invalid Formula 1 schedule')
	if not fixtures: return fixtures
	try:
		encoded = json.dumps({'year': year, 'revision': revision, 'created': now, 'fixtures': fixtures}, ensure_ascii=True, separators=(',', ':'))
		if len(encoded) <= _SCHEDULE_LIMIT: ku.set_property(_SCHEDULE_PROPERTY, encoded)
	except (ValueError, TypeError, RecursionError, RuntimeError): pass
	return fixtures


def _group_order(group, year, now):
	dates = [(row, datetime.fromisoformat(row['date'].replace('Z', '+00:00'))) for row in group]
	if year == now.year:
		if any(row.get('status') == 'LIVE' for row, _ in dates): return 0, 0
		upcoming = [when.timestamp() for row, when in dates if row.get('status') == 'Scheduled' and when >= now]
		if upcoming: return 1, min(upcoming)
	return 2, -max(when.timestamp() for _, when in dates)


def _when(fixture, short=False):
	when = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00'))
	return when.astimezone(get_timezone('America/Vancouver')).strftime('%a %H:%M' if short else '%a %d %b %Y · %H:%M')


def _message(text, event_id='', year=''):
	livetv._item(text, {}, False, playable=False, extra_properties={'F1.Event': '', 'F1.EventId': event_id, 'F1.Year': str(year), 'F1.Session': text, 'F1.When': '', 'F1.ShortWhen': '', 'F1.CardWhen': '', 'F1.Status': '', 'F1.Action': '', 'F1.Description': ''})


def events(params):
	"""List one selectable card per race in the chosen season."""
	year = ''
	try:
		value = params.get('year', '')
		year = _year(value)
		if not value and not ku.get_property(_SEASON_PROPERTY): ku.set_property(_SEASON_PROPERTY, str(year))
		fixtures = _season_schedule(year)
		if not fixtures:
			_message('No Formula 1 races found for this season', year=year)
			livetv._finish()
			return
		now = datetime.now(timezone.utc)
		live = _live_snapshot() if any(row['status'] == 'LIVE' for row in fixtures) else []
		live_counts = Counter(row.get('id') for row in live)
		fixture_counts = Counter(row.get('id') for row in fixtures)
		by_event = {}
		for fixture in fixtures: by_event.setdefault(fixture['event_id'], []).append(fixture)
		for group in sorted(by_event.values(), key=lambda group: _group_order(group, year, now)):
			group = sorted(group, key=lambda row: row['date'])
			first = group[0]
			def is_confirmed_live(row):
				identity = livetv._fixture_focus_identity('f1', row)
				return bool(row['status'] == 'LIVE' and identity and fixture_counts[row['id']] == 1 and live_counts[row['id']] == 1 and any(item.get('status') == 'LIVE' and item.get('id') == row['id'] and livetv._fixture_focus_identity('f1', item) == identity for item in live))
			confirmed_live = [row for row in group if is_confirmed_live(row)]
			upcoming = [row for row in group if row['status'] == 'Scheduled' and datetime.fromisoformat(row['date'].replace('Z', '+00:00')) >= now]
			if confirmed_live: status, reference = 'LIVE', confirmed_live[0]
			elif any(row['status'] == 'LIVE' for row in group): status, reference = 'CHECKING', first
			elif upcoming: status, reference = 'UPCOMING', upcoming[0]
			elif all(row['status'] == 'Finished' and datetime.fromisoformat(row['date'].replace('Z', '+00:00')) <= now for row in group): status, reference = 'FINISHED', group[-1]
			else: status, reference = 'CHECKING', first
			card_date = datetime.fromisoformat(reference['date'].replace('Z', '+00:00')).astimezone(get_timezone('America/Vancouver')).strftime('%d %b')
			props = {'F1.Event': first['event'], 'F1.EventId': first['event_id'], 'F1.EventKey': quote(first['event_id'], safe=''), 'F1.Year': str(year), 'F1.When': _when(reference), 'F1.ShortWhen': _when(reference, short=True), 'F1.CardWhen': card_date, 'F1.Status': status}
			livetv._item(first['event'], {'mode': 'livetv.f1_show_sessions', 'year': str(year), 'event_id': first['event_id']}, True, playable=False, extra_properties=props)
		livetv._finish()
	except (OSError, HTTPException, ValueError, TypeError, KeyError, OverflowError):
		_message('Could not load races. Reopen Formula 1 to retry.', year=year)
		livetv._finish()


def sessions(params):
	"""List only the sessions for the selected race, with safe actions."""
	event_id = ''
	year = ''
	try:
		value = params.get('year', '')
		year = _year(value)
		if not value and not ku.get_property(_SEASON_PROPERTY): ku.set_property(_SEASON_PROPERTY, str(year))
		event_id = params.get('event_id', '')
		if not event_id:
			livetv._finish()
			return
		if not isinstance(event_id, str) or len(event_id) > 300: raise ValueError('Invalid Formula 1 event')
		fixtures = _season_schedule(year)
		if not fixtures:
			_message('No Formula 1 sessions found for this season', event_id, year)
			livetv._finish()
			return
		selected = [row for row in fixtures if row['event_id'] == event_id]
		if not selected:
			_message('No sessions found for this race', event_id, year)
			livetv._finish()
			return
		now = datetime.now(timezone.utc)
		live = _live_snapshot() if any(row.get('status') == 'LIVE' for row in selected) else []
		fixture_counts = Counter(row.get('id') for row in fixtures)
		identities = Counter((row.get('event_id'), row.get('id')) for row in fixtures)
		live_counts = Counter(row.get('id') for row in live)
		for fixture in sorted(selected, key=lambda row: row['date']):
			status = fixture.get('status')
			when = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00'))
			event, title = fixture['event'], fixture['session_label']
			identity = livetv._fixture_focus_identity('f1', fixture)
			query, action = {}, ''
			if status == 'Finished' and when <= now:
				if identities[(fixture['event_id'], fixture['id'])] == 1:
					status, action = 'FINISHED', 'replay'
					query = {'mode': 'livetv.f1_replay_search', 'year': str(year), 'event_id': fixture['event_id'], 'session_id': fixture['id']}
					description = 'Search for a replay of this session.'
				else:
					status, description = 'UNAVAILABLE', 'Replay search is unavailable because schedule entries are ambiguous.'
			elif status == 'Scheduled':
				status, description = 'UPCOMING', 'Replay search is available after this session finishes.'
			elif status == 'LIVE':
				status, description = 'CHECKING', 'Live feeds are unavailable while the schedule is being checked.'
				matching = [row for row in live if row.get('id') == fixture['id'] and row.get('status') == 'LIVE' and livetv._fixture_focus_identity('f1', row) == identity]
				if identity and fixture_counts[fixture['id']] == 1 and live_counts[fixture['id']] == 1 and len(matching) == 1:
					status, action = 'LIVE', 'live'
					query = {'mode': 'livetv.match_feeds', 'sport': 'f1', 'session': fixture['id'], 'fixture_identity': identity, 'display_title': event + ' — ' + title}
					description = 'Find live feeds for this session.'
			else:
				status, description = 'UNAVAILABLE', 'Session information is unavailable.'
			properties = {'F1.Event': event, 'F1.EventId': event_id, 'F1.Year': str(year), 'F1.Session': title, 'F1.When': _when(fixture), 'F1.ShortWhen': _when(fixture, short=True), 'F1.Status': status, 'F1.Action': action, 'F1.Description': description}
			livetv._item(event + ' — ' + title, query, bool(action), playable=False, extra_properties=properties)
		livetv._finish()
	except (OSError, HTTPException, ValueError, TypeError, KeyError, OverflowError):
		_message('Could not load sessions. Reopen Formula 1 to retry.', event_id if isinstance(event_id, str) else '', year)
		livetv._finish()
