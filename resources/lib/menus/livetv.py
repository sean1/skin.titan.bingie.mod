"""Live TV using an Xtream-compatible provider and Kodi's existing video player."""
import json
import gzip
import hashlib
import io
import math
import os
import queue
import re
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime, timezone
from functools import lru_cache
from http.client import HTTPException
from urllib.parse import quote, urlencode, urlsplit

import xbmc
import xbmcgui
import xbmcplugin
from modules import kodi_utils as ku
from modules.livetv_catalog import get as _catalog_get
from modules.livetv_catalog import _icon as _catalog_icon, _redact as _catalog_redact
from modules.livetv_vpn_relay import open_url as _vpn_open, register as _vpn_register
from modules.livetv_vpn import failure_message as _vpn_failure_message
from modules import livetv_fixtures as _fixture_service
from modules import livetv_sportapi as _sportapi_service
from modules import livetv_soccer_logos as _soccer_logos
from modules import livetv_sports as _sports
try:
	from modules import livetv_feed_speed as _feed_speed
except ImportError:
	_feed_speed = None
from modules import livetv_american_standings as _american_standings
from modules import livetv_american_fixtures as _american_fixtures
from modules import livetv_tennis_fixtures as _tennis_fixtures_service
from modules import livetv_f1 as _f1
from modules import livetv_canadian_championship as _canadian_championship
from modules.livetv_timezone import get_timezone

_GUIDE_DOWNLOAD_LIMIT = 512 * 1024 * 1024
_GUIDE_XML_LIMIT = 768 * 1024 * 1024
_GUIDE_FRESH_SECONDS = 2 * 60 * 60
_GUIDE_MATCH_SLOTS = threading.BoundedSemaphore(2)
_CATALOG_PREFETCH_SLOTS = threading.BoundedSemaphore(4)
_API_RESPONSE_LIMIT = 128 * 1024 * 1024
_SEARCH_RESULT_LIMIT = 300
_SEARCH_GUIDE_LIMIT = 5000
_FAILOVER_PROPERTY = 'BingieLiveTVFailover'
_FEED_CACHE_PROPERTY = 'BingieLiveTVFeedMatches'
_FEED_CACHE_SECONDS = 15 * 60
_FEED_CACHE_SLOTS = 1024
_FEED_CACHE_PROBES = 8
_FAILOVER_LIMIT = 8
_HOME_MAX_CHANNELS = 500
_CHANNEL_PAGE_SIZE = 100
_home_state_cache = None


def _soccer_event(name):
	"""Recognize the provider's explicitly live, event-specific soccer labels."""
	if not isinstance(name, str) or _sports.non_live_title(name): return None
	parts = [part.strip() for part in name.split('|')]
	if len(parts) < 3 or parts[0].casefold() != 'live': return None
	if not re.search(r'\b(?:soccer|football)\b', ' '.join(parts[2:]), re.I): return None
	if re.search(r'\b(?:end|ended|upcoming|replay|delayed)\b', ' '.join(parts[2:]), re.I): return None
	match = re.fullmatch(r"([\w .&'’\-]{2,80}?)\s+(?:vs\.?|v\.?)\s+([\w .&'’\-]{2,80}?)", parts[1], re.I)
	if not match: return None
	teams = tuple(' '.join(team.casefold().split()) for team in match.groups())
	if not all(re.search(r'[^\W\d_]', team) for team in teams) or teams[0] == teams[1]: return None
	return teams


def _fixture_scope(name):
	"""Keep explicitly dated or named competitions separate from other feeds."""
	metadata = ' | '.join(name.split('|')[2:]).casefold()
	date_pattern = (r'\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{1,2}\s+'
		r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:\s+\d{4})?)\b')
	competition_pattern = (r'\b(?:uefa\s+champions\s+league|champions\s+league|europa\s+league|conference\s+league|premier\s+league|carabao\s+cup|fa\s+cup|'
		r'la\s*liga|serie\s+a|ligue\s+1|bundesliga|major\s+league\s+soccer|world\s+cup)\b')
	dates = re.findall(date_pattern, metadata)
	competitions = ['la liga' if re.fullmatch(r'la\s*liga', value) else value for value in re.findall(competition_pattern, metadata)]
	return '|'.join(sorted(set(' '.join(value.split()) for value in dates + competitions)))


def _play_params(stream_id, name, sport=''):
	params = {'mode': 'livetv.play', 'id': stream_id}
	if sport in ('soccer', 'hockey', 'football', 'basketball', 'tennis', 'f1', 'vietnamese'):
		params.update({'name': name, 'sport': sport})
	elif _soccer_event(name): params['name'] = name
	return params


def _matched_play_params(stream_id, name, sport, feeds, cache):
	params = _play_params(stream_id, name, sport)
	if not sport or len(feeds) < 2 or len(cache) >= 500: return params
	deadlines = {ref: feed.get('_guide_until') for ref, feed in feeds if feed.get('_guide_match')}
	try:
		if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= time.time() for value in deadlines.values()): return params
	except OverflowError: return params
	token = uuid.uuid4().hex
	cache[token] = {'sport': sport, 'feeds': [[ref, hashlib.sha256(str(feed.get('name') or '').encode('utf-8')).hexdigest()] for ref, feed in feeds[:100]]}
	if deadlines: cache[token]['guide_expiries'] = {ref: deadlines[ref] for ref, _ in feeds[:100] if ref in deadlines}
	params['feeds_token'] = token
	return params


def _unique_match(groups, stream_id, name):
	return sum(1 for feeds in groups.values() if stream_id in feeds and feeds[stream_id].get('name') == name) == 1


def _sports_feed_url(params, fixture, groups):
	from modules import livetv_feed_selection
	token = livetv_feed_selection.handoff(params, fixture, groups)
	return ku.build_url({**params, **({'handoff': token} if token else {})})


def _fixture_focus_identity(sport, fixture):
	"""Identify the same scheduled event across status and list-order changes."""
	try:
		start = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00'))
		if start.utcoffset() is None: return ''
		start = start.astimezone(timezone.utc).isoformat()
		if sport == 'f1': parts = [sport, str(fixture['id']), start]
		elif sport == 'soccer': parts = [sport, fixture['home'], fixture['away'], fixture['league'], start]
		else: parts = [sport, fixture['home']['name'], fixture['away']['name'], fixture['league'], start]
		if not all(isinstance(value, str) and value.strip() for value in parts): return ''
		return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
	except (AttributeError, KeyError, TypeError, ValueError, OverflowError): return ''


def _feed_accounts_fingerprint():
	accounts = _sports_accounts(_load())
	return hashlib.sha256(json.dumps([(identity, _account_fingerprint(account)) for identity, account in accounts], separators=(',', ':')).encode('utf-8')).hexdigest()


def _save_feed_matches(cache):
	created, accounts = time.time(), _feed_accounts_fingerprint()
	for token, match in cache.items():
		try: start = int(token, 16) % _FEED_CACHE_SLOTS
		except (TypeError, ValueError): continue
		for offset in range(_FEED_CACHE_PROBES):
			key = '%s.%d' % (_FEED_CACHE_PROPERTY, (start + offset) % _FEED_CACHE_SLOTS)
			try:
				existing = json.loads(ku.get_property(key) or '{}')
				age = created - existing['created']
				available = existing.get('token') == token or existing.get('accounts') != accounts or not 0 <= age < _FEED_CACHE_SECONDS
			except (KeyError, TypeError, ValueError, AttributeError): available = True
			if available:
				ku.set_property(key, json.dumps({'token': token, 'created': created, 'accounts': accounts, 'match': match}))
				break


def _cached_failover(params):
	try:
		token = params.get('feeds_token', '')
		if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{32}', token): return
		start = int(token, 16) % _FEED_CACHE_SLOTS
		data = None
		for offset in range(_FEED_CACHE_PROBES):
			try: candidate = json.loads(ku.get_property('%s.%d' % (_FEED_CACHE_PROPERTY, (start + offset) % _FEED_CACHE_SLOTS)) or '{}')
			except (TypeError, ValueError): continue
			if isinstance(candidate, dict) and candidate.get('token') == token:
				data = candidate
				break
		if data is None: return
		age = time.time() - data['created']
		if not 0 <= age < _FEED_CACHE_SECONDS: return
		if data['accounts'] != _feed_accounts_fingerprint(): return
		match = data['match']
		if match['sport'] != params.get('sport'): return
		if 'expires' in match:
			expires = match['expires']
			if not isinstance(expires, (int, float)) or isinstance(expires, bool) or not math.isfinite(expires) or expires <= time.time(): return
		feeds = match['feeds']
		selected = params.get('id', '')
		name_hash = hashlib.sha256(str(params.get('name', '')).encode('utf-8')).hexdigest()
		if [selected, name_hash] not in feeds: return
		deadlines = match.get('guide_expiries', {})
		if not isinstance(deadlines, dict) or any(ref not in [item[0] for item in feeds] for ref in deadlines): return
		if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) for value in deadlines.values()): return
		if selected in deadlines and deadlines[selected] <= time.time(): return
		ordered = [selected] + [ref for ref, _ in feeds if ref != selected and (ref not in deadlines or deadlines[ref] > time.time())]
		if match['sport'] == 'vietnamese':
			provider = lambda ref: ref.split(':', 1)[0] if ':' in ref else 'primary'
			seen = {provider(selected)}
			other_providers, remaining = [], []
			for ref in ordered[1:]:
				identity = provider(ref)
				if identity not in seen:
					seen.add(identity)
					other_providers.append(ref)
				else: remaining.append(ref)
			ordered = [selected] + other_providers + remaining
		if len(ordered) < 2: return
		result = {'id': selected, 'alternatives': ordered[1:_FAILOVER_LIMIT + 1], 'feeds': [{'id': ref, 'name': 'Feed %d' % (index + 1)} for index, ref in enumerate(ordered)], 'token': uuid.uuid4().hex}
		for feed in result['feeds']:
			if feed['id'] in deadlines: feed['expires'] = deadlines[feed['id']]
		if 'expires' in match: result['expires'] = expires
		return result
	except (KeyError, TypeError, ValueError, AttributeError, OverflowError): return


def _prepare_failover(account, stream_id, name, sport='', defer=False):
	if ':' in stream_id or sport in ('soccer', 'hockey', 'football', 'basketball', 'tennis', 'f1'):
		numeric_id = stream_id.partition(':')[2] if ':' in stream_id else stream_id
		if not numeric_id.isdecimal(): return
		try:
			groups, _ = _sports_catalog_groups(_load(), sport or 'soccer')
			matches = [feeds for feeds in groups.values() if stream_id in feeds and feeds[stream_id].get('name') == name]
			if len(matches) != 1: return
			feeds = matches[0]
			ordered = [stream_id] + _speed_order([ref for ref in feeds if ref != stream_id], _speed_context())
			if len(ordered) > 1:
				labels = [{'id': ref, 'name': 'Feed %d' % (index + 1)} for index, ref in enumerate(ordered)]
				data = {'id': stream_id, 'alternatives': ordered[1:_FAILOVER_LIMIT + 1], 'feeds': labels, 'token': uuid.uuid4().hex}
				if defer: return data
				xbmcgui.Window(10000).setProperty(_FAILOVER_PROPERTY, json.dumps(data))
		except (OSError, HTTPException, ValueError, TypeError): pass
		return
	event = _soccer_event(name) if not sport else None
	if not event and sport not in ('hockey', 'football', 'basketball'): return
	scope = _fixture_scope(name) if event else ''
	try:
		streams = _catalog(account, 'get_live_streams')
		if not isinstance(streams, list): return
		if not any(isinstance(stream, dict) and str(stream.get('stream_id') or '') == stream_id and _soccer_event(stream.get('name')) == event and _fixture_scope(stream['name']) == scope for stream in streams): return
		alternatives = []
		secrets = (account.get('username', ''), account.get('password', ''))
		feeds = [{'id': stream_id, 'name': _catalog_redact(name, secrets)}]
		seen = {stream_id}
		for stream in streams:
			if not isinstance(stream, dict): continue
			candidate = str(stream.get('stream_id') or '')
			if not candidate.isdecimal() or candidate in seen: continue
			if not sport and (_soccer_event(stream.get('name')) != event or _fixture_scope(stream['name']) != scope): continue
			seen.add(candidate)
			if len(alternatives) < _FAILOVER_LIMIT: alternatives.append(candidate)
			feeds.append({'id': candidate, 'name': _catalog_redact(str(stream.get('name') or 'Live channel'), secrets)})
		if alternatives:
			data = {'id': stream_id, 'alternatives': alternatives, 'feeds': feeds, 'token': uuid.uuid4().hex}
			if defer: return data
			xbmcgui.Window(10000).setProperty(_FAILOVER_PROPERTY, json.dumps(data))
	except (OSError, HTTPException, ValueError, TypeError):
		pass


def _sports_accounts(primary):
	try:
		from modules.livetv_accounts import load_accounts
		accounts = load_accounts(primary)
		primary_id = primary.get('id') if primary else None
		return [(identity, account) for identity, account in accounts if identity == 'primary' or identity != primary_id]
	except ModuleNotFoundError:
		return [('primary', primary)] if primary else []


def _sports_primary():
	accounts = _sports_accounts(_load())
	return accounts[0][1] if accounts else None


def _account_for_feed(provider_id):
	return next((account for identity, account in _sports_accounts(_load()) if identity == provider_id), None)


def _sports_guide_groups(account, streams, sport, fixtures, direct, now):
	"""Match current programmes from a fresh, provider-specific guide cache."""
	live = set()
	for fixture in fixtures:
		try:
			if not isinstance(fixture, dict) or _schedule_state(fixture, now)[0] != 'Live': continue
			if sport == 'f1': key = fixture['id']
			elif sport == 'soccer': key = _soccer_fixture_key(fixture)
			else: key = (tuple(' '.join(fixture[side]['name'].casefold().split()) for side in ('home', 'away')), str(fixture.get('league') or ''))
			live.add(key)
		except (KeyError, TypeError, AttributeError): continue
	if not live: return {}
	try:
		key = hashlib.sha256(_guide_url(account).encode('utf-8')).hexdigest()
		paths = [_sports_guide_cache_path(account, sport != 'soccer'), _guide_cache_path()]
	except (OSError, ValueError, TypeError, AttributeError, KeyError): return {}
	programmes = None
	for path in paths:
		try:
			if not 0 < os.path.getsize(path) <= 16 * 1024 * 1024: continue
			with open(path, encoding='utf-8') as source: cache = json.load(source)
			created = cache.get('created')
			if cache.get('key') != key or not isinstance(created, (int, float)) or isinstance(created, bool) or not 0 <= now - created < _GUIDE_FRESH_SECONDS: continue
			if not isinstance(cache.get('programmes'), dict): continue
			programmes = cache['programmes']
			break
		except (OSError, ValueError, TypeError, AttributeError, KeyError, RecursionError): continue
	if programmes is None: return {}
	by_id = {}
	for stream in streams:
		stream_id = str(stream.get('stream_id') or '')
		if stream_id.isdecimal(): by_id.setdefault(stream_id, []).append(stream)
	named_ids = {stream_id for feeds in direct.values() for stream_id in feeds}
	candidates = []
	for stream_id, options in by_id.items():
		if len(options) != 1 or stream_id in named_ids: continue
		stream = options[0]
		epg_id = str(stream.get('epg_channel_id') or '')
		if not epg_id: continue
		entries = programmes.get(epg_id)
		if not isinstance(entries, list): continue
		current = [entry for entry in entries if isinstance(entry, list) and len(entry) == 3 and isinstance(entry[0], (int, float)) and not isinstance(entry[0], bool) and isinstance(entry[1], (int, float)) and not isinstance(entry[1], bool) and isinstance(entry[2], str) and entry[0] <= now < entry[1]]
		if len(current) != 1: continue
		if _sports.non_live_title(current[0][2]): continue
		future = [entry[0] for entry in entries if isinstance(entry, list) and len(entry) == 3 and isinstance(entry[0], (int, float)) and not isinstance(entry[0], bool)
			and isinstance(entry[1], (int, float)) and not isinstance(entry[1], bool) and isinstance(entry[2], str) and now < entry[0] < entry[1]]
		candidates.append(dict(stream, name=current[0][2], _guide_until=min([current[0][1], created + _GUIDE_FRESH_SECONDS] + future)))
	matched = _f1.fixture_groups(candidates, fixtures, live_only=True) if sport == 'f1' else _soccer_fixture_groups(candidates, fixtures, live_only=True) if sport == 'soccer' else _sports.fixture_groups(candidates, sport, fixtures, live_only=True)
	return {event: {stream_id: dict(by_id[stream_id][0], _guide_match=True, _guide_until=feed['_guide_until']) for stream_id, feed in feeds.items()} for event, feeds in matched.items() if event in live}


