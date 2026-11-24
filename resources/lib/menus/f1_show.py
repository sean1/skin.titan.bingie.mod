"""Season and session listings for the Formula 1 show view."""
import json
import time
from collections import Counter
from datetime import datetime, timezone
from http.client import HTTPException
from urllib.parse import quote

import xbmc
import xbmcgui
import xbmcplugin

from menus import livetv
from modules import kodi_utils as ku, livetv_f1
from modules.livetv_timezone import get_timezone


_SEASON_PROPERTY = 'BingieF1.Season'
_SCHEDULE_PROPERTY = 'BingieF1.SeasonSchedule.18'
_ARCHIVE_PROPERTY = 'BingieF1.EventArchive.2'
_SCHEDULE_LIMIT = 256 * 1024
_ARCHIVE_ORDER = {code: index for index, code in enumerate(('PQ', 'FP1', 'FP2', 'FP3', 'FP4', 'Q1', 'Q2', 'Q3', 'QUAL', 'TT', 'WU', 'RACE'))}
_ARCHIVE_ORDER_2003 = {code: index for index, code in enumerate(('FP1', 'Q1', 'FP2', 'FP3', 'WU', 'Q2', 'QUAL', 'RACE'))}
_BROADCAST_TITLES = {'pre': 'Pre-Race Show', 'post': 'Post-Race Show'}


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
	"""Read the saved Formula 1 fixtures without starting a request."""
	try:
		from modules import livetv_sports_progress as progress
		cached = progress._cached_fixtures('f1', livetv._feed_accounts_fingerprint(), require_fresh_source=True)
		return [row for row in cached[0] if isinstance(row, dict)] if cached is not None else []
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


def _cached_season_schedule(year):
	now = time.time()
	current = year == datetime.now(timezone.utc).year
	ttl = 30 if current else 24 * 60 * 60
	revision = ku.get_property('LiveTV.Sports.Fixtures.Acknowledged') if current else ''
	try:
		raw = ku.get_property(_SCHEDULE_PROPERTY)
		if len(raw) > _SCHEDULE_LIMIT: raise ValueError('Schedule cache too large')
		cached = json.loads(raw or '{}')
		created = cached.get('created')
		if cached.get('year') == year and cached.get('revision') == revision and isinstance(created, (int, float)) and not isinstance(created, bool) and 0 <= now - created < ttl and cached.get('fixtures') and _valid_schedule(cached['fixtures']): return cached['fixtures']
	except (ValueError, TypeError, AttributeError, RecursionError): pass
	return None


def _season_schedule(year):
	"""Share a bounded, short-lived public schedule across widget invocations."""
	cached = _cached_season_schedule(year)
	if cached is not None: return cached
	now = time.time()
	revision = ku.get_property('LiveTV.Sports.Fixtures.Acknowledged') if year == datetime.now(timezone.utc).year else ''
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
		pending = [when.timestamp() for row, when in dates if _started_pending(row, when, now)]
		if pending: return 1, -max(pending)
		upcoming = [when.timestamp() for row, when in dates if row.get('status') == 'Scheduled' and when > now]
		if upcoming: return 2, min(upcoming)
	return 3, -max(when.timestamp() for _, when in dates)


def _started_pending(fixture, when, now):
	return fixture['status'] == 'Scheduled' and when <= now


def _event_archive_sessions(year, fixtures, event_id):
	if year > 2005: return []
	selected = [row for row in fixtures if row['event_id'] == event_id]
	if not selected: return []
	fields = ('id', 'session_code', 'date', 'status', 'event', 'event_names', 'aliases', 'calendar_day', 'archive_known_session_codes')
	identity = sorted([[row.get(field) for field in fields] for row in selected], key=lambda values: values[:4])
	now, entries, prior = time.time(), {}, None
	try:
		raw = ku.get_property(_ARCHIVE_PROPERTY)
		if len(raw) <= _SCHEDULE_LIMIT:
			cached = json.loads(raw or '{}')
			if cached.get('year') == year and isinstance(cached.get('events'), dict): entries = cached['events']
			entry = entries.get(event_id, {})
			if entry.get('identity') == identity and _valid_schedule(entry.get('rows')) and all(row['event_id'] == event_id and row.get('archive_undated') is True for row in entry['rows']):
				prior = entry['rows']
				if 0 <= now - entry['created'] < 24 * 60 * 60: return prior
	except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, RecursionError): pass
	rows = livetv_f1.get_archive_event_sessions(year, fixtures, event_id)
	if rows is None: return prior or []
	if not _valid_schedule(rows) or any(row['event_id'] != event_id or row.get('archive_undated') is not True for row in rows): return prior or []
	entries = dict(list(entries.items())[-31:])
	entries[event_id] = {'identity': identity, 'created': now, 'rows': rows}
	try:
		encoded = json.dumps({'year': year, 'events': entries}, ensure_ascii=True, separators=(',', ':'))
		if len(encoded) <= _SCHEDULE_LIMIT: ku.set_property(_ARCHIVE_PROPERTY, encoded)
	except (ValueError, TypeError, RuntimeError, RecursionError): pass
	return rows


