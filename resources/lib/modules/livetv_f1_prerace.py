"""Find a selected Grand Prix's pre-race and post-race coverage in programme guides."""
import json
import math
import re
import time
import uuid
from collections import Counter
from datetime import datetime
from http.client import HTTPException
from queue import Empty, SimpleQueue
from threading import Event, Thread
from urllib.parse import parse_qs, urlsplit

from modules import kodi_utils as ku, livetv_f1, livetv_sports_guide
from modules.f1_calendar import _normal
from modules.livetv_catalog import FRESH_SECONDS, catalog_lock_supported, saved_streams


_REQUEST = 'LiveTV.F1.PreRace.Request'
_RESULT = 'LiveTV.F1.PreRace.Result'
_REVISION = 'LiveTV.F1.PreRace.Revision'
_FOCUSED_EVENT = 'Container(9711).ListItem.Property(F1.EventId)'
_LOOKAHEAD = 2 * 3600
_AFTER_RACE_START = 8 * 3600
_GUIDE_RETRY = 5 * 60
_CATALOG_RETRY = 60
_CATALOG_MAX_AGE = 4 * 60
_RACE_SLOT_TOLERANCE = 15 * 60
_MAX_FEEDS = 40
_F1 = r'(?:formula (?:1|one)|f ?1(?: tv)?)'
_GP = r'(?:grand prix|gp)'
_SHOW_TITLES = {show: re.compile(r'\b' + pattern + r'\b') for show, pattern in livetv_f1._BROADCAST_SHOW_PATTERNS.items()}
_GENERIC_TITLES = {show: re.compile(r'(?:live )?(?:' + _F1 + r' )?' + pattern + r'(?: (?:19|20)\d\d)?(?: live)?') for show, pattern in livetv_f1._BROADCAST_SHOW_PATTERNS.items()}
_EXCLUDE = re.compile(r'\b(?:qualifying|practice|sprint|archive|on demand|nascar|indy ?car|moto ?gp|formula e|(?:formula|f) ?(?:[234]|1 academy))\b')
_REF = re.compile(r'(?:[A-Za-z0-9_-]{1,64}:)?[0-9]{1,32}')


def _read(key):
	try:
		raw = ku.get_property(key)
		if len(raw) <= 32 * 1024:
			data = json.loads(raw or '{}')
			if isinstance(data, dict): return data
	except (TypeError, ValueError, RecursionError): pass
	return {}


def _number(value):
	return type(value) in (int, float) and -math.inf < value < math.inf


def _race(race, now):
	from menus import livetv
	try:
		if race['session_code'] != 'RACE' or race['status'] not in ('Scheduled', 'LIVE', 'Finished') or race.get('date_only') or race.get('archive_schedule'): return None
		start = datetime.fromisoformat(race['date'].replace('Z', '+00:00'))
		if start.utcoffset() is None or now - start.timestamp() >= _AFTER_RACE_START: return None
		identity = livetv._fixture_focus_identity('f1', race)
		event_id, event = race['event_id'], race['event']
		if not identity or not all(isinstance(value, str) and 0 < len(value) <= 300 for value in (event_id, event)): return None
		names = [_normal(value) for value in [event, *(race.get('event_names') or ())[:6]]]
		aliases = [_normal(value) for value in (race.get('aliases') or ())[:12]]
		venues = {re.sub(r'\s+' + _GP + r'$', '', name) for name in names if re.search(r'\s+' + _GP + r'$', name)}
		venues.update(alias for alias in aliases if len(alias) >= 4 and any(re.search(r'\b' + re.escape(alias) + r'\s+' + _GP + r'\b', name) for name in names))
		if not venues: return None
		return {'identity': identity, 'event_id': event_id, 'start': start.timestamp(), 'year': start.year, 'venues': sorted(venues)}
	except (AttributeError, KeyError, TypeError, ValueError, OverflowError): return None


def _available(rows, now, show, can_mark_live=True, *, all_feeds=False):
	feeds = []
	for row in rows if isinstance(rows, list) else []:
		try:
			if row.get('show', 'pre') == show and _REF.fullmatch(row['id']) and isinstance(row['name'], str) and 0 < len(row['name']) <= 180 and all(_number(row[key]) for key in ('start', 'until', 'verified_until')):
				until = min(row['until'], row['verified_until'])
				if now < until and row['start'] < row['until']: feeds.append(dict(row, until=until))
		except (AttributeError, KeyError, TypeError, OverflowError): continue
	active = [feed for feed in feeds if can_mark_live and feed['start'] <= now]
	if not all_feeds: feeds = active or feeds
	if feeds: return {'start': min(feed['start'] for feed in feeds), 'status': 'LIVE' if active else 'UPCOMING', 'feeds': feeds}
	return None