def _sports_catalog_groups(primary, sport, reload='', on_status=None, fixtures=None, status_sink=None, notify_stale=True, on_partial=None, overlap_catalogs=False, catalog_fetch=None):
	from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
	legacy = _load()
	accounts = _sports_accounts(legacy)
	primary_id = accounts[0][0] if accounts else None
	status_values = []
	def fetch(entry):
		identity, account = entry
		account_status = []
		started, outcome = time.monotonic(), 'unavailable'
		try:
			kwargs = {'force_refresh': reload == 'refresh'}
			if not notify_stale: kwargs['notify_stale'] = False
			if on_status: kwargs['on_status'] = lambda created, stale: account_status.append((created, stale))
			streams = catalog_fetch(identity, account, **kwargs) if catalog_fetch else _catalog(account, 'get_live_streams', **kwargs)
			if isinstance(streams, list): outcome = 'ok'
			return identity, streams if isinstance(streams, list) else [], isinstance(streams, list), account_status
		except (OSError, HTTPException, ValueError, TypeError): return identity, [], False, account_status
		finally: _sports.record_timing('catalog_fetch', time.monotonic() - started, sport, outcome)
	prefetch_pool, prefetched = None, None
	prefetch_canceled = threading.Event()
	def prefetch(entry):
		while not prefetch_canceled.is_set():
			if _CATALOG_PREFETCH_SLOTS.acquire(timeout=0.1):
				try:
					if not prefetch_canceled.is_set(): return fetch(entry)
				finally: _CATALOG_PREFETCH_SLOTS.release()
		return entry[0], [], False, []
	if overlap_catalogs and fixtures is None and accounts:
		prefetch_pool = ThreadPoolExecutor(max_workers=min(2, len(accounts)))
		prefetched = {prefetch_pool.submit(prefetch, entry) for entry in accounts}
	try:
		if sport == 'soccer' and fixtures is None:
			fixtures, _, _, _ = _live_soccer_fixtures(include_upcoming=True)
		elif sport == 'f1' and fixtures is None:
			fixtures = _f1.get_schedule()
		elif sport in ('hockey', 'football', 'basketball', 'tennis') and fixtures is None:
			try:
				profile = ku.translate_path(getattr(ku, 'profile_path', ''))
				fixtures = _tennis_fixtures_service.get_schedule(cache_dir=profile or None) if sport == 'tennis' else _american_fixtures.get_schedule(sport, cache_dir=profile or None)
				if not status_sink: ku.set_property('LiveTV.Soccer.FixtureStatus', 'Live fixtures updated')
			except (OSError, HTTPException, ValueError, TypeError):
				fixtures = []
				if not status_sink: ku.set_property('LiveTV.Soccer.FixtureStatus', 'Live fixtures unavailable')
	except BaseException:
		if prefetch_pool:
			prefetch_canceled.set()
			prefetch_pool.shutdown(wait=False, cancel_futures=True)
		raise
	if not fixtures:
		if prefetch_pool:
			prefetch_canceled.set()
			prefetch_pool.shutdown(wait=False, cancel_futures=True)
		if status_sink: status_sink('')
		else: ku.set_property('LiveTV.Sports.CatalogUnavailable', '')
		if on_status: ku.set_property('LiveTV.Soccer.Status', '')
		return {}, []
	if not any(isinstance(fixture, dict) and fixture.get('status') in _fixture_service.LIVE_STATUSES for fixture in fixtures):
		if prefetch_pool:
			prefetch_canceled.set()
			prefetch_pool.shutdown(wait=False, cancel_futures=True)
		if status_sink: status_sink('')
		else: ku.set_property('LiveTV.Sports.CatalogUnavailable', '')
		return {}, []
	groups, all_streams, failed = {}, [], False
	account_map = dict(accounts)
	account_groups, account_streams = {}, {}
	def collect():
		merged = {}
		for identity, _ in accounts:
			for key, feeds in account_groups.get(identity, {}).items():
				for stream_id, stream in feeds.items(): merged.setdefault(key, {})[identity + ':' + stream_id if stream.get('_provider') else stream_id] = dict(stream)
		return merged
	def publish():
		if on_partial:
			status = [(min(created for created, _ in status_values), any(stale for _, stale in status_values))] if status_values else []
			on_partial(collect(), 'true' if failed else '', status)
	def timed_match(phase, match):
		started, outcome = time.monotonic(), 'unavailable'
		try:
			result = match()
			outcome = 'ok'
			return result
		finally: _sports.record_timing(phase, time.monotonic() - started, sport, outcome)
	def direct_groups(streams):
		if sport == 'f1': return _f1.fixture_groups(streams, fixtures, live_only=True)
		if sport in ('hockey', 'football', 'basketball', 'tennis'): return _sports.fixture_groups(streams, sport, fixtures, live_only=True)
		return _soccer_fixture_groups(streams, fixtures, live_only=True)
	def guide_groups(account, streams, local):
		with _GUIDE_MATCH_SLOTS:
			return timed_match('guide_match', lambda: _sports_guide_groups(account, streams, sport, fixtures, local, time.time()))
	with prefetch_pool or ThreadPoolExecutor(max_workers=min(4, max(1, len(accounts)))) as pool, ThreadPoolExecutor(max_workers=1) as guide_pool:
		catalog_futures = prefetched if prefetched is not None else {pool.submit(fetch, entry) for entry in accounts}
		guide_futures = {}
		while catalog_futures or guide_futures:
			wait(catalog_futures | guide_futures.keys(), return_when=FIRST_COMPLETED)
			ready_catalogs = [future for future in catalog_futures if future.done()]
			if not ready_catalogs:
				future = next(future for future in guide_futures if future.done())
				identity = guide_futures.pop(future)
				guide = future.result()
				for event, feeds in guide.items(): account_groups[identity].setdefault(event, {}).update(feeds)
				if guide: publish()
				continue
			for future in ready_catalogs:
				catalog_futures.remove(future)
				identity, streams, available, account_status = future.result()
				if not available: failed = True
				status_values.extend(account_status)
				streams = [dict(stream, _provider=identity, _primary=identity == primary_id) if len(accounts) > 1 or not legacy else stream for stream in streams if isinstance(stream, dict)]
				account_streams[identity] = streams
				local = timed_match('direct_match', lambda: direct_groups(streams))
				account_groups[identity] = local
				if local: publish()
				guide_futures[guide_pool.submit(guide_groups, account_map[identity], streams, local)] = identity
	groups = collect()
	all_streams = [stream for identity, _ in accounts for stream in account_streams.get(identity, [])]
	if status_sink: status_sink('true' if failed else '')
	else: ku.set_property('LiveTV.Sports.CatalogUnavailable', 'true' if failed else '')
	if on_status and status_values: on_status(min(created for created, _ in status_values), any(stale for _, stale in status_values))
	return groups, all_streams


def _speed_context():
	if not _feed_speed: return {}, {}
	try: return dict(_sports_accounts(_load())), _feed_speed.snapshot()
	except (OSError, ValueError, TypeError, AttributeError): return {}, {}


def _speed_order(refs, context):
	if not _feed_speed: return refs
	accounts, values = context
	keys = {ref: _feed_speed.key(ref, accounts.get(ref.split(':', 1)[0] if ':' in ref else 'primary')) for ref in refs}
	return _feed_speed.order(refs, keys, values)


def _sports_ref(stream):
	identity = stream.get('_provider')
	return identity + ':' + str(stream['stream_id']) if identity else str(stream['stream_id'])


def _credentials_path():
	return os.path.join(ku.translate_path(ku.profile_path), 'livetv-account.json')


def _bundled_credentials_path():
	return os.path.join(ku.translate_path(ku.addon_path), 'resources', 'private', 'livetv-account.json')


def _read_account(path):
	try:
		with open(path, encoding='utf-8') as account:
			data = json.load(account)
		if all(isinstance(data.get(key), str) and data[key] for key in ('server', 'username', 'password')):
			return data
	except (OSError, ValueError, AttributeError):
		pass
	return None