def _when(fixture, short=False):
	if fixture.get('archive_undated'): return 'Date unavailable'
	if fixture.get('date_only'):
		day = datetime.fromisoformat(fixture['calendar_day'])
		return day.strftime('%a %d %b' if short else '%a %d %b %Y')
	when = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00'))
	return when.astimezone(get_timezone('America/Vancouver')).strftime('%a %H:%M' if short else '%a %d %b %Y · %H:%M')


def event_schedule(params):
	"""Load the selected Grand Prix and its season schedule for the feed finder."""
	year = _year(params.get('year', ''))
	event_id = params.get('event_id', '')
	if not isinstance(event_id, str) or not 0 < len(event_id) <= 300: raise ValueError('Invalid Formula 1 event')
	fixtures = _season_schedule(year)
	selected = [row for row in fixtures if row['event_id'] == event_id]
	if selected:
		archive = _event_archive_sessions(year, fixtures, event_id)
		fixtures = fixtures + archive
		selected += archive
	return year, event_id, fixtures, _ordered_sessions(year, selected)


def _ordered_sessions(year, selected):
	order = _ARCHIVE_ORDER_2003 if year == 2003 else _ARCHIVE_ORDER
	use_order = year == 2003 or any(row.get('archive_undated') for row in selected)
	return sorted(selected, key=lambda row: (order.get(row['session_code'], len(order)) if use_order else 0, row['date'], order.get(row['session_code'], len(order))))


def _message(text, event_id='', year=''):
	livetv._item(text, {}, False, playable=False, extra_properties={'F1.Event': '', 'F1.EventId': event_id, 'F1.Year': str(year), 'F1.Session': text, 'F1.When': '', 'F1.ShortWhen': '', 'F1.CardWhen': '', 'F1.Status': '', 'F1.Action': '', 'F1.Description': ''})


def _broadcast_race(year, fixtures, event_id, now):
	if year != now.year: return None
	races = [row for row in fixtures if row['event_id'] == event_id and row['session_code'] == 'RACE']
	return races[0] if len(races) == 1 else None


def _broadcast_item(race, year, now, show):
	from modules import livetv_f1_prerace
	try:
		coverage = livetv_f1_prerace.coverage(race, now=now.timestamp(), show=show)
		if not coverage: return
		identity = livetv._fixture_focus_identity('f1', race)
		if not identity: return
		starts_at = datetime.fromtimestamp(coverage['start'], timezone.utc)
		status = coverage['status']
	except (OSError, HTTPException, ValueError, TypeError, KeyError, OverflowError): return
	title = _BROADCAST_TITLES[show]
	when = {'date': starts_at.isoformat()}
	description = 'Choose a feed for this show.' if status == 'LIVE' else 'See the channels scheduled to carry this show.'
	query = {'mode': 'livetv.f1_broadcast_feeds', 'show': show, 'year': str(year), 'event_id': race['event_id'], 'session_id': race['id'], 'fixture_identity': identity}
	properties = {'F1.Event': race['event'], 'F1.EventId': race['event_id'], 'F1.Year': str(year), 'F1.Session': title, 'F1.When': _when(when), 'F1.ShortWhen': _when(when, short=True),
		'F1.Status': status, 'F1.Action': 'feeds', 'F1.Description': description}
	livetv._item(race['event'] + ' — ' + title, query, True, playable=False, extra_properties=properties)


