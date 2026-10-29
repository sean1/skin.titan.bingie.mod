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


def _calendar_days(now):
	"""SportAPI dates use CET/CEST; include yesterday for games crossing midnight."""
	utc = datetime.fromtimestamp(now, timezone.utc)
	march = max(day for week in calendar.monthcalendar(utc.year, 3) for day in [week[calendar.SUNDAY]] if day)
	october = max(day for week in calendar.monthcalendar(utc.year, 10) for day in [week[calendar.SUNDAY]] if day)
	start = datetime(utc.year, 3, march, 1, tzinfo=timezone.utc)
	end = datetime(utc.year, 10, october, 1, tzinfo=timezone.utc)
	local = utc + timedelta(hours=2 if start <= utc < end else 1)
	return (local.date().isoformat(), (local.date() - timedelta(days=1)).isoformat())


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


def _clean(payload, key, now):
	if not isinstance(payload, dict) or payload.get('success') is not True or not isinstance(payload.get('data'), list): raise ValueError('Invalid fixture response')
	if len(payload['data']) > 10000: raise ValueError('Too many fixtures')
	items = []
	for item in payload['data']:
		if not isinstance(item, dict) or item.get('status') != 'LIVE': continue
		home, away, league, date, updated = (item.get(field) for field in ('home_team', 'away_team', 'league_name', 'kickoff_utc', 'updated_at'))
		if not all(isinstance(value, str) and 0 < len(value) <= 200 and key not in value for value in (home, away, league, date, updated)): continue
		try:
			start = datetime.fromisoformat(date.replace('Z', '+00:00'))
			if start.utcoffset() is None or not -300 <= now - start.timestamp() <= 8 * 3600: continue
		except (ValueError, OverflowError, OSError): continue
		if not _recent(updated, now): continue
		items.append({'home': home, 'away': away, 'league': league, 'status': 'LIVE', 'date': date, 'updated_at': updated})
	return items


def _counter(value):
	return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def get(profile, *, bundled=None, now=None, fetch=None):
	"""Return only current, bounded, score-free live fixtures; fail closed on errors."""
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
			created = cache.get('created', 0)
			if not isinstance(created, (int, float)) or isinstance(created, bool): created = 0
			if 0 <= now - created < FRESH_SECONDS and isinstance(cache.get('fixtures'), list): return [item for item in cache['fixtures'] if isinstance(item, dict) and _recent(item.get('updated_at'), now)], 'updated %s' % datetime.fromtimestamp(created).astimezone().strftime('%H:%M')
			count = _counter(cache.get('count')) if cache.get('day') == day else 0
			month_count = _counter(cache.get('month_count')) if cache.get('month') == month else 0
			if count + 2 > DAILY_LIMIT or month_count + 2 > MONTHLY_LIMIT: return [], 'daily or monthly limit reached'
			attempt = cache.get('attempt', 0)
			if isinstance(attempt, (int, float)) and 0 <= now - attempt < FRESH_SECONDS: return [], 'temporarily unavailable'
			cache.update(key=fingerprint, day=day, count=count + 2, month=month, month_count=month_count + 2, attempt=now)
			_write(path, cache)
			try:
				items = []
				for calendar_day in _calendar_days(now): items.extend(_clean((fetch or _fetch)(key, calendar_day), key, now))
			except (OSError, HTTPException, ValueError, TypeError, UnicodeError, RecursionError): return [], 'temporarily unavailable'
			cache.update(created=now, fixtures=items)
			_write(path, cache)
			return items, 'updated %s' % datetime.fromtimestamp(now).astimezone().strftime('%H:%M')
	except (OSError, ValueError, TypeError, OverflowError, RecursionError): return [], 'temporarily unavailable'
