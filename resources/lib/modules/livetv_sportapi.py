"""Private, bounded SportAPI live fixtures for conservative dashboard matching."""
import calendar
import hashlib
import json
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from http.client import HTTPException
from urllib.request import Request, urlopen

try:
	import fcntl
except ImportError:  # Windows Kodi
	fcntl = None
	import msvcrt

KEY_FILE = 'sportapi-key.txt'
CACHE_FILE = 'sportapi-live.json'
FRESH_SECONDS = 300
MAX_UPDATE_AGE = 900
UPCOMING_SECONDS = 2 * 3600
CACHE_VERSION = 4
STANDINGS_SECONDS = 1800
DAILY_LIMIT = 350
MONTHLY_LIMIT = 12000
MAX_RESPONSE = 8 * 1024 * 1024


def valid_key(key):
	return isinstance(key, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{8,256}', key))


def _read_key(path):
	try:
		with open(path, encoding='utf-8') as source: key = source.read(257).strip()
		return key if valid_key(key) else ''
	except (OSError, UnicodeError, ValueError): return ''


def read_key(profile, bundled=None):
	return _read_key(os.path.join(profile, KEY_FILE)) or (_read_key(bundled) if bundled else '')


def save_key(profile, key):
	if not valid_key(key): raise ValueError('Invalid API key')
	os.makedirs(profile, mode=0o700, exist_ok=True)
	fd, path = tempfile.mkstemp(prefix='.sportapi-key-', dir=profile)
	try:
		with os.fdopen(fd, 'w', encoding='utf-8') as output: output.write(key)
		os.replace(path, os.path.join(profile, KEY_FILE))
	finally:
		if os.path.exists(path): os.unlink(path)


def remove_key(profile):
	try: os.unlink(os.path.join(profile, KEY_FILE))
	except FileNotFoundError: pass


def _read(path):
	try:
		if os.path.getsize(path) > MAX_RESPONSE: return {}
		with open(path, encoding='utf-8') as source: value = json.load(source)
		return value if isinstance(value, dict) else {}
	except (OSError, ValueError, TypeError, UnicodeError, RecursionError): return {}


def _write(path, value):
	fd, temporary = tempfile.mkstemp(prefix='.sportapi-live-', dir=os.path.dirname(path))
	try:
		with os.fdopen(fd, 'w', encoding='utf-8') as output: json.dump(value, output, ensure_ascii=False, separators=(',', ':'))
		os.replace(temporary, path)
	finally:
		if os.path.exists(temporary): os.unlink(temporary)


def _calendar_date(now):
	utc = datetime.fromtimestamp(now, timezone.utc)
	march = max(day for week in calendar.monthcalendar(utc.year, 3) for day in [week[calendar.SUNDAY]] if day)
	october = max(day for week in calendar.monthcalendar(utc.year, 10) for day in [week[calendar.SUNDAY]] if day)
	start = datetime(utc.year, 3, march, 1, tzinfo=timezone.utc)
	end = datetime(utc.year, 10, october, 1, tzinfo=timezone.utc)
	return (utc + timedelta(hours=2 if start <= utc < end else 1)).date()


def _calendar_days(now):
	"""Include yesterday for live games and tomorrow when the upcoming window crosses midnight."""
	today = _calendar_date(now)
	days = [today.isoformat(), (today - timedelta(days=1)).isoformat()]
	future = _calendar_date(now + UPCOMING_SECONDS + FRESH_SECONDS)
	if future != today: days.append(future.isoformat())
	return tuple(days)


def _fetch(key, day):
	request = Request('https://sportapi.ai/api/fixtures/date/' + day, headers={'X-Api-Key': key, 'User-Agent': 'BINGIE Live TV', 'Accept': 'application/json'})
	with urlopen(request, timeout=10) as response: payload = response.read(MAX_RESPONSE + 1)
	if len(payload) > MAX_RESPONSE: raise ValueError('Fixture response too large')
	return json.loads(payload.decode('utf-8-sig'))


def _recent(updated, now):
	try:
		value = datetime.fromisoformat(updated.replace('Z', '+00:00'))
		return value.utcoffset() is not None and -60 <= now - value.timestamp() <= MAX_UPDATE_AGE
	except (AttributeError, ValueError, TypeError, OverflowError, OSError): return False


def _current(item, now, lookahead=0):
	try:
		start = datetime.fromisoformat(item['date'].replace('Z', '+00:00'))
		if start.utcoffset() is None: return False
		difference = start.timestamp() - now
		if item.get('status') == 'Scheduled': return 0 < difference <= UPCOMING_SECONDS + lookahead
		return item.get('status') == 'LIVE' and -8 * 3600 <= difference <= 300 and _recent(item.get('updated_at'), now)
	except (KeyError, AttributeError, ValueError, TypeError, OverflowError, OSError): return False


def _clean(payload, key, now):
	if not isinstance(payload, dict) or payload.get('success') is not True or not isinstance(payload.get('data'), list): raise ValueError('Invalid fixture response')
	if len(payload['data']) > 10000: raise ValueError('Too many fixtures')
	items = []
	for item in payload['data']:
		if not isinstance(item, dict) or item.get('status') not in ('LIVE', 'Scheduled'): continue
		home, away, league, date, updated = (item.get(field) for field in ('home_team', 'away_team', 'league_name', 'kickoff_utc', 'updated_at'))
		if not all(isinstance(value, str) and 0 < len(value) <= 200 and key not in value for value in (home, away, league, date)): continue
		if not isinstance(updated, str) or len(updated) > 200 or key in updated: updated = ''
		fixture = {'home': home, 'away': away, 'league': league, 'status': item['status'], 'date': date, 'updated_at': updated}
		league_id = item.get('league_id')
		if isinstance(league_id, int) and not isinstance(league_id, bool) and 0 < league_id <= 100000000: fixture['league_id'] = league_id
		for side in ('home', 'away'):
			team_id = item.get(side + '_id')
			if isinstance(team_id, int) and not isinstance(team_id, bool) and 0 < team_id <= 100000000: fixture[side + '_id'] = team_id
		if _current(fixture, now, FRESH_SECONDS): items.append(fixture)
	return items


def _counter(value):
	return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def get(profile, *, bundled=None, now=None, fetch=None):
	"""Return current live and near-term scheduled fixtures, without scores."""
	key = read_key(profile, bundled)
	if not key: return [], 'not configured'
	now = time.time() if now is None else now
	fingerprint = hashlib.sha256(key.encode()).hexdigest()
	utc = datetime.fromtimestamp(now, timezone.utc)
	day, month = utc.date().isoformat(), utc.strftime('%Y-%m')
	try:
		os.makedirs(profile, mode=0o700, exist_ok=True)
		path = os.path.join(profile, CACHE_FILE)
		fd = os.open(path + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
		with os.fdopen(fd, 'a+') as lock:
			if fcntl: fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
			else:
				lock.write('0')
				lock.flush()
				lock.seek(0)
				msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
			cache = _read(path)
			if cache.get('key') != fingerprint: cache = {k: cache[k] for k in ('day', 'count', 'month', 'month_count') if k in cache}
			elif cache.get('version') != CACHE_VERSION:
				cache = {k: cache[k] for k in ('day', 'count', 'month', 'month_count') if k in cache}
			created = cache.get('created', 0)
			if not isinstance(created, (int, float)) or isinstance(created, bool): created = 0
			if 0 <= now - created < FRESH_SECONDS and isinstance(cache.get('fixtures'), list): return [item for item in cache['fixtures'] if isinstance(item, dict) and _current(item, now)], 'updated %s' % datetime.fromtimestamp(created).astimezone().strftime('%H:%M')
			count = _counter(cache.get('count')) if cache.get('day') == day else 0
			month_count = _counter(cache.get('month_count')) if cache.get('month') == month else 0
			calendar_days = _calendar_days(now)
			requests = len(calendar_days)
			if count + requests > DAILY_LIMIT or month_count + requests > MONTHLY_LIMIT: return [], 'daily or monthly limit reached'
			attempt = cache.get('attempt', 0)
			if isinstance(attempt, (int, float)) and 0 <= now - attempt < FRESH_SECONDS: return [], 'temporarily unavailable'
			cache.update(key=fingerprint, version=CACHE_VERSION, day=day, count=count + requests, month=month, month_count=month_count + requests, attempt=now)
			_write(path, cache)
			try:
				items = []
				for calendar_day in calendar_days: items.extend(_clean((fetch or _fetch)(key, calendar_day), key, now))
			except (OSError, HTTPException, ValueError, TypeError, UnicodeError, RecursionError): return [], 'temporarily unavailable'
			cache.update(created=now, fixtures=items)
			_write(path, cache)
			return [item for item in items if _current(item, now)], 'updated %s' % datetime.fromtimestamp(now).astimezone().strftime('%H:%M')
	except (OSError, ValueError, TypeError, OverflowError, RecursionError): return [], 'temporarily unavailable'


def _fetch_standings(key, league_id):
	request = Request('https://sportapi.ai/api/standings/' + str(league_id), headers={'X-Api-Key': key, 'User-Agent': 'BINGIE Live TV', 'Accept': 'application/json'})
	with urlopen(request, timeout=10) as response: payload = response.read(MAX_RESPONSE + 1)
	if len(payload) > MAX_RESPONSE: raise ValueError('Standings response too large')
	return json.loads(payload.decode('utf-8-sig'))


def _clean_standings(payload, key):
	"""Keep only the table fields needed for display; never retain results or form."""
	if not isinstance(payload, dict) or payload.get('success') is not True or not isinstance(payload.get('data'), list) or len(payload['data']) > 1000: raise ValueError('Invalid standings')
	rows = []
	for item in payload['data']:
		if not isinstance(item, dict) or not isinstance(item.get('team'), dict): return []
		name = item['team'].get('name')
		position, played, points = (item.get(field) for field in ('position', 'played', 'points'))
		if not isinstance(name, str) or not 0 < len(name) <= 200 or key in name or any(ord(char) < 32 for char in name): return []
		if any(not isinstance(value, int) or isinstance(value, bool) for value in (position, played, points)) or not 0 < position <= 10000 or not 0 <= played <= 10000 or not -10000 <= points <= 10000: return []
		row = {'position': position, 'team': name, 'played': played, 'points': points}
		team_id = item['team'].get('id')
		if isinstance(team_id, int) and not isinstance(team_id, bool) and 0 < team_id <= 100000000: row['team_id'] = team_id
		group = item.get('group') or item.get('group_name')
		if group is not None and (not isinstance(group, str) or len(group) > 100 or key in group or any(ord(char) < 32 for char in group)): return []
		if isinstance(group, str) and 0 < len(group) <= 100 and key not in group and not any(ord(char) < 32 for char in group): row['group'] = group
		rows.append(row)
	# Repeated places imply multiple groups; never merge their rankings without labels.
	positions = [row['position'] for row in rows]
	if len(positions) != len(set(positions)):
		if any('group' not in row for row in rows): return []
		grouped_positions = [(row['group'], row['position']) for row in rows]
		if len(grouped_positions) != len(set(grouped_positions)): return []
	return sorted(rows, key=lambda row: (row.get('group', '').casefold(), row['position']))


def get_standings(profile, league_id, *, bundled=None, now=None, fetch=None):
	"""Return a cached current league table, sharing fixture request limits."""
	if not isinstance(league_id, int) or isinstance(league_id, bool) or not 0 < league_id <= 100000000: return [], 'invalid league'
	key = read_key(profile, bundled)
	if not key: return [], 'not configured'
	now = time.time() if now is None else now
	fingerprint = hashlib.sha256(key.encode()).hexdigest()
	utc = datetime.fromtimestamp(now, timezone.utc)
	day, month = utc.date().isoformat(), utc.strftime('%Y-%m')
	try:
		os.makedirs(profile, mode=0o700, exist_ok=True)
		path = os.path.join(profile, CACHE_FILE)
		fd = os.open(path + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
		with os.fdopen(fd, 'a+') as lock:
			if fcntl: fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
			else:
				lock.write('0')
				lock.flush()
				lock.seek(0)
				msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
			cache = _read(path)
			if cache.get('key') != fingerprint or cache.get('version') != CACHE_VERSION:
				cache = {k: cache[k] for k in ('day', 'count', 'month', 'month_count') if k in cache}
			tables = cache.get('standings') if isinstance(cache.get('standings'), dict) else {}
			entry = tables.get(str(league_id))
			if not isinstance(entry, dict): entry = {}
			created = entry.get('created', 0)
			if isinstance(created, (int, float)) and not isinstance(created, bool) and 0 <= now - created < STANDINGS_SECONDS and isinstance(entry.get('rows'), list):
				cached = entry['rows']
				if len(cached) <= 1000 and all(isinstance(row, dict) and isinstance(row.get('team'), str) for row in cached):
					checked = _clean_standings({'success': True, 'data': [dict(row, team={'name': row['team'], 'id': row.get('team_id')}) for row in cached]}, key)
					if len(checked) == len(cached): return checked, 'updated'
			attempt = entry.get('attempt', 0)
			if isinstance(attempt, (int, float)) and not isinstance(attempt, bool) and 0 <= now - attempt < FRESH_SECONDS: return [], 'temporarily unavailable'
			count = _counter(cache.get('count')) if cache.get('day') == day else 0
			month_count = _counter(cache.get('month_count')) if cache.get('month') == month else 0
			if count + 1 > DAILY_LIMIT or month_count + 1 > MONTHLY_LIMIT: return [], 'daily or monthly limit reached'
			if len(tables) >= 100 and str(league_id) not in tables: tables = {}
			tables[str(league_id)] = {'attempt': now}
			cache.update(key=fingerprint, version=CACHE_VERSION, day=day, count=count + 1, month=month, month_count=month_count + 1, standings=tables)
			_write(path, cache)
			try: rows = _clean_standings((fetch or _fetch_standings)(key, league_id), key)
			except (OSError, HTTPException, ValueError, TypeError, UnicodeError, RecursionError): return [], 'temporarily unavailable'
			tables[str(league_id)] = {'created': now, 'rows': rows}
			cache['standings'] = tables
			_write(path, cache)
			return rows, 'updated'
	except (OSError, ValueError, TypeError, OverflowError, RecursionError): return [], 'temporarily unavailable'