def _broadcast_choices(coverage):
	choices = {}
	for feed in coverage['feeds']:
		choices.setdefault(feed['id'], {'name': feed['name'], '_guide_match': True, '_guide_until': feed['until']})
	return list(choices.items())


def _broadcast_selection(params, show, *, legacy=False):
	from modules import livetv_f1_prerace
	try:
		now = datetime.now(timezone.utc)
		year = _year(params.get('year', ''))
		event_id, session_id, identity = params.get('event_id', ''), params.get('session_id', ''), params.get('fixture_identity', '')
		if show not in _BROADCAST_TITLES or year != now.year or not event_id or not session_id or not (legacy or identity): return None
		race = _broadcast_race(year, _season_schedule(year), event_id, now)
		if not race or race['id'] != session_id or (identity and livetv._fixture_focus_identity('f1', race) != identity): return None
		accounts = livetv._feed_accounts_fingerprint()
		if 'fixture_accounts' in params and params['fixture_accounts'] != accounts: return None
		coverage = livetv_f1_prerace.coverage(race, request=False, show=show, all_feeds=not legacy)
		if not coverage or (legacy and coverage['status'] != 'LIVE') or livetv._feed_accounts_fingerprint() != accounts: return None
		choices = _broadcast_choices(coverage)
		if choices: return race, accounts, choices
	except (OSError, HTTPException, ValueError, TypeError, KeyError, OverflowError): return None
	return None


def broadcast_feeds(params):
	show = params.get('show', '')
	if show not in ('pre', 'post'): return livetv._finish(False)
	title = _BROADCAST_TITLES[show]
	selection = _broadcast_selection(params, show)
	if selection:
		race, accounts, choices = selection
		ku.set_property('LiveTV.FeedSelection.Title', race['event'] + ' — ' + title)
		context = {'mode': f'livetv.f1_{show}race_play', 'year': params.get('year', ''), 'event_id': race['event_id'], 'session_id': race['id'],
			'fixture_identity': params['fixture_identity'], 'fixture_accounts': accounts}
		livetv._feed_items(choices, 'f1', playback_context=context)
	else:
		ku.set_property('LiveTV.FeedSelection.Title', title)
		livetv._item('No matching feeds are currently available', {}, False, playable=False)
	livetv._finish()


def event_broadcast_items(race, year, now, show):
	if not race: return 0
	from modules import livetv_f1_prerace
	identity = livetv._fixture_focus_identity('f1', race)
	if not identity: return 0
	accounts = livetv._feed_accounts_fingerprint()
	coverage = livetv_f1_prerace.coverage(race, now=now.timestamp(), year=year, show=show, all_feeds=True)
	if not coverage or accounts != livetv._feed_accounts_fingerprint(): return 0
	choices = _broadcast_choices(coverage)
	context = {'mode': f'livetv.f1_{show}race_play', 'year': str(year), 'event_id': race['event_id'], 'session_id': race['id'], 'fixture_identity': identity, 'fixture_accounts': accounts}
	livetv._feed_items(choices, 'f1', playback_context=context, label_prefix=_BROADCAST_TITLES[show])
	return len(choices)