def _account_fingerprint(account):
	account = {key: value for key, value in account.items() if key not in ('id', 'enabled', 'label', 'expires_at')}
	return hashlib.sha256(json.dumps(account, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _load():
	path = _bundled_credentials_path()
	if not os.path.exists(path):
		account = _read_account(_credentials_path())
		if account: return account
		accounts = _sports_accounts(None)
		return accounts[0][1] if accounts else None
	account = _read_account(path)
	if not account:
		accounts = _sports_accounts(None)
		account = accounts[0][1] if accounts else None
	if not account: return None
	profile = _read_account(_credentials_path())
	if profile and profile.get('guide_override_base') == _account_fingerprint(account):
		account['epg_url'] = profile['epg_url'] if isinstance(profile.get('epg_url'), str) else account.get('epg_url', '')
	return account


def _save(data):
	path = _credentials_path()
	os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
	fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
	try:
		os.fchmod(fd, 0o600)
		with os.fdopen(fd, 'w', encoding='utf-8') as account:
			fd = None
			json.dump(data, account)
	finally:
		if fd is not None: os.close(fd)


def _handle():
	return int(sys.argv[1])


def _home_account_key(account):
	identity = {key: account.get(key, '') for key in ('server', 'username', 'password')}
	return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _home_path(account):
	return os.path.join(ku.translate_path(ku.profile_path), 'livetv-home-%s.json' % _home_account_key(account))


def _home_clean(account, value):
	if not isinstance(value, dict): return None
	stream_id, name = value.get('id'), value.get('name')
	if not isinstance(stream_id, str) or not stream_id.isascii() or not stream_id.isdecimal() or len(stream_id) > 32: return None
	if not isinstance(name, str) or not name.strip() or len(name) > 500: return None
	secrets = (account.get('username', ''), account.get('password', ''))
	if _catalog_redact(name, secrets) != name or _catalog_redact(stream_id, secrets) != stream_id: return None
	icon = _catalog_icon(value.get('icon'), secrets)
	return {'id': stream_id, 'name': name, 'icon': icon or ''}


def _home_read(account):
	try:
		path = _home_path(account)
		if os.path.getsize(path) > 1024 * 1024: return []
		with open(path, encoding='utf-8') as source: data = json.load(source)
		if not isinstance(data, list): return []
		items, seen = [], set()
		for value in data[:_HOME_MAX_CHANNELS]:
			item = _home_clean(account, value)
			if item and item['id'] not in seen:
				items.append(item)
				seen.add(item['id'])
		return items
	except (OSError, ValueError, TypeError):
		return []


def _home_write(account, items):
	path = _home_path(account)
	os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
	fd, temporary = tempfile.mkstemp(prefix='.livetv-home-', dir=os.path.dirname(path))
	try:
		with os.fdopen(fd, 'w', encoding='utf-8') as target:
			json.dump(items, target, ensure_ascii=False, separators=(',', ':'))
		os.replace(temporary, path)
	finally:
		if os.path.exists(temporary): os.unlink(temporary)


def _home_state():
	global _home_state_cache
	if _home_state_cache is None:
		account = _load()
		_home_state_cache = (account, _home_read(account) if account else [])
	return _home_state_cache


def _home_context(label, params, icon):
	account, items = _home_state()
	if not account: return None
	stream_id = str(params.get('id') or '')
	name = str(params.get('name') or label)
	if name.endswith(' (play channel)'): name = name[:-len(' (play channel)')]
	item = _home_clean(account, {'id': stream_id, 'name': name, 'icon': icon})
	if not item: return None
	saved = any(value['id'] == stream_id for value in items)
	query = {'mode': 'livetv.home_action', 'action': 'remove' if saved else 'add', **item}
	return ('Remove from Home channels' if saved else 'Add to Home channels', 'RunPlugin(%s)' % ku.build_url(query))


def home_action(params):
	global _home_state_cache
	account = _load()
	item = _home_clean(account, params) if account else None
	choice = params.get('action')
	if not item or choice not in ('add', 'remove'):
		ku.dialog.notification('Live TV', 'Could not update Home channels.')
		return
	items = _home_read(account)
	items = [value for value in items if value['id'] != item['id']]
	if choice == 'add':
		if len(items) >= _HOME_MAX_CHANNELS:
			ku.dialog.notification('Live TV', 'Home channels is full.')
			return
		items.append(item)
	try:
		_home_write(account, items)
	except (OSError, ValueError, TypeError):
		ku.dialog.notification('Live TV', 'Could not update Home channels.')
		return
	_home_state_cache = (account, items)
	xbmcgui.Window(10000).setProperty('BingieLiveTVFavoritesRefresh', str(time.time_ns()))
	ku.dialog.notification('Live TV', 'Added to Home channels.' if choice == 'add' else 'Removed from Home channels.')
	if xbmc.getCondVisibility('Window.IsActive(Videos)'): xbmc.executebuiltin('Container.Refresh')


def home_channels(params):
	account = _load()
	if account:
		for item in _home_read(account): _item(item['name'], _play_params(item['id'], item['name']), False, item['icon'])
	_finish()


def _item(label, params, folder=True, icon=None, playable=True, extra_context=None, extra_properties=None):
	item = xbmcgui.ListItem(label=label, offscreen=True)
	for key, value in (extra_properties or {}).items(): item.setProperty(key, str(value))
	if icon:
		try:
			parts = urlsplit(icon)
			if parts.scheme.lower() in ('http', 'https'): icon = _vpn_register(icon)
			elif parts.scheme.lower() not in ('', 'special', 'resource'): icon = None
			elif not parts.scheme and icon.startswith('//'): icon = None
		except (OSError, HTTPException, ValueError, TypeError): icon = None
	if icon: item.setArt({'thumb': icon, 'icon': icon, 'poster': icon, 'landscape': icon})
	if not folder and playable: item.setProperty('IsPlayable', 'true')
	context = list(extra_context or [])
	if not folder and playable and params.get('mode') == 'livetv.play':
		home_context = _home_context(label, params, icon)
		if home_context: context.append(home_context)
	if context: item.addContextMenuItems(context)
	ku.add_item(_handle(), ku.build_url(params), item, folder)


def _finish(success=True):
	xbmcplugin.endOfDirectory(_handle(), succeeded=success, cacheToDisc=False)


def _request(account, action, **params):
	query = {'username': account['username'], 'password': account['password'], 'action': action, **params}
	url = account['server'] + '/player_api.php?' + urlencode(query)
	headers = {'User-Agent': 'BINGIE Live TV'}
	if action == 'get_live_streams': headers['Accept-Encoding'] = 'gzip'
	with _vpn_open(url, headers=headers, timeout=20) as response:
		encoding = response.headers.get('Content-Encoding', '').strip().lower() if action == 'get_live_streams' else ''
		if encoding not in ('', 'identity', 'gzip'): raise ValueError('Unsupported response encoding')
		data = response.read(_API_RESPONSE_LIMIT + 1)
		if len(data) > _API_RESPONSE_LIMIT: raise ValueError('Response too large')
	if encoding == 'gzip':
		try:
			with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed: data = compressed.read(_API_RESPONSE_LIMIT + 1)
		except (EOFError, zlib.error) as error: raise ValueError('Invalid compressed response') from error
		if len(data) > _API_RESPONSE_LIMIT: raise ValueError('Response too large')
	return json.loads(data.decode('utf-8-sig'))


def _catalog(account, action, *, force_refresh=False, on_status=None, notify_stale=True, **params):
	on_stale = (lambda: _error(_vpn_failure_message() or 'Showing saved channels; check the VPN or provider connection.')) if notify_stale else None
	return _catalog_get(account, action, _request, ku.translate_path(ku.profile_path), on_stale=on_stale, on_status=on_status, force_refresh=force_refresh, **params)


def _error(message):
	ku.dialog.notification('Live TV', message, time=max(5000, min(60000, 2000 + 200 * len(message))))


def next_feed(params):
	from modules.livetv_failover import request_manual_switch
	request_manual_switch()


def settings(params):
	current = _load() or {}
	server = ku.dialog.input('Server URL', defaultt=current.get('server', '')).strip().rstrip('/')
	if not server:
		return
	try:
		parts = urlsplit(server)
		valid = parts.scheme in ('http', 'https') and parts.hostname and not (parts.username or parts.password or parts.query or parts.fragment)
	except ValueError:
		valid = False
	if not valid:
		_error('Enter a server URL beginning with http:// or https://, without a username, password, or query string.')
		return
	username = ku.dialog.input('Username', defaultt=current.get('username', '')).strip()
	if not username:
		return
	password = ku.dialog.input('Password', option=xbmcgui.ALPHANUM_HIDE_INPUT)
	if not password:
		return
	try:
		_save({'server': server, 'username': username, 'password': password, **({'epg_url': current['epg_url']} if current.get('epg_url') else {})})
	except OSError:
		_error('Could not save the account in the Kodi profile.')
		return
	ku.dialog.notification('Live TV', 'Live TV account saved.', time=5000)


def browse(params):
	account = _load()
	if not account:
		_error('No Live TV account is available. Add a valid private account file to the BINGIE package.')
		_finish(False)
		return
	try:
		categories = _catalog(account, 'get_live_categories')
		if not isinstance(categories, list): raise ValueError('Invalid response')
		_item('Search Live TV', {'mode': 'livetv.search', 'isFolder': 'false'}, False, None, False)
		_item('Try another feed', {'mode': 'livetv.next', 'isFolder': 'false'}, False, None, False)
		_item('All channels', {'mode': 'livetv.channels'})
		for category in categories:
			if not isinstance(category, dict) or not category.get('category_id'): continue
			_item(str(category.get('category_name') or 'Other'), {'mode': 'livetv.channels', 'category': str(category['category_id'])})
	except (OSError, HTTPException, ValueError, TypeError):
		_error('Could not load Live TV categories. Check the VPN and account details, then try again.')
	_finish()


def _channel_page(streams, params):
	streams = [stream for stream in streams if isinstance(stream, dict) and str(stream.get('stream_id') or '').isdecimal()]
	try: page = max(0, int(params.get('page', 0)))
	except (ValueError, TypeError): page = 0
	page = min(page, max(0, (len(streams) - 1) // _CHANNEL_PAGE_SIZE))
	return streams[page * _CHANNEL_PAGE_SIZE:(page + 1) * _CHANNEL_PAGE_SIZE], page, (page + 1) * _CHANNEL_PAGE_SIZE < len(streams)


def channels(params):
	account = _load()
	if not account:
		_error('No Live TV account is available. Add a valid private account file to the BINGIE package.')
		_finish(False)
		return
	try:
		category = params.get('category', '')
		streams = _catalog(account, 'get_live_streams', **({'category_id': category} if category else {}))
		if not isinstance(streams, list): raise ValueError('Invalid response')
		streams, page, has_next = _channel_page(streams, params)
		page_params = {'mode': 'livetv.channels', 'category': category}
		if page: _item('Previous page', {**page_params, 'page': str(page - 1)})
		for stream in streams:
			stream_id = str(stream.get('stream_id') or '')
			name = str(stream.get('name') or 'Live channel')
			_item(name, _play_params(stream_id, name), False, stream.get('stream_icon'))
		if has_next: _item('Next page', {**page_params, 'page': str(page + 1)})
	except (OSError, HTTPException, ValueError, TypeError):
		_error('Could not load channels. Check the VPN and account details, then try again.')
		_finish(False)
		return
	_finish()


def _vietnamese_feeds(accounts):
	from concurrent.futures import ThreadPoolExecutor
	def fetch(entry):
		identity, account = entry
		try:
			streams = _catalog(account, 'get_live_streams', notify_stale=False)
			return identity, streams if isinstance(streams, list) else None
		except (OSError, HTTPException, ValueError, TypeError):
			return identity, None
	channels, failed = {}, False
	with ThreadPoolExecutor(max_workers=min(4, max(1, len(accounts)))) as pool:
		for identity, streams in pool.map(fetch, accounts):
			if streams is None:
				failed = True
				continue
			for stream in streams:
				if not isinstance(stream, dict): continue
				stream_id = str(stream.get('stream_id') or '')
				match = re.fullmatch(r'\s*VN\s*[:|]\s*VTV\s*([1-9])(?:\s+HD)?\s*', str(stream.get('name') or ''), re.I)
				if not match or not stream_id.isdecimal(): continue
				number = int(match.group(1))
				ref = stream_id if identity == 'primary' else identity + ':' + stream_id
				channels.setdefault(number, {})[ref] = stream
	return channels, failed


def vietnamese(params):
	accounts = _sports_accounts(_load())
	if not accounts:
		_error('No Live TV account is available.')
		_finish(False)
		return
	channels, failed = _vietnamese_feeds(accounts)
	feed_cache, speed = {}, _speed_context()
	for number, feeds in sorted(channels.items()):
		ordered = [(ref, feeds[ref]) for ref in _speed_order(list(feeds), speed)]
		stream_id, stream = ordered[0]
		name = str(stream.get('name') or 'VTV%d' % number)
		icon = os.path.join(ku.translate_path(ku.addon_path), 'media', 'livetv', 'vtv', '%d.png' % number)
		context = []
		if len(ordered) > 1:
			url = ku.build_url({'mode': 'livetv.vtv_feeds', 'channel': str(number)})
			context.append(('Choose another feed', 'Container.Update(%s)' % url))
		_item('VTV%d — %d %s' % (number, len(ordered), 'feed' if len(ordered) == 1 else 'feeds'), _matched_play_params(stream_id, name, 'vietnamese', ordered, feed_cache), False, icon, extra_context=context)
	_save_feed_matches(feed_cache)
	if not channels:
		_error('Could not load Vietnamese TV channels. Check the VPN and provider connection.' if failed else 'No VTV channels were found in the available providers.')
	elif failed:
		ku.dialog.notification('Live TV', 'Some provider feeds are unavailable.')
	_finish(bool(channels))
	if xbmc.getCondVisibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.vietnamese) + !Control.IsVisible(523)'):
		xbmc.executebuiltin('Container.SetViewMode(523)')


def vietnamese_feeds(params):
	number = params.get('channel', '')
	if number not in tuple(str(value) for value in range(1, 10)):
		_finish(False)
		return
	accounts = _sports_accounts(_load())
	if not accounts:
		_error('No Live TV account is available.')
		_finish(False)
		return
	channels, failed = _vietnamese_feeds(accounts)
	feeds = channels.get(int(number), {})
	choices = [(ref, feeds[ref]) for ref in _speed_order(list(feeds), _speed_context())]
	feed_cache = {}
	for index, (ref, stream) in enumerate(choices, 1):
		name = str(stream.get('name') or 'VTV' + number)
		_item('Feed %d (play channel)' % index, _matched_play_params(ref, name, 'vietnamese', choices, feed_cache), False)
	_save_feed_matches(feed_cache)
	if not choices:
		_error('Could not load matching feeds. Try again.' if failed else 'No matching feeds are currently available.')
	elif failed:
		ku.dialog.notification('Live TV', 'Some provider feeds are unavailable.')
	_finish(bool(choices))


def _guide_url(account):
	return account.get('epg_url') or account['server'] + '/xmltv.php?' + urlencode({'username': account['username'], 'password': account['password']})


def _guide_cache_path():
	return os.path.join(ku.translate_path(ku.profile_path), 'livetv-guide.json')


def _sports_guide_cache_path(account, permissive=False):
	key = hashlib.sha256((_guide_url(account) + '\0' + _account_fingerprint(account)).encode('utf-8')).hexdigest()
	return os.path.join(ku.translate_path(ku.profile_path), 'livetv-sports-guide-' + ('permissive-' if permissive else '') + key + '.json')


def _guide_timestamp(value):
	match = re.fullmatch(r'(\d{8}|\d{10}|\d{12}|\d{14})(?:\s+([+-]\d{4}|Z))?', (value or '').strip())
	if not match: return None
	digits, offset = match.groups()
	try:
		return datetime.strptime(digits + (' +0000' if not offset or offset == 'Z' else ' ' + offset), {8: '%Y%m%d', 10: '%Y%m%d%H', 12: '%Y%m%d%H%M', 14: '%Y%m%d%H%M%S'}[len(digits)] + ' %z').timestamp()
	except (ValueError, OverflowError):
		return None


def _guide_xml_chunks(source):
	"""Remove a plain external XMLTV DTD without allowing entities or fetching it."""
	size = 0
	previous = b''
	pending = b''
	seen_doctype = False
	while True:
		chunk = source.read(65536)
		if not chunk:
			if pending.upper().startswith(b'<!DOCTYPE'): raise ValueError('Incomplete XML declaration')
			if pending: yield pending
			return
		size += len(chunk)
		if size > _GUIDE_XML_LIMIT: raise ValueError('Guide too large')
		if b'\x00' in chunk: raise ValueError('Unsupported XML encoding')
		check = (previous + chunk).upper()
		if b'<!ENTITY' in check: raise ValueError('Unsupported XML declaration')
		previous = check[-16:]
		data = pending + chunk
		pending = b''
		while data:
			position = data.upper().find(b'<!DOCTYPE')
			if position < 0:
				if len(data) > 8: yield data[:-8]
				pending = data[-8:]
				break
			if position: yield data[:position]
			declaration = data[position:]
			end = declaration.find(b'>')
			if end < 0:
				if len(declaration) > 4096: raise ValueError('Unsupported XML declaration')
				pending = declaration
				break
			declaration, data = declaration[:end + 1], declaration[end + 1:]
			if seen_doctype or not re.fullmatch(rb'<!DOCTYPE\s+tv\s+SYSTEM\s+(?:"[^"<>\[\]]{1,2048}"|\'[^\'<>\[\]]{1,2048}\')\s*>', declaration, re.I):
				raise ValueError('Unsupported XML declaration')
			seen_doctype = True


def _parse_guide(source, now, wanted=None, search_term=None, sports_window=False, require_zone=True):
	parser = ET.XMLPullParser(events=('start', 'end'))
	root = None
	programmes = {}
	count = 0
	for chunk in _guide_xml_chunks(source):
		parser.feed(chunk)
		for event, node in parser.read_events():
			if root is None and event == 'start': root = node
			if event != 'end': continue
			if node.tag == 'programme':
				channel = node.get('channel', '')
				title = (node.findtext('title') or 'Programme')[:300]
				zoned = not sports_window or not require_zone or all(re.search(r'\s(?:[+-]\d{4}|Z)$', node.get(field, '')) for field in ('start', 'stop'))
				if zoned and channel and (wanted is None or channel in wanted) and (search_term is None or search_term in title.casefold()):
					start, stop = _guide_timestamp(node.get('start')), _guide_timestamp(node.get('stop'))
				else:
					start = stop = None
				if start is not None and stop is not None and stop > (now if search_term is not None or sports_window else now - 86400) and start < now + (7200 if sports_window else 3 * 86400) and stop > start:
					count += 1
					if search_term is None and count > 200000: raise ValueError('Too many programmes')
					if search_term is None or count <= _SEARCH_GUIDE_LIMIT:
						programmes.setdefault(channel, []).append([start, stop, title])
				if root is not None: root.clear()
			elif node.tag == 'channel' and root is not None: root.clear()
	parser.close()
	for entries in programmes.values(): entries.sort(key=lambda entry: entry[0])
	return programmes


def _parse_raw_guide(raw, now, wanted, search_term=None, sports_window=False, require_zone=True):
	raw.seek(0)
	magic = raw.read(2)
	raw.seek(0)
	if magic == b'\x1f\x8b':
		with gzip.GzipFile(fileobj=raw) as decoded: return _parse_guide(decoded, now, wanted, search_term, sports_window, require_zone)
	return _parse_guide(raw, now, wanted, search_term, sports_window, require_zone)


def _soccer_kickoff(fixture):
	if not fixture: return ''
	try:
		value = fixture.get('date', '')
		if not isinstance(value, str): return ''
		kickoff = datetime.fromisoformat(value.replace('Z', '+00:00'))
		if kickoff.utcoffset() is None: return ''
		return kickoff.astimezone(get_timezone('America/Vancouver')).strftime('%a %H:%M')
	except (ValueError, OverflowError, OSError, ImportError, KeyError): return ''


def _live_start(fixture):
	try:
		start = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00'))
		return start.timestamp() if start.utcoffset() is not None else float('inf')
	except (AttributeError, KeyError, TypeError, ValueError, OverflowError, OSError): return float('inf')


def _schedule_state(fixture, now):
	if fixture.get('status') in _fixture_service.LIVE_STATUSES: return 'Live', 0
	if fixture.get('status') != 'Scheduled': return '', 0
	try:
		start = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00'))
		if start.utcoffset() is None: return '', 0
		kickoff = start.timestamp()
		return ('Upcoming', kickoff) if -15 * 60 <= kickoff - now <= 24 * 3600 else ('', 0)
	except (AttributeError, KeyError, TypeError, ValueError, OverflowError, OSError): return '', 0


def _soccer_match_state(fixture, now):
	"""Classify only confirmed live fixtures and scheduled kickoffs within two hours."""
	if not fixture: return '', 0
	status = fixture.get('status')
	if status in _fixture_service.LIVE_STATUSES: return 'Live', 0
	if status != 'Scheduled': return '', 0
	try:
		start = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00'))
		if start.utcoffset() is None: return '', 0
		kickoff = start.timestamp()
		if 0 < kickoff - now <= 2 * 60 * 60: return 'Starting soon', kickoff
	except (AttributeError, KeyError, TypeError, ValueError, OverflowError, OSError): pass
	return '', 0


def _fixtures_agree(first, second):
	"""Only combine independent fixture matches when their competition and kickoff agree."""
	try:
		league_a, women_a, youth_a = _fixture_service._team(first['league'])
		league_b, women_b, youth_b = _fixture_service._team(second['league'])
		a, b = set(league_a.split()), set(league_b.split())
		if not a or not b or women_a != women_b or youth_a != youth_b or not (a <= b or b <= a): return False
		start_a = datetime.fromisoformat(first['date'].replace('Z', '+00:00'))
		start_b = datetime.fromisoformat(second['date'].replace('Z', '+00:00'))
		return start_a.utcoffset() is not None and start_b.utcoffset() is not None and start_a == start_b
	except (AttributeError, KeyError, TypeError, ValueError, OverflowError): return False


def _fixture_candidates(event, scope, fixtures):
	"""Keep an ambiguous source distinct from a source with no candidate."""
	return [item for item in fixtures if _fixture_service.match(event, scope, [item])]


_CONTEXT_PROGRAMME = re.compile(r"^\s*([^:|]{1,120}):\s*([\w .&'’\-]{2,80}?)\s+vs\.?\s+([\w .&'’\-]{2,80}?)\s+@\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+(\d{1,2})(?:[ ,]+(\d{4}))?\b", re.I)
_CONTEXT_CONFLICT = object()


def _context_team(name):
	return _fixture_service._team(name.replace('ø', 'o').replace('Ø', 'O'))


def _context_related(event, fixture):
	"""Include possible abbreviated identities as blockers, not positive matches."""
	try:
		given = [_context_team(name)[0] for name in event]
		other = [_context_team(fixture[key])[0] for key in ('home', 'away')]
		def compatible(a, b):
			if a == b: return True
			a, b = a.split(), b.split()
			if a and a[-1] in ('ii', 'b', 'reserve', 'reserves'): a = a[:-1]
			if b and b[-1] in ('ii', 'b', 'reserve', 'reserves'): b = b[:-1]
			if a == b: return True
			if _fixture_service._team_alias(' '.join(a), ' '.join(b)) or _fixture_service._team_alias(' '.join(b), ' '.join(a)): return True
			if len(a) < len(b): a, b = b, a
			return bool(len(a) == len(b) + 1 and len(a[0]) <= 3 and a[1:] == b and len(''.join(b)) >= 4)
		return all(given) and all(other) and all(compatible(a, b) for a, b in zip(given, other))
	except (KeyError, TypeError, AttributeError): return False


def _context_has_conflict(event, sources):
	"""Unqualified team names cannot disambiguate conflicting fixture categories."""
	try:
		if any(_context_team(name)[1] for name in event): return False
		groups = [[item for item in fixtures if _context_related(event, item)] for fixtures in sources]
		if any(len(group) > 1 for group in groups): return True
		items = [group[0] for group in groups if group]
		def reserve(name):
			words = _context_team(name)[0].split()
			return words[-1] if words and words[-1] in ('ii', 'b', 'reserve', 'reserves') else ''
		if any([reserve(item[key]) for key in ('home', 'away')] != [reserve(name) for name in event] for item in items): return True
		if len(items) != 2: return False
		def category(item):
			home, away = (_context_team(item[key]) for key in ('home', 'away'))
			_, women, youth = _fixture_service._team(item['league'])
			return bool(home[1] or away[1] or women), home[2] | away[2] | youth
		return category(items[0]) != category(items[1]) or not _fixtures_agree(*items)
	except (KeyError, TypeError, ValueError, AttributeError): return True


def _context_identity(names):
	return tuple((base, women, frozenset(youth)) for base, women, youth in map(_context_team, names))


def _context_programmes(streams, created, stale, now=None, events=None):
	"""Index relevant dated programmes once, only for a fresh catalogue."""
	now = time.time() if now is None else now
	if stale or not isinstance(created, (int, float)) or not 0 <= now - created <= 120: return {}
	try:
		current = datetime.fromtimestamp(created, timezone.utc).date()
		wanted = {_context_identity(event) for event in events} if events is not None else None
		programmes = {}
		for stream in streams:
			name = stream.get('name') if isinstance(stream, dict) else None
			match = _CONTEXT_PROGRAMME.match(name) if isinstance(name, str) else None
			if not match: continue
			league, home, away, month, day, year = match.groups()
			identity = _context_identity((home, away))
			if wanted is not None and identity not in wanted: continue
			try:
				date = datetime.strptime('%s %s %s' % (month, day, year or current.year), '%b %d %Y').date()
			except ValueError: continue
			if date != current: continue
			base, women, youth = _fixture_service._team(league)
			programmes.setdefault(identity, set()).add((base, women, frozenset(youth), date))
		return programmes
	except (TypeError, ValueError, AttributeError, OverflowError, OSError): return {}


def _context_fixture(event, scope, streams, sources, created, stale, now=None, index=None):
	"""Corroborate omitted gender only from a fresh, dated, exact-event programme."""
	try:
		now = time.time() if now is None else now
		given = [_context_team(name) for name in event]
		if len(given) != 2 or any(item[1] for item in given): return None
		if index is None: index = _context_programmes(streams, created, stale, now)
		programmes = index.get(_context_identity(event), set())
		if not programmes: return None
		if len(programmes) != 1: return _CONTEXT_CONFLICT
		base, women, youth, programme_day = next(iter(programmes))
		if not base: return None
		categories = []
		for fixtures in sources:
			related = [item for item in fixtures if _context_related(event, item)]
			if len(related) > 1: return _CONTEXT_CONFLICT
			if related: categories.append(related[0])
		if not categories: return None
		if len(categories) == 2 and (not _fixtures_agree(*categories) or _soccer_match_state(categories[0], now)[0] != _soccer_match_state(categories[1], now)[0]): return _CONTEXT_CONFLICT
		confirmed = None
		for item in categories:
			if item.get('status') not in _fixture_service.LIVE_STATUSES and _soccer_match_state(item, now)[0] != 'Starting soon': return _CONTEXT_CONFLICT
			home, away = (_context_team(item[key]) for key in ('home', 'away'))
			league_name, league_women, league_youth = _fixture_service._team(item['league'])
			if bool(home[1] or away[1] or league_women) != women or (home[2] | away[2] | league_youth) != set(youth): return _CONTEXT_CONFLICT
			start = datetime.fromisoformat(item['date'].replace('Z', '+00:00'))
			if start.utcoffset() is None or start.astimezone(timezone.utc).date() != programme_day: return _CONTEXT_CONFLICT
			context_league = {'league': base + (' women' if women else '') + ''.join(' ' + age for age in sorted(youth)), 'date': item['date']}
			if not _fixtures_agree(item, context_league): return _CONTEXT_CONFLICT
			if not women or [_context_team(item[key])[0] for key in ('home', 'away')] != [item[0] for item in given]: continue
			if not _fixture_service.match((event[0] + ' Women', event[1]), scope, [item]): return _CONTEXT_CONFLICT
			confirmed = item
		return confirmed
	except (KeyError, TypeError, ValueError, AttributeError, OverflowError, OSError): return _CONTEXT_CONFLICT
	return None


def search(params):
	query = (ku.dialog.input('Search Live TV: team, league or channel') or '').strip()
	if not query: return
	url = ku.build_url({'mode': 'livetv.search_results', 'query': query})
	xbmc.executebuiltin('Container.Update(%s)' % url)


def search_results(params):
	account = _load()
	if not account:
		_error('No Live TV account is available. Add a valid private account file to the BINGIE package.')
		_finish(False)
		return
	query = (params.get('query') or '').strip()
	if not query:
		_finish()
		return
	needle = query.casefold()
	try:
		streams = _catalog(account, 'get_live_streams')
		if not isinstance(streams, list): raise ValueError('Invalid response')
	except (OSError, HTTPException, ValueError, TypeError):
		_error('Could not search Live TV channels. Check the VPN and account, then try again.')
		_finish(False)
		return
	streams = [s for s in streams if isinstance(s, dict) and str(s.get('stream_id') or '').isdecimal()]
	event_feeds = {}
	channel_matches = []
	for stream in streams:
		name = str(stream.get('name') or 'Live channel')
		event = _soccer_event(name)
		if event: event_feeds.setdefault((event, _fixture_scope(name)), {})[str(stream['stream_id'])] = stream
		if needle in name.casefold(): channel_matches.append(stream)
	channel_matches.sort(key=lambda stream: (0 if str(stream.get('name') or '').casefold().startswith('live |') else 2 if str(stream.get('name') or '').casefold().startswith('end |') else 1))
	rows = []
	seen_events = set()
	for stream in channel_matches:
		name = str(stream.get('name') or 'Live channel')
		event = _soccer_event(name)
		scope = _fixture_scope(name) if event else ''
		key = (event, scope)
		feeds = event_feeds.get(key, {})
		if event and len(feeds) > 1:
			if key in seen_events: continue
			seen_events.add(key)
			rows.append((name.split('|')[1].strip() + (' — ' + scope if scope else '') + ' — %d feeds' % len(feeds), {'mode': 'livetv.match_feeds', 'home': event[0], 'away': event[1], 'scope': scope}, True, stream.get('stream_icon')))
		else:
			rows.append((name + ' (play channel)', _play_params(str(stream['stream_id']), name), False, stream.get('stream_icon')))
	for row in rows[:_SEARCH_RESULT_LIMIT]: _item(*row)
	if len(rows) > _SEARCH_RESULT_LIMIT:
		ku.dialog.notification('Live TV', 'Showing the first search results. Try a more specific name.')
	elif not channel_matches:
		ku.dialog.notification('Live TV', 'No matches found.')
	_finish()


_SOCCER_LEAGUES = (
	('Champions League', r'\b(?:uefa\s+)?champions\s+league\b'),
	('Europa League', r'\beuropa\s+league\b'),
	('Conference League', r'\bconference\s+league\b'),
	('Premier League', r'\bpremier\s+league\b'),
	('Carabao Cup', r'\bcarabao\s+cup\b'),
	('FA Cup', r'\bfa\s+cup\b'),
	('La Liga', r'\bla\s+liga\b'),
	('Serie A', r'\bserie\s+a\b'),
	('Ligue 1', r'\bligue\s+1\b'),
	('Bundesliga', r'\bbundesliga\b'),
	('Major League Soccer', r'\bmajor\s+league\s+soccer\b'),
	('World Cup', r'\bworld\s+cup\b'),
)


def _soccer_league(name):
	metadata = ' | '.join(name.split('|')[2:])
	matches = [label for label, pattern in _SOCCER_LEAGUES if re.search(pattern, metadata, re.I)]
	return matches[0] if len(matches) == 1 else 'Other / unknown'


def _tennis_fixtures(streams, today):
	fixtures = set()
	pattern = re.compile(r'^\s*([^@]+?)\s+vs\.?\s+([^@]+?)\s+@\s+([A-Za-z]{3})\s+(\d{1,2})(?:[ ,]+(\d{4}))?\b', re.I)
	for item in streams:
		if not isinstance(item, dict): continue
		name = str(item.get('name') or '')
		if not re.search(r'\b(?:tennis|atp|wta)\b', name, re.I): continue
		match = pattern.search(name)
		if not match: continue
		try:
			date = datetime.strptime('%s %s %s' % (match[3], match[4], match[5] or today.year), '%b %d %Y').date()
		except ValueError:
			continue
		if date != today: continue
		teams = tuple(' '.join(side.split(',', 1)[0].casefold().split()) for side in match.group(1, 2))
		if all(teams): fixtures.add(teams)
	return fixtures


_OTHER_SPORT_NICKNAMES = {
	'nhl': {tuple(name.split()) for name in (
		'ducks', 'bruins', 'sabres', 'flames', 'hurricanes', 'blackhawks', 'avalanche', 'blue jackets', 'stars', 'red wings', 'oilers', 'panthers', 'kings', 'wild',
		'canadiens', 'predators', 'devils', 'islanders', 'rangers', 'senators', 'flyers', 'penguins', 'sharks', 'kraken', 'blues', 'lightning', 'maple leafs', 'canucks',
		'golden knights', 'capitals', 'jets', 'mammoth',
	)},
	'nba': {tuple(name.split()) for name in (
		'hawks', 'celtics', 'nets', 'hornets', 'bulls', 'cavaliers', 'mavericks', 'nuggets', 'pistons', 'warriors', 'rockets', 'pacers', 'clippers', 'lakers',
		'grizzlies', 'heat', 'bucks', 'timberwolves', 'pelicans', 'knicks', 'thunder', 'magic', '76ers', 'suns', 'trail blazers', 'kings', 'spurs', 'raptors', 'jazz', 'wizards',
	)},
	'nfl': {tuple(name.split()) for name in (
		'cardinals', 'falcons', 'ravens', 'bills', 'panthers', 'bears', 'bengals', 'browns', 'cowboys', 'broncos', 'lions', 'packers', 'texans', 'colts', 'jaguars',
		'chiefs', 'raiders', 'chargers', 'rams', 'dolphins', 'vikings', 'patriots', 'saints', 'giants', 'jets', 'eagles', 'steelers', '49ers', 'seahawks', 'buccaneers',
		'titans', 'commanders',
	)},
}


def _other_sport_fixtures(streams, today):
	"""Find explicitly identified non-soccer events in the provider catalog."""
	fixtures = set()
	event_pattern = r'([^|()]+?)\s+(?:vs\.?|v\.?|at|@)\s+([^|()@]+?)'
	date_pattern = r'\b(\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4}|[A-Za-z]{3}\s+\d{1,2}(?:\s+\d{4})?)\b'
	for item in streams:
		if not isinstance(item, dict): continue
		name = str(item.get('name') or '')
		parts = [part.strip() for part in name.split('|')]
		match = None
		sport = ''
		date_match = None
		undated_live = False
		# Dated channel listings keep the sport, teams and date in one field.
		for part in parts:
			candidate = re.search(r'\b(NHL|NFL|NBA|NCAA\s+Football)\s*:\s*' + event_pattern + r'(?=\s+@\s+[A-Za-z]{3}\s+\d|\s*\()', part, re.I)
			if candidate:
				match, sport, date_match = candidate, candidate.group(1).casefold(), re.search(date_pattern, part[candidate.end():])
				break
		if match:
			teams_raw = match.group(2, 3)
		elif len(parts) >= 4 and parts[0].casefold() in ('live', 'next'):
			match = re.fullmatch(event_pattern, parts[1], re.I)
			sport = next((part.casefold() for part in parts[2:] if re.fullmatch(r'NHL|NFL|NBA|NCAA\s+Football|National\s+Squash\s+League', part, re.I)), '')
			if not match or not sport: continue
			teams_raw = match.group(1, 2)
			date_match = next((re.fullmatch(date_pattern, part) for part in parts[2:] if re.fullmatch(date_pattern, part)), None)
			undated_live = parts[0].casefold() == 'live' and sport == 'national squash league' and not date_match
		else: continue
		if not date_match and not undated_live: continue
		if date_match:
			value = date_match.group(1)
			try:
				if re.match(r'\d{4}-', value): date = datetime.strptime(value, '%Y-%m-%d').date()
				elif re.match(r'\d{2}-', value): date = datetime.strptime(value, '%d-%m-%Y').date()
				else:
					date = datetime.strptime(value if len(value.split()) == 3 else value + ' ' + str(today.year), '%b %d %Y').date()
					if len(value.split()) == 2 and date.month == 12 and today.month == 1: date = date.replace(year=today.year - 1)
					elif len(value.split()) == 2 and date.month == 1 and today.month == 12: date = date.replace(year=today.year + 1)
			except ValueError:
				continue
			if abs((date - today).days) > 1: continue
		teams = tuple(tuple(re.findall(r'[^\W_]+', side.casefold())) for side in teams_raw)
		if all(teams): fixtures.add((sport, teams))
	return fixtures


def _other_sport_match(event, fixtures):
	teams = tuple(tuple(re.findall(r'[^\W_]+', side)) for side in event)
	for sport, (a, b) in fixtures:
		for first, second in ((a, b), (b, a)):
			if teams == (first, second): return True
			nicknames = _OTHER_SPORT_NICKNAMES.get(sport, set())
			if all(team in nicknames and len(team) <= len(full) and full[-len(team):] == team for team, full in zip(teams, (first, second))): return True
	return False


def _soccer_groups(streams, now=None):
	groups = {}
	# Some provider PPV labels say SOCCER for tennis too. A separate dated,
	# explicitly tennis-labelled fixture can identify that contradiction.
	today = (now or datetime.now().astimezone()).date()
	tennis = _tennis_fixtures(streams, today)
	other_sports = _other_sport_fixtures(streams, today)
	for stream in streams:
		if not isinstance(stream, dict): continue
		stream_id = str(stream.get('stream_id') or '')
		name = stream.get('name')
		event = _soccer_event(name)
		if not stream_id.isdecimal() or not event: continue
		if event in tennis: continue
		# Dated soccer listings can refer to a different occurrence than the evidence.
		metadata = ' | '.join(name.split('|')[2:])
		date_pattern = (r'\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4}|'
			r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*)\b')
		if not re.search(date_pattern, metadata, re.I) and _other_sport_match(event, other_sports): continue
		key = (event, _fixture_scope(name))
		groups.setdefault(key, {}).setdefault(stream_id, stream)
	return groups


_SOCCER_COMPETITION_NAMES = {
	'english premier league', 'spanish la liga', 'la liga', 'german bundesliga', 'italian serie a', 'french ligue 1',
	'major league soccer', 'mls', 'usa mls', 'us major league soccer', 'american major league soccer',
	'uefa champions league', 'uefa europa league', 'uefa conference league', 'uefa europa conference league',
	'concacaf champions league', 'concacaf champions cup', 'concacaf champions league cup',
	'canadian premier league', 'canada premier league', 'canadian championship',
	'saudi pro league', 'saudi arabian league', 'saudi arabian pro league', 'roshn saudi league',
}
_SOCCER_COUNTRY_COMPETITIONS = {
	'england': {'premier league'}, 'spain': {'la liga', 'primera división', 'primera division'}, 'germany': {'bundesliga'},
	'italy': {'serie a'}, 'france': {'ligue 1'}, 'usa': {'major league soccer', 'mls'}, 'canada': {'canadian premier league', 'canadian championship'},
	'saudi-arabia': {'pro league', 'saudi league', 'saudi pro league'},
}


def _selected_soccer_fixture(fixture, source):
	"""Keep selected competitions and senior Canada national-team fixtures."""
	for side in ('home', 'away'):
		name = ' '.join(str(fixture.get(side) or '').casefold().split())
		if re.fullmatch(r'canada(?:\s+(?:women|womens|women\'s|wnt|w)|\s*\(w\))?', name): return True
	name = ' '.join(str(fixture.get('league') or '').casefold().split())
	if name in _SOCCER_COMPETITION_NAMES: return True
	if source == 'sportapi':
		# SportAPI identifies the English top flight as the generic "Premier League".
		return name == 'premier league' and fixture.get('league_id') == 24
	country = str(fixture.get('league_country') or '').casefold()
	if name in _SOCCER_COUNTRY_COMPETITIONS.get(country, set()): return True
	# API-Football uses generic labels for the European Champions and Europa leagues.
	return fixture.get('api_football_league_id') in {2, 3} and name in {'champions league', 'europa league', 'uefa champions league', 'uefa europa league'}


def _valid_soccer_fixture(item):
	return isinstance(item, dict) and isinstance(item.get('date'), str) and all(isinstance(item.get(key), str) and item[key] for key in ('home', 'away', 'league', 'status'))


def _live_soccer_fixtures(include_upcoming=False, with_expiry=False):
	"""Get one authoritative live-fixture set, falling back when SportAPI is unavailable."""
	profile = ku.translate_path(getattr(ku, 'profile_path', ''))
	base = os.path.join(ku.translate_path(getattr(ku, 'addon_path', '')), 'resources', 'private')
	def fetch(method, *args, **kwargs):
		if with_expiry: return method(*args, with_expiry=True, **kwargs)
		return method(*args, **kwargs), None
	(sport, sport_status), sport_expiry = fetch(_sportapi_service.get, profile, bundled=os.path.join(base, _sportapi_service.KEY_FILE)) if profile else (([], 'not configured'), None)
	(football, football_status), football_expiry = (([], 'not needed'), None) if sport_status.startswith('updated ') else (fetch(_fixture_service.get, profile, bundled=os.path.join(base, _fixture_service.KEY_FILE)) if profile else (([], 'not configured'), None))
	sport = [item for item in sport if _valid_soccer_fixture(item)] if isinstance(sport, list) else []
	football = [item for item in football if _valid_soccer_fixture(item)] if isinstance(football, list) else []
	available = sport_status.startswith('updated ') or football_status.startswith('updated ')
	selected = sport if sport_status.startswith('updated ') else football if football_status.startswith('updated ') else []
	source = 'sportapi' if sport_status.startswith('updated ') else 'api-football'
	live = [item for item in selected if isinstance(item, dict) and _selected_soccer_fixture(item, source) and (item.get('status') in _fixture_service.LIVE_STATUSES or (include_upcoming and _schedule_state(item, time.time())[0] == 'Upcoming'))]
	from concurrent.futures import ThreadPoolExecutor
	with ThreadPoolExecutor(max_workers=3) as pool:
		canadian_future = pool.submit(fetch, _fixture_service.get_canadian_premier, profile) if profile else None
		live_future = pool.submit(fetch, _canadian_championship.get_live, cache_dir=profile or None)
		schedule_future = pool.submit(fetch, _canadian_championship.get_schedule, cache_dir=profile or None) if include_upcoming else None
		(canadian, canadian_status), canadian_expiry = canadian_future.result() if canadian_future else (([], 'unavailable'), None)
		championship_live_status = 'updated'
		try: championship_live, championship_live_expiry = live_future.result()
		except (OSError, HTTPException, ValueError, TypeError): championship_live, championship_live_status, championship_live_expiry = [], 'unavailable', None
		championship_status = 'updated'
		try: championship_schedule, championship_expiry = schedule_future.result() if schedule_future else ([], None)
		except (OSError, HTTPException, ValueError, TypeError, KeyError): championship_schedule, championship_status, championship_expiry = [], 'unavailable', None
	seen = {_soccer_fixture_key(item) for item in live}
	def add_fixture(item):
		if not _valid_soccer_fixture(item): return
		key = _soccer_fixture_key(item)
		if key in seen: return
		event = (item['home'], item['away'])
		if any(_fixtures_agree(item, existing) and _fixture_service.match(event, '', [existing]) for existing in live): return
		live.append(item)
		seen.add(key)
	for item in canadian:
		if not _valid_soccer_fixture(item): continue
		if not include_upcoming and item.get('status') not in _fixture_service.LIVE_STATUSES: continue
		add_fixture(item)
	try:
		for item in championship_live: add_fixture(item)
	except (OSError, HTTPException, ValueError, TypeError): championship_live_status = 'unavailable'
	if include_upcoming:
		try:
			for item in championship_schedule: add_fixture(item)
		except (OSError, HTTPException, ValueError, TypeError, KeyError): championship_status = 'unavailable'
	status = ('API-Football: %s · SportAPI: %s' % (football_status, sport_status)) if available else 'Live fixtures unavailable'
	if canadian_status != 'updated' and available: status += ' · Canadian Premier League unavailable'
	if championship_live_status != 'updated' and available: status += ' · Canadian Championship live status unavailable'
	if championship_status != 'updated' and available: status += ' · Canadian Championship schedule unavailable'
	result = (live, football, sport, status)
	if not with_expiry: return result
	expiries = [sport_expiry if source == 'sportapi' else football_expiry]
	if canadian_status == 'updated': expiries.append(canadian_expiry)
	if championship_live_status == 'updated': expiries.append(championship_live_expiry)
	if include_upcoming and championship_status == 'updated': expiries.append(championship_expiry)
	if not available or any(not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value < float('inf') for value in expiries): return result, None
	now = time.time()
	for fixture in live:
		state, kickoff = _schedule_state(fixture, now)
		if state == 'Upcoming' and kickoff > now: expiries.append(kickoff)
	return result, min(expiries)


def _soccer_feed_event(name):
	"""Extract two teams from a provider event label, never a team-only channel."""
	if not isinstance(name, str) or _sports.non_live_title(name): return None
	known = _soccer_event(name)
	if known: return known
	for part in name.split('|')[:2]:
		part = part.strip()
		part = re.sub(r'^laliga(?:\s+ea\s+sports)?(?:\s+(?:(?:\d{4}|\d{2})/\d{2}|\(t\d{2}/\d{2}\)))?\s*:\s*', '', part, flags=re.I)
		part = re.sub(r'\s+-\s+spanish\s+laliga\s+\d{4}/\d{4}\s+-\s+week\s+\d{1,2}\s*$', '', part, flags=re.I)
		ending = re.search(r'\s*\(([^()]*)\)\s*$', part)
		if ending:
			# Keep identity markers when removing a parenthesized time or feed label.
			markers = re.findall(r'\b(?:women|womens|ladies|wfc|w|reserve|reserves|ii|iii|b|(?:u|under)\s*[- ]?\s*\d{1,2})\b', ending.group(1), re.I)
			part = part[:ending.start()] + (' ' + ' '.join(markers) if markers else '')
		match = re.fullmatch(r"([\w .&'’\-]{2,80}?)\s+(?:vs\.?|v\.?)\s+([\w .&'’\-]{2,80}?)", part, re.I)
		if not match and len(re.findall(r'\s+-\s+', part)) == 1:
			match = re.fullmatch(r"([\w .&'’\-]{2,80}?)\s+-\s+([\w .&'’\-]{2,80}?)", part, re.I)
		if match:
			event = tuple(' '.join(side.casefold().split()) for side in match.groups())
			if event[0] != event[1] and all(re.search(r'[^\W\d_]', side) for side in event): return event
	return None


def _soccer_schedule_agrees(name, fixture):
	"""Reject explicitly scheduled labels that conflict with the fixture kickoff."""
	prefix = re.match(r'^\s*laliga(?:\s+ea\s+sports)?\s+(?:(\d{4}|\d{2})/(\d{2})|\(t(\d{2})/(\d{2})\))\s*:', name, re.I)
	suffix = re.search(r'\s+-\s+spanish\s+laliga\s+(\d{4})/(\d{4})\s+-\s+week\s+\d{1,2}\s*$', name, re.I)
	for season in (prefix, suffix):
		if not season: continue
		try:
			start = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00'))
			season_year = start.year if start.month >= 7 else start.year - 1
			first, second = (season.group(1), season.group(2)) if season.group(1) else (season.group(3), season.group(4))
			if int(first) != (season_year if len(first) == 4 else season_year % 100) or int(second) != (season_year + 1 if len(second) == 4 else (season_year + 1) % 100): return False
		except (KeyError, TypeError, ValueError, AttributeError): return False
	if suffix: name = name[:suffix.start()]
	if prefix: name = name[prefix.end():]
	daytime = re.search(r'\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\s+(\d{1,2}):(\d{2})\s*(UK|UTC|GMT)\b', name, re.I)
	date = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', name)
	if date and date.group(1) != str(fixture.get('date', ''))[:10]: return False
	# Dates without a defined timezone/format cannot safely establish an occurrence.
	if re.search(r'\b\d{4}[/.]\d{1,2}[/.]\d{1,2}\b|(?<![\d/.-])\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?(?![\d/.-])|\b(?:\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2})\b', name, re.I): return False
	if not daytime:
		return not re.search(r'\b\d{1,2}:\d{2}\b', name)
	try:
		start = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00'))
		if start.utcoffset() is None: return False
		if daytime.group(4).upper() == 'UK':
			start = start.astimezone(get_timezone('Europe/London'))
		else: start = start.astimezone(timezone.utc)
		return start.strftime('%a').casefold() == daytime.group(1).casefold() and start.hour == int(daytime.group(2)) and start.minute == int(daytime.group(3))
	except (KeyError, TypeError, ValueError, OSError, ImportError): return False


def _soccer_fixture_groups(streams, fixtures, live_only=False):
	"""Join provider event labels to a unique live fixture using both team identities."""
	# Artwork IDs are only used within the bundled league catalog; they are not
	# comparable with IDs from a fixture provider.
	aliases = ({'bayer 04', 'bayer leverkusen'}, {'monchengladbach', 'borussia m gladbach'}, {'leipzig', 'rb leipzig'}, {'bremen', 'werder bremen'})
	curated = {'131': ('bayer leverkusen', aliases[0]), '268': ('borussia monchengladbach', aliases[1]), '11420': ('rb leipzig', aliases[2]), '137': ('werder bremen', aliases[3]),
		'110': ('internazionale', {'inter', 'inter milan', 'internazionale'})}
	def base_keys(name):
		keys = _fixture_service.team_keys(name)
		canonical = _fixture_service._team(name)[0]
		for group in aliases:
			if canonical in group: keys.update(group)
		return keys
	catalogs, full_names, global_names = {}, {}, {}
	items = _soccer_logos._catalog()
	ids = [item.get('id') for item in items if isinstance(item, dict) and isinstance(item.get('id'), str)]
	duplicate_ids = {identity for identity in ids if ids.count(identity) > 1}
	for item in items:
		if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'].isdecimal(): continue
		if item['id'] in duplicate_ids or not isinstance(item.get('leagues'), list): continue
		names = [item.get('name'), item.get('short_name')]
		if isinstance(item.get('aliases'), list): names.extend(item['aliases'])
		curated_team = curated.get(item['id'])
		if curated_team and isinstance(item.get('name'), str) and _fixture_service._team(item['name'])[0] == curated_team[0]: names.extend(curated_team[1])
		for name in names:
			if isinstance(name, str) and name:
				key = _fixture_service._team(name)[0]
				if key: global_names.setdefault(key, set()).add(item['id'])
		for league in item.get('leagues', []):
			if not isinstance(league, str): continue
			if isinstance(item.get('name'), str):
				full_name = _soccer_logos._normalize(item['name'])
				if full_name: full_names.setdefault(league, {}).setdefault(full_name, set()).add(item['id'])
			for name in names:
				if isinstance(name, str) and name:
					key = _fixture_service._team(name)[0]
					if key: catalogs.setdefault(league, {}).setdefault(key, set()).add(item['id'])
	def league_catalog(fixture):
		league = _soccer_logos._LEAGUES.get(_soccer_logos._normalize(fixture.get('league')))
		countries = {'eng.1': {'england'}, 'esp.1': {'spain'}, 'ger.1': {'germany'}, 'ita.1': {'italy'}, 'fra.1': {'france'},
			'usa.1': {'usa', 'united states', 'united states of america'}, 'ksa.1': {'saudi arabia'}, 'can.1': {'canada'}, 'can.championship': {'canada'}}
		country = _soccer_logos._normalize(fixture.get('league_country'))
		if country and league in countries and country not in countries[league]: return None, {}
		return league, catalogs.get(league, {})
	def catalog_keys(canonical, league, catalog):
		ids = catalog.get(canonical, set())
		return {('catalog', league, next(iter(ids)))} if len(ids) == 1 else set()
	index = {}
	owners = {}
	for fixture in fixtures or []:
		if not isinstance(fixture, dict) or not _schedule_state(fixture, time.time())[0]: continue
		for side in ('home', 'away'):
			try:
				name = fixture[side]
				canonical = _fixture_service._team(name)[0]
				league, catalog = league_catalog(fixture)
				for key in base_keys(name) | catalog_keys(canonical, league, catalog): owners.setdefault(key, set()).add(canonical)
			except (KeyError, TypeError, AttributeError): continue
	def keys(name, fixture=None):
		canonical = _fixture_service._team(name)[0]
		keys = base_keys(name)
		if fixture:
			league, catalog = league_catalog(fixture)
			keys |= catalog_keys(canonical, league, catalog)
		else:
			for league, catalog in catalogs.items(): keys |= catalog_keys(canonical, league, catalog)
		return {key for key in keys if (key == canonical and not any(key in group for group in aliases)) or len(owners.get(key, ())) == 1}
	@lru_cache(maxsize=4096)
	def provider_keys(name):
		return keys(name)
	for fixture in fixtures or []:
		if not isinstance(fixture, dict) or not _schedule_state(fixture, time.time())[0]: continue
		try:
			home, away = (keys(fixture[side], fixture) for side in ('home', 'away'))
			if home and away and fixture['league'] and fixture['date']:
				for home_key in home:
					for away_key in away: index.setdefault((home_key, away_key), []).append(fixture)
		except (KeyError, TypeError, AttributeError): continue
	groups = {}
	for stream in streams:
		if not isinstance(stream, dict) or not str(stream.get('stream_id') or '').isdecimal(): continue
		name = stream.get('name')
		event = _soccer_feed_event(name)
		if not event: continue
		scope = _fixture_scope('||' + name)
		home, away = (provider_keys(side) for side in event)
		candidates = {}
		for home_key in home:
			for away_key in away:
				for fixture in index.get((home_key, away_key), []): candidates[(id(fixture), False)] = fixture
				for fixture in index.get((away_key, home_key), []): candidates[(id(fixture), True)] = fixture
		matched = []
		for (_, reverse), fixture in candidates.items():
			if live_only and fixture.get('status') not in _fixture_service.LIVE_STATUSES: continue
			if live_only and any(other.get('status') not in _fixture_service.LIVE_STATUSES and other.get('league') == fixture.get('league') and {str(other.get(side, '')).casefold() for side in ('home', 'away')} == {str(fixture.get(side, '')).casefold() for side in ('home', 'away')} and _soccer_schedule_agrees(name, other) for other in candidates.values()): continue
			oriented = event[::-1] if reverse else event
			if re.search(r'\bWSL\b', name, re.I): oriented = (oriented[0] + ' Women', oriented[1])
			provider_sides = [_fixture_service._team(side) for side in oriented]
			fixture_sides = [_fixture_service._team(fixture[side]) for side in ('home', 'away')]
			_, league_women, league_youth = _fixture_service._team(fixture['league'])
			if not league_women and any(provider[1] != expected[1] for provider, expected in zip(provider_sides, fixture_sides)): continue
			if any((provider[2] - league_youth) != (expected[2] - league_youth) for provider, expected in zip(provider_sides, fixture_sides)): continue
			league, catalog = league_catalog(fixture)
			exact_pairs = set()
			for position, (provider, side) in enumerate(zip(oriented, ('home', 'away'))):
				expected = fixture[side]
				raw = _soccer_logos._normalize(provider)
				if raw and raw == _soccer_logos._normalize(expected) and len(full_names.get(league, {}).get(raw, set())) == 1:
					exact_pairs.add(position)
			if any((len(catalog.get(provider[0], set())) > 1 or len(catalog.get(expected[0], set())) > 1) and position not in exact_pairs for position, (provider, expected) in enumerate(zip(provider_sides, fixture_sides))): continue
			def team_match(left, right):
				left_ids, right_ids = catalog.get(left, set()), catalog.get(right, set())
				if len(left_ids) > 1 or len(right_ids) > 1: return True
				if left_ids and right_ids: return left_ids == right_ids
				if any(left in group and right in group for group in aliases):
					left_global, right_global = global_names.get(left, set()), global_names.get(right, set())
					if len(left_global) > 1 or len(right_global) > 1: return False
					if left_ids and len(right_global) == 1 and left_ids != right_global: return False
					if right_ids and len(left_global) == 1 and right_ids != left_global: return False
					if left_global and right_global and left_global != right_global: return False
					return True
				return _fixture_service._team_alias(left, right)
			if _fixture_service.match(oriented, scope, [fixture], team_match=team_match) and _soccer_schedule_agrees(name, fixture): matched.append(fixture)
		if len(matched) != 1: continue
		fixture = matched[0]
		groups.setdefault(_soccer_fixture_key(fixture), {}).setdefault(str(stream['stream_id']), stream)
	return groups


def _soccer_fixture_key(fixture):
	canonical = tuple(' '.join(fixture[side].casefold().split()) for side in ('home', 'away'))
	return canonical, fixture['date'][:10] + '|' + fixture['league'].casefold() + '|' + fixture['date'][11:16]


def all_standings(params):
	"""Show complete available standings in Kodi's scrollable text viewer."""
	sport = params.get('sport', '')
	if sport not in ('hockey', 'football', 'basketball'): return
	ku.dialog.notification('Live TV', 'Loading standings...')
	leagues = {'hockey': (('nhl', 'NHL'),), 'football': (('nfl', 'NFL'),), 'basketball': (('nba', 'NBA'),)}[sport]
	lines = []
	def clean(value):
		return re.sub(r'[\x00-\x1f\x7f\[\]]', '', str(value)).strip()
	for league, name in leagues:
		groups, metric = _american_standings.get_all(league)
		if not groups:
			lines.append(name + ' standings unavailable.')
			continue
		for heading, rows in groups:
			if lines: lines.append('')
			lines.append(clean(heading) + '  ·  ' + clean(metric))
			if not rows: lines.append('Standings unavailable.')
			for row in rows:
				lines.append('%s. %s  —  %s' % (clean(row.get('position', '')), clean(row.get('team', '')), clean(row.get('points', ''))))
	ku.dialog.textviewer('All %s standings' % sport, '\n'.join(lines))


def _f1_dashboard(account, reload, force_refresh=False, url_refresh=False):
	try:
		from modules import livetv_sports_progress
		state, fixtures, _ = livetv_sports_progress.load_fixtures('f1', force_refresh=reload in ('refresh', 'schedule'), reload=reload, url_refresh=url_refresh)
		if state not in ('ready', 'cached', 'cached_unavailable'):
			_item('Loading Formula 1 sessions…' if state == 'pending' else 'Formula 1 sessions unavailable', {}, False, playable=False)
			_item('Browse Formula 1 channels', {'mode': 'livetv.f1_channels'})
			_finish()
			return
		cached = state in ('cached', 'cached_unavailable')
		cache_status = 'Refresh failed' if state == 'cached_unavailable' else 'Updating fixtures…'
		if cached: groups = None
		else:
			fixture_reload = livetv_sports_progress.fixture_reload()
			reload = fixture_reload or reload
			force_refresh = reload == 'refresh'
			fixtures, groups = livetv_sports_progress.prepare('f1', reload, fixtures, force_refresh=force_refresh)
		ku.set_property('LiveTV.Soccer.FixtureStatus', cache_status if cached else 'Sessions updated')
		has_groups = groups is not None
		pending = not has_groups or livetv_sports_progress.incomplete()
		groups = groups or {}
		saved_counts = livetv_sports_progress.load_counts('f1', fixtures) if pending else {}
		feed_cache, speed_context = {}, _speed_context() if has_groups else ({}, {})
		for fixture in sorted(fixtures, key=lambda row: row['date']):
			state = {'LIVE': 'Live', 'Scheduled': 'Upcoming', 'Finished': 'Finished'}.get(fixture['status'], '')
			if not state: continue
			feeds = groups.get(fixture['id'], {}) if state == 'Live' else {}
			ordered = sorted(feeds.values(), key=lambda stream: (0 if re.search(r'\bCA\s*:', stream['name'], re.I) else 1, stream['name'].casefold(), int(stream['stream_id'])))
			by_ref = {_sports_ref(stream): stream for stream in ordered}
			ordered = [by_ref[ref] for ref in _speed_order(list(by_ref), speed_context)]
			stream, count = (ordered[0] if ordered else None), len(feeds)
			feed_label = cache_status if cached else '' if state == 'Upcoming' else ('%d %s' % (count, 'feed' if count == 1 else 'feeds')) if count else ('Session finished' if state == 'Finished' else 'Finding feeds…' if pending else 'Feeds unavailable' if ku.get_property('LiveTV.Sports.CatalogUnavailable') else '0 feeds')
			saved_count = livetv_sports_progress.cached_count(saved_counts, fixture['id']) if pending and state == 'Live' and not count else 0
			if saved_count: feed_label = '%d %s · %s' % (saved_count, 'feed' if saved_count == 1 else 'feeds', 'saved' if cache_status == 'Refresh failed' else 'updating')
			elif count and pending: feed_label += ' · updating'
			title = fixture['event'] + ' — ' + fixture['session_label']
			context = []
			if count > 1:
				feed_url = _sports_feed_url({'mode': 'livetv.match_feeds', 'sport': 'f1', 'session': fixture['id']}, fixture, groups)
				context.append(('Choose another feed', 'Container.Update(%s)' % feed_url))
			if pending and state == 'Live': context.append(('Retry finding feeds', 'RunPlugin(%s)' % ku.build_url({'mode': 'livetv.soccer', 'sport': 'f1', 'action': 'refresh'})))
			matched = [(_sports_ref(feed), feed) for feed in ordered] if stream and _unique_match(groups, _sports_ref(stream), stream['name']) else []
			_item(title + (' — ' + feed_label if feed_label and not count else ''), _matched_play_params(_sports_ref(stream), stream['name'], 'f1', matched, feed_cache) if stream else {}, False, stream.get('stream_icon') if stream else None, playable=bool(stream), extra_context=context,
				extra_properties={'LiveTV.EventIdentity': _fixture_focus_identity('f1', fixture), 'LiveTV.Match': title, 'LiveTV.League': 'Formula 1', 'LiveTV.Feeds': feed_label,
					'LiveTV.MatchState': '' if cached else state, 'LiveTV.StartTime': _soccer_kickoff(fixture).partition(' ')[2], 'LiveTV.Kickoff': _soccer_kickoff(fixture), 'LiveTV.StandingsKey': 'f1'})
		if has_groups: _save_feed_matches(feed_cache)
		if not fixtures: _item(cache_status if cached else 'No upcoming Formula 1 sessions', {}, False, playable=False)
	except (OSError, HTTPException, ValueError, TypeError):
		ku.set_property('LiveTV.Soccer.FixtureStatus', 'Sessions unavailable')
		_item('Formula 1 sessions unavailable', {}, False, playable=False)
	_item('Browse Formula 1 channels', {'mode': 'livetv.f1_channels'})
	_finish()
	if xbmc.getCondVisibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.soccer) + !Control.IsVisible(523)'):
		xbmc.executebuiltin('Container.SetViewMode(523)')


def f1_channels(params):
	"""Browse F1 channels without treating different feeds as interchangeable."""
	legacy = _load()
	accounts = _sports_accounts(legacy)
	if not accounts:
		_error('No Live TV account is available.')
		_finish(False)
		return
	streams, failed = [], False
	for identity, account in accounts:
		try:
			catalog = _catalog(account, 'get_live_streams')
			if not isinstance(catalog, list): raise ValueError('Invalid channel catalog')
			for stream in catalog:
				if not isinstance(stream, dict) or not str(stream.get('stream_id') or '').isdecimal(): continue
				name = stream.get('name')
				if not isinstance(name, str) or not re.search(r'\b(?:f\s*1|formula\s*(?:1|one))\b', name, re.I) or re.match(r'^\s*#+', name): continue
				streams.append(dict(stream, _provider=identity) if len(accounts) > 1 or not legacy else stream)
		except (OSError, HTTPException, ValueError, TypeError): failed = True
	streams.sort(key=lambda stream: (stream['name'].casefold(), stream.get('_provider', ''), int(stream['stream_id'])))
	page_streams, page, has_next = _channel_page(streams, params)
	if page: _item('Previous page', {'mode': 'livetv.f1_channels', 'page': str(page - 1)})
	for stream in page_streams:
		_item(stream['name'], {'mode': 'livetv.play', 'id': _sports_ref(stream)}, False, stream.get('stream_icon'))
	if has_next: _item('Next page', {'mode': 'livetv.f1_channels', 'page': str(page + 1)})
	if not streams: _item('Formula 1 channels unavailable' if failed else 'No Formula 1 channels found', {}, False, playable=False)
	elif failed: ku.dialog.notification('Live TV', 'Some providers are unavailable.')
	_finish()


def _sports_dashboard(account, sport, reload, force_refresh=False, url_refresh=False):
	try:
		from modules import livetv_sports_progress
		state, fixtures, _ = livetv_sports_progress.load_fixtures(sport, force_refresh=reload in ('refresh', 'schedule'), reload=reload, url_refresh=url_refresh)
		if state not in ('ready', 'cached', 'cached_unavailable'):
			ku.set_property('LiveTV.Soccer.FixtureStatus', 'Loading fixtures' if state == 'pending' else 'Live fixtures unavailable')
			if state == 'pending': _item('Loading fixtures…', {}, False, playable=False)
			else: _item('Fixtures unavailable — browse channels', {'mode': 'livetv.refine'})
			_finish()
			return
		cached = state in ('cached', 'cached_unavailable')
		cache_status = 'Refresh failed' if state == 'cached_unavailable' else 'Updating fixtures…'
		if cached: groups = None
		else:
			fixture_reload = livetv_sports_progress.fixture_reload()
			reload = fixture_reload or reload
			force_refresh = reload == 'refresh'
			fixtures, groups = livetv_sports_progress.prepare(sport, reload, fixtures, force_refresh=force_refresh)
		ku.set_property('LiveTV.Soccer.FixtureStatus', cache_status if cached else 'Live fixtures updated')
		has_groups = groups is not None
		pending = not has_groups or livetv_sports_progress.incomplete()
		groups = groups or {}
		saved_counts = livetv_sports_progress.load_counts(sport, fixtures) if pending else {}
		speed_context = _speed_context() if has_groups else ({}, {})
		feed_cache = {}
		now = time.time()
		rows = []
		for fixture in fixtures:
			state, kickoff = _schedule_state(fixture, now)
			if not state: continue
			event = tuple(' '.join(fixture[side]['name'].casefold().split()) for side in ('home', 'away'))
			scope = str(fixture.get('league') or '')
			rows.append((state, kickoff, event, scope, fixture, groups.get((event, scope), {}) if state == 'Live' else {}))
		# The feed key has no kickoff. Do not attach a feed to the wrong
		# game if the same teams meet twice in the schedule window.
		duplicate_keys = {(row[2], row[3]) for row in rows if sum((other[2], other[3]) == (row[2], row[3]) for other in rows) > 1}
		for state, kickoff, event, scope, fixture, feeds in sorted(rows, key=lambda row: (row[0] != 'Live', _live_start(row[4]) if row[0] == 'Live' else row[1], row[2])):
			if (event, scope) in duplicate_keys: feeds = {}
			ordered = sorted(feeds.values(), key=lambda stream: (0 if re.search(r'\bCA\s*:', stream['name'], re.I) else 1, stream['name'].casefold(), int(stream['stream_id'])))
			by_ref = {_sports_ref(stream): stream for stream in ordered}
			ordered = [by_ref[ref] for ref in _speed_order(list(by_ref), speed_context)]
			stream = ordered[0] if ordered else None
			title = '%s vs %s' % (fixture['home']['name'], fixture['away']['name'])
			count = len(feeds)
			feed_label = cache_status if cached else '' if state == 'Upcoming' else ('%d %s' % (count, 'feed' if count == 1 else 'feeds')) if count else ('Finding feeds…' if pending else 'Feeds unavailable' if ku.get_property('LiveTV.Sports.CatalogUnavailable') else '0 feeds')
			saved_count = livetv_sports_progress.cached_count(saved_counts, (event, scope)) if pending and state == 'Live' and not count and (event, scope) not in duplicate_keys else 0
			if saved_count: feed_label = '%d %s · %s' % (saved_count, 'feed' if saved_count == 1 else 'feeds', 'saved' if cache_status == 'Refresh failed' else 'updating')
			elif count and pending: feed_label += ' · updating'
			standings_sport = {'hockey': 'nhl', 'basketball': 'nba', 'football': 'nfl'}.get(sport)
			standings_event = _sports._event(title) or event
			standings_key = '|'.join((standings_sport, *standings_event)) if standings_sport else ''
			standings_match_key = standings_key
			tennis_names = ''
			if sport == 'tennis':
				tour = str(fixture.get('tour') or scope.partition(':')[0]).casefold()
				discipline = fixture.get('discipline')
				standings_key = 'tennis|' + tour + ('|doubles' if discipline == 'doubles' else '') if tour in ('atp', 'wta') and discipline in ('singles', 'doubles') else ''
				standings_match_key = ''
				if standings_key and discipline == 'singles':
					player_ids = [fixture[side].get('player_ids', []) for side in ('home', 'away')]
					if all(isinstance(ids, list) and len(ids) == 1 and isinstance(ids[0], str) and re.fullmatch(r'[0-9]{1,12}', ids[0]) and int(ids[0]) > 0 for ids in player_ids) and player_ids[0][0] != player_ids[1][0]:
						standings_match_key = ':'.join(ids[0] for ids in player_ids)
				if standings_key:
					rosters = [fixture[side].get('player_names', []) for side in ('home', 'away')]
					expected = 2 if discipline == 'doubles' else 1
					if all(isinstance(names, list) and len(names) == expected and all(isinstance(name, str) and 1 < len(name.split()) and len(name) <= 160 and all(ord(char) >= 32 for char in name) for name in names) for names in rosters):
						names = [name for roster in rosters for name in roster]
						normalized = [' '.join(unicodedata.normalize('NFKC', name).casefold().split()) for name in names]
						if len(set(normalized)) == 2 * expected: tennis_names = json.dumps(names, ensure_ascii=False)
			context = []
			if count > 1:
				feed_url = _sports_feed_url({'mode': 'livetv.match_feeds', 'sport': sport, 'home': event[0], 'away': event[1], 'scope': scope}, fixture, groups)
				context.append(('Choose another feed', 'Container.Update(%s)' % feed_url))
			if pending and state == 'Live': context.append(('Retry finding feeds', 'RunPlugin(%s)' % ku.build_url({'mode': 'livetv.soccer', 'sport': sport, 'action': 'refresh'})))
			matched_feeds = [(_sports_ref(feed), feed) for feed in ordered] if stream and _unique_match(groups, _sports_ref(stream), stream['name']) else []
			team_properties = {}
			if sport == 'tennis': team_properties['LiveTV.Tennis.PlayerNames'] = tennis_names
			if sport in ('hockey', 'basketball', 'football', 'tennis'):
				for side in ('home', 'away'):
					team_properties['LiveTV.%sTeam' % side.title()] = fixture[side]['name']
					if sport != 'tennis': team_properties['LiveTV.%sLogo' % side.title()] = _american_fixtures.team_logo(sport, fixture[side].get('abbreviation'))
			_item(title + (' — ' + feed_label if feed_label and not count else ''), _matched_play_params(_sports_ref(stream), stream['name'], sport, matched_feeds, feed_cache) if stream else {}, False, stream.get('stream_icon') if stream else None, playable=bool(stream), extra_context=context,
				extra_properties={'LiveTV.EventIdentity': _fixture_focus_identity(sport, fixture), 'LiveTV.Match': title, 'LiveTV.League': fixture.get('league_label', scope.upper()), 'LiveTV.Feeds': feed_label,
					'LiveTV.MatchState': '' if cached else state, 'LiveTV.StartTime': _soccer_kickoff(fixture).partition(' ')[2], 'LiveTV.Kickoff': _soccer_kickoff(fixture) if state == 'Upcoming' else '',
					'LiveTV.StandingsKey': standings_key, 'LiveTV.StandingsMatchKey': standings_match_key, **team_properties})
		if has_groups: _save_feed_matches(feed_cache)
		if not rows:
			message = cache_status if cached else 'Fixtures unavailable — browse channels' if ku.get_property('LiveTV.Soccer.FixtureStatus') == 'Live fixtures unavailable' else 'No live or upcoming %s matches — browse channels' % sport
			_item(message, {} if cached else {'mode': 'livetv.refine'}, False if cached else True, playable=False if cached else True)
	except (OSError, HTTPException, ValueError, TypeError):
		_error('Could not load Live TV channels.')
		_finish(False)
		return
	_finish()
	if xbmc.getCondVisibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.soccer) + !Control.IsVisible(523)'):
		xbmc.executebuiltin('Container.SetViewMode(523)')


def _sports_overview(sport_filter=None, revision=''):
	from modules import livetv_sports_overview as overview, livetv_sports_progress as progress
	accounts = _feed_accounts_fingerprint()
	rows, unavailable, schedule_unavailable, loading, stale = [], [], [], [], []
	for sport in ((sport_filter,) if sport_filter else overview.SPORTS):
		try: data = json.loads(ku.get_property(overview.PREFIX + sport) or '{}')
		except (TypeError, ValueError): data = {}
		if data.get('accounts') != accounts or not data: loading.append(sport); continue
		if data.get('unavailable'): unavailable.append(sport)
		if data.get('schedule_unavailable'): schedule_unavailable.append(sport)
		if data.get('matching'): loading.append(sport)
		if data.get('stale'): stale.append(sport)
		fixtures = data.get('fixtures', [])
		try: groups = progress._filter_guide_groups(progress._groups_from_json(data.get('groups', [])), time.time())
		except (TypeError, ValueError, KeyError): groups = {}
		entries = []
		for fixture in fixtures:
			state, kickoff = _schedule_state(fixture, time.time())
			if not state: continue
			if sport == 'f1': key = fixture['id']
			elif sport == 'soccer': key = _soccer_fixture_key(fixture)
			else:
				event = tuple(' '.join(fixture[side]['name'].casefold().split()) for side in ('home', 'away'))
				key = (event, str(fixture.get('league') or ''))
			entries.append((state, kickoff, fixture, key))
		for state, kickoff, fixture, key in entries:
			if state != 'Live': continue
			feeds = groups.get(key, {}) if state == 'Live' and sum(other[3] == key for other in entries) == 1 else {}
			rows.append((state != 'Live', _live_start(fixture) if state == 'Live' else kickoff, sport, fixture, key, feeds, groups, state))
	feed_cache = {}
	speed_context = _speed_context()
	for _, _, sport, fixture, key, feeds, groups, state in sorted(rows, key=lambda row: (row[0], row[1], row[2], str(row[4]))):
		by_ref = {_sports_ref(feed): feed for feed in feeds.values()}
		ordered = [by_ref[ref] for ref in _speed_order(list(by_ref), speed_context)]
		stream = ordered[0] if ordered else None
		if sport == 'f1': title, league = fixture['event'] + ' — ' + fixture['session_label'], 'Formula 1'
		elif sport == 'soccer': title, league = '%s vs %s' % (fixture['home'], fixture['away']), fixture['league']
		else: title, league = '%s vs %s' % (fixture['home']['name'], fixture['away']['name']), fixture.get('league_label', str(fixture.get('league') or '').upper())
		feed_label = ('%d %s' % (len(feeds), 'feed' if len(feeds) == 1 else 'feeds')) if feeds else ('Finding feeds…' if sport in loading else 'Feeds unavailable' if sport in unavailable else '0 feeds') if state == 'Live' else ''
		if feeds and sport in stale: feed_label += ' · saved'
		context = []
		if len(feeds) > 1:
			params = {'mode': 'livetv.match_feeds', 'sport': sport}
			if sport == 'f1': params['session'] = key
			else: params.update({'home': key[0][0], 'away': key[0][1], 'scope': key[1]})
			context.append(('Choose another feed', 'Container.Update(%s)' % _sports_feed_url(params, fixture, groups)))
		matched = [(_sports_ref(feed), feed) for feed in ordered] if stream and _unique_match(groups, _sports_ref(stream), stream['name']) else []
		properties = {'LiveTV.OverviewRevision': revision, 'LiveTV.ItemSport': sport, 'LiveTV.EventIdentity': _fixture_focus_identity(sport, fixture), 'LiveTV.Match': title, 'LiveTV.League': league,
			'LiveTV.Feeds': feed_label, 'LiveTV.MatchState': state, 'LiveTV.StartTime': _soccer_kickoff(fixture).partition(' ')[2], 'LiveTV.Kickoff': _soccer_kickoff(fixture) if state == 'Upcoming' else ''}
		if sport != 'f1':
			for side in ('home', 'away'):
				properties['LiveTV.%sTeam' % side.title()] = fixture[side] if sport == 'soccer' else fixture[side]['name']
				if sport == 'soccer': properties['LiveTV.%sLogo' % side.title()] = _soccer_logos.team_logo(fixture, side)
				elif sport != 'tennis': properties['LiveTV.%sLogo' % side.title()] = _american_fixtures.team_logo(sport, fixture[side].get('abbreviation'))
		_item(title + (' — ' + feed_label if feed_label and not feeds else ''), _matched_play_params(_sports_ref(stream), stream['name'], sport, matched, feed_cache) if stream else {}, False, stream.get('stream_icon') if stream else None, playable=bool(stream), extra_context=context, extra_properties=properties)
	if feed_cache: _save_feed_matches(feed_cache)
	status = ' · '.join(part for part in ('Loading sports schedules…' if loading else '', 'Some sports unavailable' if unavailable or schedule_unavailable else '', 'Some feeds are saved' if stale else '') if part)
	if not sport_filter: ku.set_property('LiveTV.Sports.OverviewStatus', status)
	if not rows and loading: _item('Loading sports schedules…', {}, False, playable=False, extra_properties={'LiveTV.OverviewRevision': revision})
	elif not rows and (unavailable or schedule_unavailable): _item('Sports schedule unavailable', {}, False, playable=False, extra_properties={'LiveTV.OverviewRevision': revision})
	elif not rows and not sport_filter: _item('No live sports events', {}, False, playable=False)
	_finish()


def sports_overview_row(params):
	from modules import livetv_sports_overview as overview
	sport = params.get('sport', '')
	if sport not in overview.SPORTS:
		_finish(False)
		return
	revision = str(params.get('revision', ''))
	if not re.fullmatch(r'[0-9]{1,30}', revision): revision = ''
	_sports_overview(sport, revision)


def soccer(params):
	sport = params.get('sport', 'soccer')
	if sport not in ('all', 'soccer', 'hockey', 'football', 'basketball', 'tennis', 'f1'): sport = 'soccer'
	ku.set_property('LiveTV.Sport', sport)
	if sport != 'all': ku.clear_property('LiveTV.Sports.OverviewStatus')
	ku.set_property('LiveTV.Sport.Title', 'LIVE SPORTS' if sport == 'all' else 'LIVE ' + ('FORMULA 1' if sport == 'f1' else sport.upper()))
	action = params.get('action', '')
	if sport == 'all':
		from modules import livetv_sports_overview as overview
		if action == 'refresh':
			ku.set_property(overview.PREFIX + 'Refresh', 'true')
			xbmc.executebuiltin('ClearProperty(ShowViewSubMenu,Home)')
			xbmc.executebuiltin('SetFocus(523)')
			return
		if not _sports_primary():
			_error('No Live TV account is available.')
			_finish(False)
			return
		ku.set_property('LiveTV.Soccer.Status', '')
		ku.set_property('LiveTV.Soccer.FixtureStatus', '')
		_sports_overview()
		return
	reload = ku.get_property('LiveTV.Soccer.Reload') if not action else ''
	force_refresh = reload == 'refresh'
	if reload: ku.clear_property('LiveTV.Soccer.Reload')
	url_refresh = params.get('refresh') == '1'
	if url_refresh:
		from modules import livetv_sports_progress
		if livetv_sports_progress.url_refresh_requested(sport): reload = 'refresh'
	account = _sports_primary()
	if not account:
		_error('No Live TV account is available.')
		if not params.get('action'): _finish(False)
		return
	league = str(ku.get_property('LiveTV.Soccer.League') or params.get('league') or 'All')
	if action == 'refresh':
		from modules.livetv_standings_generation import mark_refresh
		try:
			marker = mark_refresh(ku.translate_path(ku.profile_path), sport)
			ku.set_property('LiveTV.Standings.Refresh', sport + ':' + repr(marker))
		except (OSError, ValueError, TypeError):
			ku.dialog.notification('Live TV', 'Could not refresh saved standings.')
		xbmc.executebuiltin('ClearProperty(ShowViewSubMenu,Home)')
		xbmc.executebuiltin('SetFocus(523)')
		ku.set_property('LiveTV.Soccer.Reload', 'refresh')
		xbmc.executebuiltin('Container.Refresh')
		return
	if sport == 'f1':
		_f1_dashboard(account, reload, force_refresh, url_refresh)
		return
	if sport != 'soccer':
		_sports_dashboard(account, sport, reload, force_refresh, url_refresh)
		return
	try:
		from modules import livetv_sports_progress
		state, fixtures, auxiliary = livetv_sports_progress.load_fixtures('soccer', force_refresh=reload in ('refresh', 'schedule'), reload=reload, url_refresh=url_refresh)
		if state not in ('ready', 'cached', 'cached_unavailable'):
			if action:
				ku.dialog.notification('Live TV', 'Loading fixtures…' if state == 'pending' else 'Fixtures unavailable.')
			else:
				if state == 'pending': _item('Loading fixtures…', {}, False, playable=False)
				else: _item('Fixtures unavailable — browse channels', {'mode': 'livetv.refine'})
				_finish()
			return
		sport_fixtures, status_text = auxiliary['sport_fixtures'], auxiliary['status_text']
		leagues = sorted({fixture['league'] for fixture in fixtures}, key=str.casefold)
		if action == 'league':
			options = ['All'] + leagues
			choice = ku.dialog.select('Soccer league', options)
			if choice < 0: return
			league = options[choice]
			ku.set_property('LiveTV.Soccer.League', league)
			xbmc.executebuiltin('ClearProperty(ShowViewSubMenu,Home)')
			xbmc.executebuiltin('SetFocus(523)')
			ku.set_property('LiveTV.Soccer.Reload', 'league')
			xbmc.executebuiltin('Container.Refresh')
			return
		cached = state in ('cached', 'cached_unavailable')
		cache_status = 'Refresh failed' if state == 'cached_unavailable' else 'Updating fixtures…'
		if cached: groups = None
		else:
			fixture_reload = livetv_sports_progress.fixture_reload()
			reload = fixture_reload or reload
			force_refresh = reload == 'refresh'
			fixtures, groups = livetv_sports_progress.prepare('soccer', reload, fixtures, force_refresh=force_refresh, auxiliary={'sport_fixtures': sport_fixtures, 'status_text': status_text})
		has_groups = groups is not None
		pending = not has_groups or livetv_sports_progress.incomplete()
		groups = groups or {}
		saved_counts = livetv_sports_progress.load_counts('soccer', fixtures) if pending else {}
		speed_context = _speed_context() if has_groups else ({}, {})
		feed_cache = {}
		ku.set_property('LiveTV.Soccer.FixtureStatus', cache_status if cached else status_text)
		matches = {}
		standings_keys = {}
		standings_teams = {}
		fixtures_by_key = {_soccer_fixture_key(fixture): fixture for fixture in fixtures}
		for key in fixtures_by_key:
			matches[key] = fixtures_by_key.get(key)
			matched = matches[key]
			statuses = _fixture_service.LIVE_STATUSES if matched.get('status') in _fixture_service.LIVE_STATUSES else {'Scheduled'} if matched.get('status') == 'Scheduled' else set()
			second = [item for item in sport_fixtures if item.get('status') in statuses and _soccer_fixture_key(item) == key]
			sport_match = second[0] if len(second) == 1 else None
			if matched and sport_match and _fixtures_agree(matched, sport_match) and (matched.get('status') in _fixture_service.LIVE_STATUSES) == (sport_match.get('status') in _fixture_service.LIVE_STATUSES):
				league_id = sport_match.get('league_id')
				if isinstance(league_id, int) and not isinstance(league_id, bool) and league_id > 0:
					standings_keys[key] = str(league_id)
					home_id, away_id = sport_match.get('home_id'), sport_match.get('away_id')
					if all(isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 100000000 for value in (home_id, away_id)) and home_id != away_id:
						standings_teams[key] = '%d:%d' % (home_id, away_id)
		leagues = sorted({fixture['league'] for fixture in fixtures}, key=str.casefold)
		if league not in ['All'] + leagues: league = 'All'
		ku.set_property('LiveTV.Soccer.League', league)
		rows = []
		now = time.time()
		for (event, scope), matched in matches.items():
			feeds = groups.get((event, scope), {}) if matched.get('status') in _fixture_service.LIVE_STATUSES else {}
			ordered = sorted(feeds.values(), key=lambda stream: (0 if re.search(r'\bCA\s*:', stream['name'], re.I) else 1, stream['name'].casefold(), int(stream['stream_id'])))
			by_ref = {_sports_ref(stream): stream for stream in ordered}
			ordered = [by_ref[ref] for ref in _speed_order(list(by_ref), speed_context)]
			preferred = ordered[0] if ordered else None
			match_league = matched['league']
			if league != 'All' and league != match_league: continue
			title = '%s vs %s' % (matched['home'], matched['away']) if matched else ' vs '.join(event)
			state, kickoff = _schedule_state(matched, now)
			if not state: continue
			rows.append((title, match_league, preferred, len(feeds), event, scope, matched, state, kickoff))
		for title, match_league, stream, count, event, scope, matched, state, kickoff in sorted(rows, key=lambda row: (row[7] != 'Live', _live_start(row[6]) if row[7] == 'Live' else row[8], row[1].casefold(), row[0].casefold())):
			feed_label = cache_status if cached else '' if state == 'Upcoming' else ('%d %s' % (count, 'feed' if count == 1 else 'feeds')) if count else ('Finding feeds…' if pending else 'Feeds unavailable' if ku.get_property('LiveTV.Sports.CatalogUnavailable') else '0 feeds')
			saved_count = livetv_sports_progress.cached_count(saved_counts, (event, scope)) if pending and state == 'Live' and not count else 0
			if saved_count: feed_label = '%d %s · %s' % (saved_count, 'feed' if saved_count == 1 else 'feeds', 'saved' if cache_status == 'Refresh failed' else 'updating')
			elif count and pending: feed_label += ' · updating'
			label = '%s — %s%s' % (match_league, title, ' — ' + feed_label if feed_label else '')
			context = []
			if count > 1:
				feed_url = _sports_feed_url({'mode': 'livetv.match_feeds', 'sport': 'soccer', 'home': event[0], 'away': event[1], 'scope': scope}, matched, groups)
				context.append(('Choose another feed', 'Container.Update(%s)' % feed_url))
			if pending and state == 'Live': context.append(('Retry finding feeds', 'RunPlugin(%s)' % ku.build_url({'mode': 'livetv.soccer', 'sport': 'soccer', 'action': 'refresh'})))
			feeds = groups.get((event, scope), {})
			ordered_refs = _speed_order(list(feeds), speed_context)
			matched_feeds = [(ref, feeds[ref]) for ref in ordered_refs] if stream and _unique_match(groups, _sports_ref(stream), stream['name']) else []
			team_properties = {}
			for side in ('home', 'away'):
				team_properties['LiveTV.%sTeam' % side.title()] = matched[side]
				team_properties['LiveTV.%sLogo' % side.title()] = _soccer_logos.team_logo(matched, side)
			is_mls = _soccer_logos._LEAGUES.get(_soccer_logos._normalize(match_league)) == 'usa.1'
			_item(label, _matched_play_params(_sports_ref(stream), stream['name'], 'soccer', matched_feeds, feed_cache) if stream else {}, False, stream.get('stream_icon') if stream else None,
				playable=bool(stream), extra_context=context, extra_properties={
					'LiveTV.EventIdentity': _fixture_focus_identity('soccer', matched), 'LiveTV.Match': title, 'LiveTV.League': match_league, 'LiveTV.Feeds': feed_label, 'LiveTV.Recognition': 'Fixture matched',
					'LiveTV.StartTime': _soccer_kickoff(matched).partition(' ')[2], 'LiveTV.Kickoff': _soccer_kickoff(matched) if state == 'Upcoming' else '', 'LiveTV.MatchState': '' if cached else state,
					'LiveTV.StandingsKey': 'mls' if is_mls else standings_keys.get((event, scope), ''), 'LiveTV.StandingsMatchKey': title if is_mls else standings_teams.get((event, scope), ''), **team_properties})
		if has_groups: _save_feed_matches(feed_cache)
		if not rows:
			if cached: _item(cache_status, {}, False, playable=False)
			else: _item('Fixtures unavailable — browse channels' if status_text == 'Live fixtures unavailable' else 'No live or upcoming soccer matches — browse channels', {'mode': 'livetv.refine'})
	except (OSError, ValueError, TypeError):
		ku.set_property('LiveTV.Soccer.Status', 'Could not load channels')
		_error('Could not load Live TV channels.')
		if not action: _finish(False)
		return
	_finish()
	if xbmc.getCondVisibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.soccer) + !Control.IsVisible(523)'):
		xbmc.executebuiltin('Container.SetViewMode(523)')