def coverage(race, *, now=None, request=True, year=None, show='pre', all_feeds=False):
	"""Read provider-backed coverage and optionally ask the service to refresh it."""
	from menus import livetv
	now = time.time() if now is None else now
	selected = _race(race, now)
	if selected is None or show not in _SHOW_TITLES: return None
	selected['accounts'] = livetv._feed_accounts_fingerprint()
	if request and (year == selected['year'] or selected['event_id'] == ku.get_infolabel(_FOCUSED_EVENT)) and _read(_REQUEST) != selected:
		ku.set_property(_REQUEST, json.dumps(selected, separators=(',', ':')))
	if selected['start'] - now > _LOOKAHEAD: return None
	if show == 'pre' and (race['status'] != 'Scheduled' or now >= selected['start']): return None
	result = _read(_RESULT)
	return _available(result.get('feeds'), now, show, can_mark_live=show == 'pre' or race['status'] == 'Finished', all_feeds=all_feeds) if result.get('race') == selected else None


def event_picker_selection():
	if not ku.get_visibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.f1_event_feeds)'): return None
	try:
		query = parse_qs(urlsplit(ku.get_infolabel('Container.FolderPath')).query)
		event_ids, years = query.get('event_id', []), query.get('year', [])
		if query.get('mode') == ['livetv.f1_event_feeds'] and query.get('sport') == ['f1'] and len(event_ids) == len(years) == 1 and re.fullmatch(r'[0-9]{4}', years[0]):
			return event_ids[0], int(years[0])
	except (ValueError, TypeError): pass
	return None


def _named_race(title, venues):
	return any(re.search(r'\b' + re.escape(venue) + r'\s+' + _GP + r'\b|\b' + _GP + r'\s+(?:of\s+)?' + re.escape(venue) + r'\b', title) for venue in venues)


def _same_year(title, year):
	return all(int(value) == year for value in re.findall(r'\b(?:19|20)\d\d\b', title))


def _show_types(title):
	return [show for show, pattern in _SHOW_TITLES.items() if pattern.search(title)]


def _race_programme(slot, race):
	start, _, title = slot
	return abs(start - race['start']) <= _RACE_SLOT_TOLERANCE and _named_race(title, race['venues']) and not (_show_types(title) or _EXCLUDE.search(title) or livetv_f1.non_live_title(title)) and _same_year(title, race['year'])


def _slots(entries, race, now):
	if not isinstance(entries, list): return []
	slots = set()
	for entry in entries:
		if not isinstance(entry, list) or len(entry) < 3: continue
		start, stop, title = entry[:3]
		if _number(start) and _number(stop) and start < stop and isinstance(title, str): slots.add((start, stop, _normal(title)))
	start_of_race = race['start']
	matches = []
	for start, stop, title in sorted(slots):
		if stop <= now or not start_of_race - _LOOKAHEAD <= start < start_of_race + _AFTER_RACE_START: continue
		shows = _show_types(title)
		if len(shows) != 1 or _EXCLUDE.search(title) or livetv_f1.non_live_title(title) or not _same_year(title, race['year']): continue
		show = shows[0]
		if show == 'pre' and (start >= start_of_race or stop > start_of_race + _RACE_SLOT_TOLERANCE): continue
		if show == 'post' and start <= start_of_race: continue
		if any(other != (start, stop, title) and start < other[1] and stop > other[0] for other in slots): continue
		identified = _named_race(title, race['venues']) or (re.search(r'\b' + _F1 + r'\b', title) and any(re.search(r'\b' + re.escape(venue) + r'\b', title) for venue in race['venues']))
		if not identified and _GENERIC_TITLES[show].fullmatch(title):
			adjacent = [slot for slot in slots if slot[0] == stop] if show == 'pre' else [slot for slot in slots if slot[1] == start]
			identified = len(adjacent) == 1 and _race_programme(adjacent[0], race)
		if identified: matches.append((show, start, min(stop, start_of_race) if show == 'pre' else stop))
	return matches


def _scan(accounts, profile, race, canceled):
	from menus import livetv
	feeds = {show: [] for show in _SHOW_TITLES}
	deadline = time.monotonic() + livetv._SPORTS_SEARCH_SECONDS
	for identity, account in accounts:
		if canceled.is_set(): return []
		now = time.time()
		guide = livetv_sports_guide.read_strict(account, now)
		if not guide: continue
		programmes = {key: slots for key, entries in guide['programmes'].items() if (slots := _slots(entries, race, now))}
		if not programmes: continue
		saved = saved_streams(account, profile)
		if (not saved or now - saved[1] >= FRESH_SECONDS) and time.monotonic() < deadline and catalog_lock_supported(profile):
			try: livetv._catalog(account, 'get_live_streams', notify_stale=False, _deadline=deadline)
			except (OSError, ValueError, TypeError, HTTPException): pass
			saved = saved_streams(account, profile)
		if not saved or time.time() - saved[1] >= _CATALOG_MAX_AGE: continue
		streams, created = saved
		counts = Counter(str(stream.get('stream_id') or '') for stream in streams if isinstance(stream, dict))
		valid_until = min(created + _CATALOG_MAX_AGE, guide['created'] + livetv._GUIDE_FRESH_SECONDS)
		for stream in streams:
			if not isinstance(stream, dict): continue
			stream_id, name = str(stream.get('stream_id') or ''), stream.get('name')
			ref = stream_id if identity == 'primary' else identity + ':' + stream_id
			if counts[stream_id] != 1 or not re.fullmatch(r'[0-9]{1,32}', stream_id) or not isinstance(name, str) or not 0 < len(name) <= 180 or re.search(r'[\x00-\x1f\x7f]', name): continue
			for show, start, stop in programmes.get(str(stream.get('epg_channel_id') or ''), ()):
				if time.time() < min(stop, valid_until):
					feeds[show].append({'show': show, 'id': ref, 'name': name, 'start': start, 'until': stop, 'verified_until': valid_until})
	return [row for group in feeds.values() for row in sorted(group, key=lambda feed: feed['start'])[:_MAX_FEEDS]]