def event_feeds(params):
	"""Show the selected Grand Prix's matching feeds and replay searches together."""
	from modules import livetv_feed_selection
	ku.set_property('LiveTV.FeedSelection.Title', 'Formula 1 feeds')
	try:
		year, event_id, fixtures, selected = event_schedule(params)
		if not selected:
			livetv._item('No feeds or replays found for this Grand Prix', {}, False, playable=False)
			livetv._finish()
			return
		ku.set_property('LiveTV.FeedSelection.Title', selected[0]['event'])
		now = datetime.now(timezone.utc)
		race = _broadcast_race(year, selected, event_id, now)
		identity = livetv_feed_selection._event_identity(fixtures, event_id)
		eligible = {row['id'] for row in livetv._f1_event_feed_fixtures(fixtures, event_id)}
		lookup = bool(identity and eligible and livetv._sports_primary())
		state, results = livetv_feed_selection.prepare(dict(params, sport='f1', year=str(year), event_id=event_id, fixture_identity=identity)) if lookup else ('', [])
		unique = {(feed['_session'], ref): valid for ref, feed, valid in results if feed.get('_session') in eligible}
		choices = [((feed['_session'], ref), feed) for ref, feed, _ in results if feed.get('_session') in eligible]
		if lookup: choices = livetv_feed_selection.order_choices(choices, by_session=True)
		matches = {}
		for (session, ref), feed in choices:
			if session in eligible: matches.setdefault(session, []).append((ref, feed))
		identities = Counter((row['event_id'], row['id']) for row in selected)
		count = 0
		for fixture in selected:
			if fixture is race: count += event_broadcast_items(race, year, now, 'pre')
			session = fixture['id']
			rows = matches.get(session, [])
			if rows:
				livetv._feed_items(rows, 'f1', {ref: unique.get((session, ref), False) for ref, _ in rows}, label_prefix=fixture['session_label'])
				count += sum(not stream.get('_unavailable') for _, stream in rows)
			elif fixture['status'] == 'Finished' and identities[(event_id, session)] == 1 and datetime.fromisoformat(fixture['date'].replace('Z', '+00:00')) <= now:
				replay = {'mode': 'livetv.f1_replay_search', 'year': str(year), 'event_id': event_id, 'session_id': session}
				livetv._item(f"{fixture['session_label']} · Search replays", replay, True, playable=False)
				count += 1
			if fixture is race: count += event_broadcast_items(race, year, now, 'post')
		guide_state = livetv_feed_selection.guide_state() if lookup else ''
		if state in ('loading', 'retry_queued', 'partial', 'partial_saved') or guide_state == 'searching': livetv._item('Finding feeds…', {}, False, playable=False)
		elif state in ('slow', 'unavailable', 'partial_unavailable', 'partial_saved_unavailable') or guide_state == 'failed':
			retry = {'mode': 'livetv.f1_event_feeds', 'sport': 'f1', 'year': str(year), 'event_id': event_id, 'retry': livetv_feed_selection.retry_request()}
			if state == 'slow': retry['early_retry'] = 'true'
			livetv._item(livetv._vpn_failure_message() or 'Feeds unavailable — select to retry', retry)
		elif not count: livetv._item('No matching feeds are currently available', {}, False, playable=False)
	except (OSError, HTTPException, ValueError, TypeError, KeyError, OverflowError):
		livetv._item('Could not load Grand Prix feeds. Reopen the card to retry.', {}, False, playable=False)
	livetv._finish()


