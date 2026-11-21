"""Keep a rolling, device-local history of ordinary Live TV use."""
import hashlib
import json
import math
import os
import re
import sqlite3
import time
import unicodedata
from collections import Counter
from contextlib import closing

from modules import kodi_utils as ku


WINDOW_DAYS = 30
_WINDOW_SECONDS = WINDOW_DAYS * 24 * 60 * 60
_DATABASE = 'livetv-provider-health.db'
_SCOPES = ('provider', 'shared', 'unknown')
_KINDS = ('playback', 'refresh', 'vpn', 'relay')
_VPN_REASONS = {'ready': '', 'start_failed': 'VPN connection failed', 'interrupted': 'VPN health check failed', 'recovered': 'VPN connection restored'}
_KEY = re.compile(r'[0-9a-f]{64}\Z')
_SCHEMA = '''CREATE TABLE IF NOT EXISTS events (
	occurred REAL NOT NULL, provider TEXT NOT NULL, kind TEXT NOT NULL, reason TEXT NOT NULL, scope TEXT NOT NULL, channel TEXT NOT NULL,
	recovered INTEGER NOT NULL, outcome TEXT NOT NULL, started INTEGER NOT NULL, startup REAL, watched REAL NOT NULL, buffered REAL NOT NULL
)'''


def provider_key(identity, account):
	if not isinstance(identity, str) or not isinstance(account, dict): return ''
	server, username = account.get('server'), account.get('username')
	if not isinstance(server, str) or not isinstance(username, str) or not server or not username: return ''
	if isinstance(account.get('id'), str) and account['id']: identity = account['id']
	value = json.dumps([identity, server.strip().rstrip('/'), username], separators=(',', ':'))
	return hashlib.sha256(value.encode('utf-8')).hexdigest()


def safe_label(value, limit=120):
	if not isinstance(value, str) or re.search(r'://|%3a|[?&][\w-]+=|/r/[A-Za-z0-9_-]{32,}', value, re.I): return ''
	value = ''.join(' ' if unicodedata.category(char).startswith('C') or char in '[]' else char for char in value)
	return ' '.join(value.split())[:limit]


def _seconds(value):
	return value if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 else 0


def _path():
	return os.path.join(ku.translate_path(ku.profile_path), _DATABASE)


def _record(provider, kind, reason, scope, channel, recovered, outcome='', started=False, startup=None, watched=0, buffered=0):
	provider = provider if isinstance(provider, str) and _KEY.fullmatch(provider) else ''
	if scope not in _SCOPES or (scope == 'provider' and not provider): scope = 'unknown'
	try:
		path = _path()
		os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
		os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
		now = time.time()
		with closing(sqlite3.connect(path, timeout=0.25)) as db, db:
			db.execute(_SCHEMA)
			db.execute('CREATE INDEX IF NOT EXISTS events_occurred ON events(occurred)')
			db.execute('DELETE FROM events WHERE occurred < ?', (now - _WINDOW_SECONDS,))
			db.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (now, provider, kind, safe_label(reason), scope, safe_label(channel), bool(recovered), safe_label(outcome), bool(started), startup, watched, buffered))
	except (OSError, sqlite3.Error): pass


def record_issue(kind, reason, *, scope='unknown', provider_id='', channel='', recovered=False):
	if kind in _KINDS and safe_label(reason): _record(provider_id, kind, reason, scope, channel, recovered)


def record_playback(provider_id, *, started, startup_seconds=None, watched_seconds=0, buffer_seconds=0, outcome, reason='', scope='unknown', channel='', recovered=False):
	if not started and outcome != 'start_failed': return
	startup = _seconds(startup_seconds) if startup_seconds is not None else None
	_record(provider_id, 'playback', reason, scope, channel, recovered, outcome, started, startup, _seconds(watched_seconds), _seconds(buffer_seconds))


def record_vpn(outcome, *, reason='', seconds=None, kind='vpn'):
	if outcome not in _VPN_REASONS or kind not in ('vpn', 'relay'): return
	detail = '' if outcome == 'ready' else safe_label(reason) or _VPN_REASONS[outcome]
	elapsed = _seconds(seconds) if seconds is not None else None
	_record('', kind, detail, 'shared', '', outcome == 'recovered', outcome, startup=elapsed)


def recent():
	try:
		path = _path()
		if not os.path.exists(path): return []
		now = time.time()
		with closing(sqlite3.connect(path, timeout=0.25)) as db:
			db.row_factory = sqlite3.Row
			return db.execute('SELECT * FROM events WHERE occurred BETWEEN ? AND ? ORDER BY occurred DESC, rowid DESC', (now - _WINDOW_SECONDS, now)).fetchall()
	except (OSError, sqlite3.Error): return None


def issue_reason(event):
	if event['reason']: return event['reason']
	if event['outcome'] == 'start_failed': return 'Playback failed to start'
	if event['outcome'] == 'dropped': return 'Playback error after starting'
	if event['buffered'] >= 2: return 'Buffering during playback'
	return ''


def summarize(events):
	providers = {}
	for event in events:
		provider = event['provider']
		if not provider: continue
		stats = providers.setdefault(provider, {'attempts': 0, 'starts': 0, 'startup': [], 'watched': 0, 'measured': 0, 'buffered': 0, 'drops': 0, 'unclear': 0, 'issues': Counter(), 'channels': Counter(), 'recoveries': 0})
		shared = event['scope'] == 'shared'
		if event['outcome'] and (event['started'] or not shared):
			stats['attempts'] += 1
			stats['starts'] += event['started']
			if event['started'] and event['startup'] is not None: stats['startup'].append(event['startup'])
			stats['watched'] += event['watched']
			if event['watched'] and event['channel']: stats['channels'][event['channel']] += event['watched']
			if not shared:
				stats['measured'] += event['watched']
				stats['buffered'] += event['buffered']
				stats['drops'] += event['outcome'] == 'dropped'
		if not shared:
			reason = issue_reason(event)
			if reason and not event['recovered']:
				stats['issues'][reason] += 1
				stats['unclear'] += event['scope'] == 'unknown'
			stats['recoveries'] += event['kind'] == 'playback' and event['recovered']
	return providers


def summarize_vpn(events):
	stats = {'starts': 0, 'failed': 0, 'startup': [], 'dropouts': 0, 'relay_stops': 0, 'recovered': 0, 'recovery': [], 'latest_issue': None}
	for event in events:
		kind, outcome, reason, seconds = event['kind'], event['outcome'], event['reason'], event['startup']
		if kind not in ('vpn', 'relay'): continue
		if outcome == 'ready':
			stats['starts'] += 1
			if seconds is not None: stats['startup'].append(seconds)
		elif outcome == 'recovered':
			stats['recovered'] += 1
			if seconds is not None: stats['recovery'].append(seconds)
		else:
			if outcome == 'start_failed': stats['failed'] += 1
			elif outcome == 'interrupted' or reason in ('VPN health check failed', 'Relay stopped running'):
				stats['relay_stops' if kind == 'relay' else 'dropouts'] += 1
			else: continue
			if stats['latest_issue'] is None: stats['latest_issue'] = event
	return stats