def match_feeds(params):
	account = _sports_primary()
	if not account:
		_error('No Live TV account is available. Add a valid private account file to the BINGIE package.')
		_finish(False)
		return
	home, away, scope = params.get('home', ''), params.get('away', ''), params.get('scope', '')
	session = params.get('session', '')
	if params.get('sport') == 'f1':
		valid = isinstance(session, str) and bool(session)
	else:
		valid = isinstance(home, str) and isinstance(away, str) and isinstance(scope, str) and bool(home and away)
	if not valid:
		_finish(False)
		return
	try:
		from modules import livetv_feed_selection
		sport = params.get('sport', '')
		state, result = livetv_feed_selection.prepare(params)
		def waiting_row(retry_label=None):
			if retry_label:
				retry = {key: params.get(key, '') for key in ('sport', 'home', 'away', 'scope', 'session')}
				retry.update({'mode': 'livetv.match_feeds', 'retry': livetv_feed_selection.retry_request()})
				_item(retry_label, retry)
			else: _item('Finding more feeds…', {}, False, playable=False)
		if state not in ('ready', 'partial', 'partial_unavailable'):
			if state == 'slow': waiting_row('Still finding feeds… — select to retry')
			else: _item('Finding feeds…' if state == 'loading' else 'Feeds unavailable — return and try again', {}, False, playable=False)
			_finish()
			return
		choices = [(ref, feed) for ref, feed, _ in result]
		unique = {ref: is_unique for ref, _, is_unique in result}
		choices.sort(key=lambda entry: (0 if re.search(r'\bCA\s*:', str(entry[1].get('name') or ''), re.I) else 1, str(entry[1].get('name') or '').casefold(), int(str(entry[1].get('stream_id') or entry[0]).rsplit(':', 1)[-1])))
		by_ref = dict(choices)
		choices = livetv_feed_selection.order_choices([(ref, by_ref[ref]) for ref in _speed_order(list(by_ref), _speed_context())])
		feed_cache = {}
		if choices:
			stream_id, first = choices[0]
			name = str(first.get('name') or 'Live channel')
			feed_label = 'Feed 1' if ':' in stream_id else name.split('|')[-1].strip()
			_item('Play first feed — ' + feed_label, _matched_play_params(stream_id, name, sport, choices if unique.get(stream_id) else [], feed_cache), False, first.get('stream_icon'))
			for number, (stream_id, stream) in enumerate(choices, 1):
				name = str(stream.get('name') or 'Live channel')
				_item(('Feed %d' % number if ':' in stream_id else name.split('|')[-1].strip()) + ' (play channel)', _matched_play_params(stream_id, name, sport, choices if unique.get(stream_id) else [], feed_cache), False, stream.get('stream_icon'))
		elif state == 'ready':
			_item('No matching live feeds are currently available', {}, False, playable=False)
		if state == 'partial_unavailable': waiting_row('Some feeds unavailable — select to retry')
		elif state == 'partial': waiting_row('Still finding feeds… — select to retry' if livetv_feed_selection.is_slow() else None)
		if sport: _save_feed_matches(feed_cache)
	except (OSError, HTTPException, ValueError, TypeError):
		_error('Could not load matching feeds. Try again.')
		_finish(False)
		return
	_finish()