def play_broadcast(params, show):
	ref = params.get('feed_id', '')
	legacy = not any(key in params for key in ('feed_id', 'fixture_identity', 'fixture_accounts'))
	selection = _broadcast_selection(params, show, legacy=legacy) if legacy or (isinstance(ref, str) and ref and params.get('fixture_accounts')) else None
	if selection:
		_, accounts, choices = selection
		ref = choices[0][0] if legacy else ref
		feed = dict(choices).get(ref)
		if feed:
			try:
				matches = {}
				labels = livetv._provider_labels(livetv._sports_accounts(livetv._load()))
				playback = livetv._matched_play_params(ref, feed['name'], 'f1', choices, matches, labels)
				livetv._save_feed_matches(matches, expected_accounts=accounts)
				playback['fixture_expiry'] = str(feed['_guide_until'])
				if livetv._feed_accounts_fingerprint() == accounts: return livetv.play(playback)
			except (OSError, HTTPException, ValueError, TypeError, KeyError, OverflowError): pass
	livetv._error(f'The channel for the {_BROADCAST_TITLES[show].lower()} is no longer available.')
	xbmcplugin.setResolvedUrl(livetv._handle(), False, xbmcgui.ListItem())


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
				return bool(identity and fixture_counts[row['id']] == 1 and live_counts[row['id']] == 1 and any(item.get('status') == 'LIVE' and item.get('id') == row['id'] and livetv._fixture_focus_identity('f1', item) == identity for item in live))
			reported_live = [row for row in group if row['status'] == 'LIVE']
			confirmed_live = next((row for row in reported_live if is_confirmed_live(row)), None)
			dated = [(row, datetime.fromisoformat(row['date'].replace('Z', '+00:00'))) for row in group]
			pending = [(row, when) for row, when in dated if _started_pending(row, when, now)]
			upcoming = [row for row, when in dated if row['status'] == 'Scheduled' and when > now]
			if confirmed_live: status, reference = 'LIVE', confirmed_live
			elif reported_live: status, reference = 'CHECKING', reported_live[0]
			elif pending: status, reference = 'CHECKING', max(pending, key=lambda item: item[1])[0]
			elif upcoming: status, reference = 'UPCOMING', upcoming[0]
			elif all(row['status'] == 'Finished' and when <= now for row, when in dated):
				status, reference = 'FINISHED', next((row for row in reversed(group) if not row.get('archive_undated')), group[-1])
			else: status, reference = 'CHECKING', first
			card_day = datetime.fromisoformat(reference['calendar_day']) if reference.get('date_only') else datetime.fromisoformat(reference['date'].replace('Z', '+00:00')).astimezone(get_timezone('America/Vancouver'))
			card_date = card_day.strftime('%d %b')
			props = {'F1.Event': first['event'], 'F1.EventId': first['event_id'], 'F1.EventKey': quote(first['event_id'], safe=''), 'F1.Year': str(year), 'F1.When': _when(reference), 'F1.ShortWhen': _when(reference, short=True), 'F1.CardWhen': card_date, 'F1.Status': status}
			livetv._item(first['event'], {'mode': 'livetv.f1_event_feeds', 'sport': 'f1', 'year': str(year), 'event_id': first['event_id']}, True, playable=False, extra_properties=props)
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
		archive = _event_archive_sessions(year, fixtures, event_id)
		if archive:
			selected += archive
			fixtures = fixtures + archive
		now = datetime.now(timezone.utc)
		race = _broadcast_race(year, selected, event_id, now)
		live = _live_snapshot() if any(row.get('status') == 'LIVE' for row in selected) else []
		fixture_counts = Counter(row.get('id') for row in fixtures)
		identities = Counter((row.get('event_id'), row.get('id')) for row in fixtures)
		live_counts = Counter(row.get('id') for row in live)
		for fixture in _ordered_sessions(year, selected):
			if fixture is race: _broadcast_item(race, year, now, 'pre')
			status = fixture.get('status')
			when = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00'))
			event, title = fixture['event'], fixture['session_label']
			identity = livetv._fixture_focus_identity('f1', fixture)
			query, action = {}, ''
			if status == 'Finished' and when <= now:
				if identities[(fixture['event_id'], fixture['id'])] == 1:
					status, action = ('ARCHIVE' if fixture.get('archive_schedule') else 'FINISHED'), 'replay'
					query = {'mode': 'livetv.f1_replay_search', 'year': str(year), 'event_id': fixture['event_id'], 'session_id': fixture['id']}
					description = 'Search for a replay of this session.'
				else:
					status, description = 'UNAVAILABLE', 'Replay search is unavailable because schedule entries are ambiguous.'
			elif status in ('Scheduled', 'LIVE'):
				selectable = identity and fixture_counts[fixture['id']] == 1 and not (fixture.get('date_only') or fixture.get('archive_schedule'))
				if status == 'LIVE':
					confirmed = selectable and live_counts[fixture['id']] == 1 and any(row.get('id') == fixture['id'] and row.get('status') == 'LIVE' and livetv._fixture_focus_identity('f1', row) == identity for row in live)
					status = 'LIVE' if confirmed else 'CHECKING'
				else: status = 'CHECKING' if _started_pending(fixture, when, now) else 'UPCOMING'
				if selectable:
					action = 'feeds'
					query = {'mode': 'livetv.match_feeds', 'sport': 'f1', 'session': fixture['id'], 'fixture_identity': identity, 'display_title': event + ' — ' + title}
					description = 'Find feeds for this session.'
				else: description = 'Feeds are unavailable because the session could not be identified.'
			else:
				status, description = 'UNAVAILABLE', 'Session information is unavailable.'
			properties = {'F1.Event': event, 'F1.EventId': event_id, 'F1.Year': str(year), 'F1.Session': title, 'F1.When': _when(fixture), 'F1.ShortWhen': _when(fixture, short=True), 'F1.Status': status, 'F1.Action': action, 'F1.Description': description}
			livetv._item(event + ' — ' + title, query, bool(action), playable=False, extra_properties=properties)
			if fixture is race: _broadcast_item(race, year, now, 'post')
		livetv._finish()
	except (OSError, HTTPException, ValueError, TypeError, KeyError, OverflowError):
		_message('Could not load sessions. Reopen Formula 1 to retry.', event_id if isinstance(event_id, str) else '', year)
		livetv._finish()
