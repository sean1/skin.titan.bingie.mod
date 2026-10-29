"""Private, bounded API-Football live fixture lookup and conservative matching."""
import hashlib
import json
import os
import re
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from http.client import HTTPException
from urllib.request import Request, urlopen

try:
	import fcntl
except ImportError:  # Windows Kodi
	fcntl = None
	import msvcrt

FRESH_SECONDS = 300
DAILY_LIMIT = 80
MAX_RESPONSE = 4 * 1024 * 1024
KEY_FILE = 'api-football-key.txt'
CACHE_FILE = 'api-football-live.json'
LIVE_STATUSES = {'1H', 'HT', '2H', 'ET', 'BT', 'P', 'LIVE'}


def valid_key(key):
	return isinstance(key, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{8,256}', key))


def _read_key_file(path):
	try:
		with open(path, encoding='utf-8') as source: key = source.read(257).strip()
		return key if valid_key(key) else ''
	except (OSError, UnicodeError, ValueError): return ''


def read_key(profile, bundled=None):
	return _read_key_file(os.path.join(profile, KEY_FILE)) or (_read_key_file(bundled) if bundled else '')


def save_key(profile, key):
	if not valid_key(key): raise ValueError('Invalid API key')
	os.makedirs(profile, mode=0o700, exist_ok=True)
	fd, path = tempfile.mkstemp(prefix='.api-football-key-', dir=profile)
	try:
		with os.fdopen(fd, 'w', encoding='utf-8') as output: output.write(key)
		os.replace(path, os.path.join(profile, KEY_FILE))
	finally:
		if os.path.exists(path): os.unlink(path)


def remove_key(profile):
	try: os.unlink(os.path.join(profile, KEY_FILE))
	except FileNotFoundError: pass


def _write(path, value):
	fd, temporary = tempfile.mkstemp(prefix='.api-football-live-', dir=os.path.dirname(path))
	try:
		with os.fdopen(fd, 'w', encoding='utf-8') as output: json.dump(value, output, ensure_ascii=False, separators=(',', ':'))
		os.replace(temporary, path)
	finally:
		if os.path.exists(temporary): os.unlink(temporary)


def _read(path):
	try:
		if os.path.getsize(path) > MAX_RESPONSE: return {}
		with open(path, encoding='utf-8') as source: value = json.load(source)
		return value if isinstance(value, dict) else {}
	except (OSError, ValueError, TypeError): return {}


def _fetch(key):
	request = Request('https://v3.football.api-sports.io/fixtures?live=all', headers={'x-apisports-key': key, 'User-Agent': 'BINGIE Live TV'})
	with urlopen(request, timeout=10) as response: payload = response.read(MAX_RESPONSE + 1)
	if len(payload) > MAX_RESPONSE: raise ValueError('Fixture response too large')
	return json.loads(payload.decode('utf-8-sig'))


def _clean(payload, key):
	if not isinstance(payload, dict) or payload.get('errors') or not isinstance(payload.get('response'), list): raise ValueError('Invalid fixture response')
	if len(payload['response']) > 10000: raise ValueError('Too many fixtures')
	items = []
	for item in payload['response']:
		if not isinstance(item, dict): continue
		try:
			fixture, league, teams = item['fixture'], item['league'], item['teams']
			home, away, name = teams['home']['name'], teams['away']['name'], league['name']
			status = fixture['status']['short']
			if status not in LIVE_STATUSES or not all(isinstance(value, str) and 0 < len(value) <= 200 and key not in value for value in (home, away, name)): continue
			items.append({'home': home, 'away': away, 'league': name, 'status': status, 'date': str(fixture.get('date') or '')[:40]})
		except (KeyError, TypeError, AttributeError): continue
	return items


def get(profile, *, bundled=None, now=None, fetch=None):
	"""Return fresh verified fixtures and status; fail closed without hiding channels."""
	key = read_key(profile, bundled)
	if not key: return [], 'not configured'
	now = time.time() if now is None else now
	fingerprint = hashlib.sha256(key.encode()).hexdigest()
	day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
	try:
		os.makedirs(profile, mode=0o700, exist_ok=True)
		path = os.path.join(profile, CACHE_FILE)
		lock_path = path + '.lock'
		fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
		with os.fdopen(fd, 'a+') as lock:
			if fcntl: fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
			else:
				lock.write('0')
				lock.flush()
				lock.seek(0)
				msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
			cache = _read(path)
			if cache.get('key') != fingerprint:
				cache = {key: cache[key] for key in ('day', 'count') if key in cache}
			created = cache.get('created', 0)
			if not isinstance(created, (int, float)) or isinstance(created, bool): created = 0
			age = now - created
			if 0 <= age < FRESH_SECONDS and isinstance(cache.get('fixtures'), list): return cache['fixtures'], 'updated %s' % datetime.fromtimestamp(created).astimezone().strftime('%H:%M')
			count = cache.get('count', 0) if cache.get('day') == day else 0
			if not isinstance(count, int) or isinstance(count, bool) or count < 0: count = 0
			if count >= DAILY_LIMIT: return [], 'daily limit reached'
			attempt = cache.get('attempt', 0)
			if isinstance(attempt, (int, float)) and 0 <= now - attempt < FRESH_SECONDS: return [], 'temporarily unavailable'
			cache.update(key=fingerprint, day=day, count=count + 1, attempt=now)
			_write(path, cache)
			try: items = _clean((fetch or _fetch)(key), key)
			except (OSError, HTTPException, ValueError, TypeError, UnicodeError): return [], 'temporarily unavailable'
			cache.update(created=now, fixtures=items)
			_write(path, cache)
			return items, 'updated %s' % datetime.fromtimestamp(now).astimezone().strftime('%H:%M')
	except (OSError, ValueError, TypeError, OverflowError): return [], 'temporarily unavailable'


def _team(name):
	"""Normalize harmless punctuation and club suffixes without erasing team identity."""
	name = unicodedata.normalize('NFKD', name.casefold())
	name = ''.join(c for c in name if not unicodedata.combining(c))
	name = re.sub(r'\bu[\s-]+(\d{1,2})\b', r'u\1', name)
	name = re.sub(r"\bwomen['’]s\b", 'womens', name)
	words = re.findall(r'[a-z0-9]+', name)
	women = any(word in ('w', 'wfc', 'women', 'womens', 'ladies') for word in words)
	youth = {word for word in words if re.fullmatch(r'u\d{1,2}', word)}
	words = [word for word in words if word not in ('fc', 'sc', 'cf', 'afc', 'w', 'wfc', 'women', 'womens', 'ladies') and not re.fullmatch(r'u\d{1,2}', word)]
	return ' '.join(words), women, youth


def _team_alias(provider, fixture):
	"""Recognize known provider names without merging different clubs or reserve sides."""
	return (provider, fixture) in {('thionville', 'thionville lusitanos')}


def match(event, scope, fixtures):
	"""Return a league only for one unambiguous, identity-preserving live fixture."""
	if len(event) != 2: return None
	provider = [_team(name) for name in event]
	if not all(item[0] for item in provider): return None
	p_women = any(item[1] for item in provider)
	p_youth = set().union(*(item[2] for item in provider))
	matches = []
	for fixture in fixtures:
		try:
			home, away = _team(fixture['home']), _team(fixture['away'])
			home_exact = provider[0][0] == home[0]
			away_exact = provider[1][0] == away[0]
			if not ((home_exact and (away_exact or _team_alias(provider[1][0], away[0]))) or (away_exact and _team_alias(provider[0][0], home[0]))): continue
			league = fixture['league']
			league_identity = _team(league)
			if p_women != (home[1] or away[1] or league_identity[1]) or p_youth != (home[2] | away[2] | league_identity[2]): continue
			if scope:
				normal_scope = re.sub(r'\s+', ' ', scope.casefold())
				# Only named scope that is plainly incompatible rejects a match; dates are compared to fixture UTC date.
				parts = normal_scope.split('|')
				date_pattern = r'(?:\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b|\b\d{1,2}[-/.]\d{1,2}(?:[-/.]\d{2,4})?\b|\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b)'
				dates = [part for part in parts if re.search(date_pattern, part)]
				if dates and any(not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) or value != fixture.get('date', '')[:10] for value in dates): continue
				names = [part for part in parts if part not in dates]
				if names and not any(part in league.casefold() or league.casefold() in part for part in names): continue
			matches.append(fixture)
		except (KeyError, TypeError, AttributeError): continue
	return matches[0] if len(matches) == 1 else None