def _f1_startup(reference, account, failover):
	"""Choose a matched feed that produces data within a bounded startup window."""
	refs = [reference] + (failover.get('alternatives', []) if failover else [])
	candidates = []
	for ref in refs[:_FAILOVER_LIMIT + 1]:
		if not isinstance(ref, str): continue
		provider, separator, stream_id = ref.partition(':')
		selected_account = (_account_for_feed(provider) if separator else _load()) if ref != reference else account
		if not separator: stream_id = ref
		if not selected_account or not stream_id.isdecimal(): continue
		try: url = '%s/live/%s/%s/%s.ts' % (selected_account['server'], quote(selected_account['username'], safe=''), quote(selected_account['password'], safe=''), stream_id)
		except (KeyError, TypeError): continue
		candidates.append((ref, url))
	if not candidates: return None
	results = queue.Queue()
	deadline = time.monotonic() + 8.5
	def probe():
		for ref, url in candidates:
			if time.monotonic() >= deadline: break
			valid = False
			try:
				with _vpn_open(url, headers={'User-Agent': 'BINGIE Live TV', 'Accept-Encoding': 'identity'}, timeout=3) as response:
					status = getattr(response, 'status', 200)
					content_type = str(response.headers.get('Content-Type', '')).lower()
					if 200 <= status < 300 and not any(value in content_type for value in ('text/html', 'application/json', 'text/plain')):
						first = response.read(1)
						valid = bool(first) and first not in (b'<', b'{')
			except (OSError, HTTPException, ValueError, TypeError, AttributeError): pass
			if valid:
				results.put((ref, url))
				return
		results.put(None)
	threading.Thread(target=probe, daemon=True).start()
	try: return results.get(timeout=9)
	except queue.Empty: return None