def _refresh_guide(race_start, canceled, updates):
	from modules.livetv_vpn import request_connection, wait_for_connection
	try:
		request_connection()
		if not canceled.is_set() and wait_for_connection():
			covers_until = race_start + _RACE_SLOT_TOLERANCE if time.time() + _LOOKAHEAD > race_start + _RACE_SLOT_TOLERANCE else race_start
			livetv_sports_guide.refresh(canceled, lambda: updates.put(True), covers_until=covers_until, include_f1=True)
	except (OSError, ValueError, TypeError, KeyError, AttributeError): pass
	finally: updates.put(False)


def _work(race, results, canceled, refresh_guide):
	from menus import livetv
	try:
		accounts = livetv._sports_accounts(livetv._load())
		if not accounts or canceled.is_set() or race['accounts'] != livetv._feed_accounts_fingerprint(): return
		profile = ku.translate_path(ku.profile_path)
		results.put((race, canceled, _scan(accounts, profile, race, canceled)))
		if refresh_guide and not canceled.is_set():
			updates = SimpleQueue()
			Thread(target=_refresh_guide, args=(race['start'], canceled, updates), daemon=True).start()
			while updates.get():
				if not canceled.is_set(): results.put((race, canceled, _scan(accounts, profile, race, canceled)))
	except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError): pass


class PreRaceRefresh:
	def __init__(self):
		self.results = SimpleQueue()
		self.worker = None
		self.canceled = Event()
		self.race = None
		self.cards = []
		self.refresh_event = None
		self.next_scan = self.next_guide = 0

	def close(self):
		self.canceled.set()

	def tick(self, visible, picker_event=None):
		now, clock = time.time(), time.monotonic()
		selected = _read(_REQUEST) if visible else None
		if selected:
			focused = (selected.get('event_id'), selected.get('year')) == picker_event if picker_event else selected.get('event_id') == ku.get_infolabel(_FOCUSED_EVENT)
			if not focused or not _number(selected.get('start')) or not -_AFTER_RACE_START < selected['start'] - now <= _LOOKAHEAD: selected = None
		if selected != self.race:
			self.close()
			self.canceled = Event()
			self.race = selected
			self.refresh_event = None
			self.next_scan = self.next_guide = 0
		while True:
			try: race, canceled, feeds = self.results.get_nowait()
			except Empty: break
			if race == selected and canceled is self.canceled:
				ku.set_property(_RESULT, json.dumps({'race': race, 'feeds': feeds}, ensure_ascii=False, separators=(',', ':')))
		if not visible:
			self.refresh_event = None
			return
		result = _read(_RESULT)
		feeds = result.get('feeds') if selected and result.get('race') == selected else None
		cards = []
		for show in _SHOW_TITLES:
			available = _available(feeds, now, show, all_feeds=bool(picker_event))
			if available: cards.append((selected['identity'], show, available['status'], [(row['id'], row['name'], row['start'], row['until']) for row in available['feeds']]))
		if cards != self.cards:
			self.cards = cards
			ku.set_property(_REVISION, uuid.uuid4().hex)
			self.refresh_event = picker_event
		if self.refresh_event and picker_event == self.refresh_event and ku.get_visibility('System.IdleTime(2) + !Container.IsUpdating + !System.HasActiveModalDialog + !Window.IsVisible(busydialog) + !Window.IsVisible(busydialognocancel)'):
			self.refresh_event = None
			ku.execute_builtin('Container.Refresh')
		if not selected or clock < self.next_scan or (self.worker and self.worker.is_alive()): return
		refresh_guide = clock >= self.next_guide
		if refresh_guide: self.next_guide = clock + _GUIDE_RETRY
		self.next_scan = clock + _CATALOG_RETRY
		self.worker = Thread(target=_work, args=(selected, self.results, self.canceled, refresh_guide), daemon=True)
		self.worker.start()