def play(params):
	xbmcgui.Window(10000).clearProperty(_FAILOVER_PROPERTY)
	xbmcgui.Window(10000).clearProperty('BingieLiveTVPlaybackFailed')
	xbmcgui.Window(10000).clearProperty('BingieLiveTVOpening')
	reference = params.get('id', '')
	provider, separator, stream_id = reference.partition(':')
	account = _account_for_feed(provider) if separator else _load()
	if not separator: stream_id = reference
	if not account or not stream_id.isdecimal():
		_error('This channel cannot be played. Check your Live TV account.')
		xbmcplugin.setResolvedUrl(_handle(), False, xbmcgui.ListItem())
		return
	failover = _cached_failover(params) if params.get('sport') else None if separator or _soccer_event(params.get('name', '')) else _prepare_failover(account, stream_id, params.get('name', ''), defer=True)
	if not failover: failover = {'id': reference, 'alternatives': [], 'feeds': [{'id': reference, 'name': 'Feed 1'}], 'token': uuid.uuid4().hex}
	url = '%s/live/%s/%s/%s.ts' % (account['server'], quote(account['username'], safe=''), quote(account['password'], safe=''), stream_id)
	if params.get('sport') == 'f1':
		selected = _f1_startup(reference, account, failover)
		if not selected:
			ku.dialog.notification('Live TV', 'No Formula 1 feed responded. Check the VPN or choose another channel.')
			xbmcplugin.setResolvedUrl(_handle(), False, xbmcgui.ListItem())
			return
		selected_reference, url = selected
		if failover:
			selected_feed = next((feed for feed in failover['feeds'] if feed['id'] == selected_reference), None)
			if ('expires' in failover and time.time() >= failover['expires']) or not selected_feed or ('expires' in selected_feed and time.time() >= selected_feed['expires']):
				if selected_reference != reference:
					ku.dialog.notification('Live TV', 'Feed matches expired. Return to the feed list and try again.')
					xbmcplugin.setResolvedUrl(_handle(), False, xbmcgui.ListItem())
					return
				failover = None
			else:
				failover['feeds'] = [feed for feed in failover['feeds'] if 'expires' not in feed or time.time() < feed['expires']]
				failover['id'] = selected_reference
				failover['alternatives'] = [feed['id'] for feed in failover['feeds'] if feed['id'] != selected_reference][:_FAILOVER_LIMIT]
				failover['manual_only'] = True
	try: url = _vpn_register(url)
	except (OSError, HTTPException, ValueError, TypeError):
		_error(_vpn_failure_message() or 'Live TV VPN unavailable. Check the VPN connection and try again.')
		xbmcplugin.setResolvedUrl(_handle(), False, xbmcgui.ListItem())
		return
	item = xbmcgui.ListItem(path=url)
	item.setMimeType('video/mp2t')
	item.setContentLookup(False)
	if failover:
		failover['started_at'] = time.monotonic()
		xbmcgui.Window(10000).setProperty(_FAILOVER_PROPERTY, json.dumps(failover))
		from modules.livetv_failover import start_opening
		start_opening()
	xbmcplugin.setResolvedUrl(_handle(), True, item)
