"""Live TV using an Xtream-compatible provider and Kodi's existing video player."""
import json
import gzip
import hashlib
import heapq
import os
import re
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime, timezone
from http.client import HTTPException
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

import xbmc
import xbmcgui
import xbmcplugin
from modules import kodi_utils as ku
from modules.livetv_catalog import get as _catalog_get
from modules.livetv_catalog import _icon as _catalog_icon, _redact as _catalog_redact
from modules.livetv_guide_search import cached_search as _cached_guide_search
from modules import livetv_fixtures as _fixture_service
from modules import livetv_sportapi as _sportapi_service
from modules import livetv_sports as _sports
try:
	from modules import livetv_feed_speed as _feed_speed
except ImportError:
	_feed_speed = None
from modules import livetv_american_standings as _american_standings
from modules import livetv_american_fixtures as _american_fixtures
from modules import livetv_canadian_championship as _canadian_championship

_GUIDE_DOWNLOAD_LIMIT = 512 * 1024 * 1024
_GUIDE_XML_LIMIT = 768 * 1024 * 1024
_GUIDE_FRESH_SECONDS = 2 * 60 * 60
_GUIDE_STALE_SECONDS = 24 * 60 * 60
_API_RESPONSE_LIMIT = 128 * 1024 * 1024
_SEARCH_RESULT_LIMIT = 300
_SEARCH_GUIDE_LIMIT = 5000
_FAILOVER_PROPERTY = 'BingieLiveTVFailover'
_FAILOVER_LIMIT = 8
_HOME_MAX_CHANNELS = 500
_CHANNEL_PAGE_SIZE = 100
_home_state_cache = None


def _soccer_event(name):
	"""Recognize the provider's explicitly live, event-specific soccer labels."""
	if not isinstance(name, str): return None
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
		r'la\s+liga|serie\s+a|ligue\s+1|bundesliga|major\s+league\s+soccer|world\s+cup)\b')
	dates = re.findall(date_pattern, metadata)
	competitions = re.findall(competition_pattern, metadata)
	return '|'.join(sorted(set(' '.join(value.split()) for value in dates + competitions)))


def _play_params(stream_id, name, sport=''):
	params = {'mode': 'livetv.play', 'id': stream_id}
	if sport in ('soccer', 'hockey', 'football', 'basketball'):
		params.update({'name': name, 'sport': sport})
	elif _soccer_event(name): params['name'] = name
	return params


def _prepare_failover(account, stream_id, name, sport='', defer=False):
	if ':' in stream_id or sport in ('soccer', 'hockey', 'football', 'basketball'):
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
		except (OSError, ValueError, TypeError): pass
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
	except (OSError, ValueError, TypeError):
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


def _sports_catalog_groups(primary, sport, reload='', on_status=None, fixtures=None):
	from concurrent.futures import ThreadPoolExecutor
	legacy = _load()
	accounts = _sports_accounts(legacy)
	primary_id = accounts[0][0] if accounts else None
	status_values = []
	if sport == 'soccer' and fixtures is None:
		fixtures, _, _, _ = _live_soccer_fixtures(include_upcoming=True)
	elif sport in ('hockey', 'football', 'basketball') and fixtures is None:
		try:
			profile = ku.translate_path(getattr(ku, 'profile_path', ''))
			fixtures = _american_fixtures.get_schedule(sport, cache_dir=profile or None)
			ku.set_property('LiveTV.Soccer.FixtureStatus', 'Live fixtures updated')
		except (OSError, ValueError, TypeError):
			fixtures = []
			ku.set_property('LiveTV.Soccer.FixtureStatus', 'Live fixtures unavailable')
	if not fixtures: return {}, []
	def fetch(entry):
		identity, account = entry
		try:
			kwargs = {'force_refresh': reload == 'refresh'}
			if on_status and identity == primary_id: kwargs['on_status'] = lambda created, stale: status_values.append((created, stale))
			streams = _catalog(account, 'get_live_streams', **kwargs)
			return identity, streams if isinstance(streams, list) else [], isinstance(streams, list)
		except (OSError, ValueError, TypeError): return identity, [], False
	groups, all_streams, failed = {}, [], False
	with ThreadPoolExecutor(max_workers=min(4, max(1, len(accounts)))) as pool:
		for identity, streams, available in pool.map(fetch, accounts):
			if not available: failed = True
			streams = [dict(stream, _provider=identity, _primary=identity == primary_id) if len(accounts) > 1 or not legacy else stream for stream in streams if isinstance(stream, dict)]
			all_streams.extend(streams)
			local = _sports.fixture_groups(streams, sport, fixtures) if sport in ('hockey', 'football', 'basketball') else _soccer_fixture_groups(streams, fixtures)
			for key, feeds in local.items():
				for stream_id, stream in feeds.items(): groups.setdefault(key, {})[identity + ':' + stream_id if stream.get('_provider') else stream_id] = stream
	ku.set_property('LiveTV.Sports.CatalogUnavailable', 'true' if failed else '')
	if on_status and status_values: on_status(*status_values[-1])
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
	with urlopen(Request(url, headers={'User-Agent': 'BINGIE Live TV'}), timeout=20) as response:
		data = response.read(_API_RESPONSE_LIMIT + 1)
		if len(data) > _API_RESPONSE_LIMIT: raise ValueError('Response too large')
		return json.loads(data.decode('utf-8-sig'))


def _catalog(account, action, *, force_refresh=False, on_status=None, **params):
	return _catalog_get(account, action, _request, ku.translate_path(ku.profile_path), on_stale=lambda: ku.dialog.notification('Live TV', 'Showing saved channels; refresh failed.'), on_status=on_status, force_refresh=force_refresh, **params)


def _error(message):
	ku.dialog.ok('Live TV', message)


def next_feed(params):
	from modules.livetv_failover import request_manual_switch
	request_manual_switch()


def fixture_settings(params):
	profile = ku.translate_path(ku.profile_path)
	bundled = os.path.join(ku.translate_path(ku.addon_path), 'resources', 'private', _fixture_service.KEY_FILE)
	profile_key = bool(_fixture_service.read_key(profile))
	existing = bool(_fixture_service.read_key(profile, bundled))
	if existing:
		options = ['Replace API-Football key'] + (['Remove saved API-Football key'] if profile_key else [])
		choice = ku.dialog.select('Soccer match recognition', options)
		if choice < 0: return
		if choice == 1:
			try: _fixture_service.remove_key(profile)
			except OSError: return _error('Could not remove the API-Football key.')
			message = 'Bundled API-Football key restored.' if _fixture_service.read_key(profile, bundled) else 'Soccer match recognition disabled.'
			ku.dialog.notification('Live TV', message)
			if xbmc.getCondVisibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.soccer)'): xbmc.executebuiltin('Container.Refresh')
			return
	key = ku.dialog.input('API-Football key', option=xbmcgui.ALPHANUM_HIDE_INPUT)
	if not key or not key.strip(): return
	try: _fixture_service.save_key(profile, key.strip())
	except ValueError: return _error('The API-Football key format is invalid.')
	except OSError: return _error('Could not save the API-Football key.')
	ku.dialog.notification('Live TV', 'Soccer match recognition enabled.')
	if xbmc.getCondVisibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.soccer)'): xbmc.executebuiltin('Container.Refresh')


def sportapi_settings(params):
	profile = ku.translate_path(ku.profile_path)
	local_key = os.path.join(ku.translate_path(ku.addon_path), 'resources', 'private', _sportapi_service.KEY_FILE)
	profile_key = bool(_sportapi_service.read_key(profile))
	if _sportapi_service.read_key(profile, local_key):
		options = ['Replace SportAPI key'] + (['Remove saved SportAPI key'] if profile_key else [])
		choice = ku.dialog.select('SportAPI match recognition', options)
		if choice < 0: return
		if choice == 1:
			try: _sportapi_service.remove_key(profile)
			except OSError: return _error('Could not remove the SportAPI key.')
			message = 'Local SportAPI key restored.' if _sportapi_service.read_key(profile, local_key) else 'SportAPI match recognition disabled.'
			ku.dialog.notification('Live TV', message)
			if xbmc.getCondVisibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.soccer)'): xbmc.executebuiltin('Container.Refresh')
			return
	key = ku.dialog.input('SportAPI key', option=xbmcgui.ALPHANUM_HIDE_INPUT)
	if not key or not key.strip(): return
	try: _sportapi_service.save_key(profile, key.strip())
	except ValueError: return _error('The SportAPI key format is invalid.')
	except OSError: return _error('Could not save the SportAPI key.')
	ku.dialog.notification('Live TV', 'SportAPI match recognition enabled.')
	if xbmc.getCondVisibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.soccer)'): xbmc.executebuiltin('Container.Refresh')


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
	ku.dialog.ok('Live TV', 'Live TV account saved.')


def guide_settings(params):
	account = _load()
	if not account:
		_error('No Live TV account is available. Add a valid private account file to the BINGIE package.')
		return
	url = ku.dialog.input('XMLTV guide URL (leave empty to cancel)', option=xbmcgui.ALPHANUM_HIDE_INPUT).strip()
	if not url: return
	try:
		parts = urlsplit(url)
		valid = parts.scheme in ('http', 'https') and parts.hostname and not (parts.username or parts.password or parts.fragment)
	except ValueError:
		valid = False
	if not valid:
		_error('Enter a valid HTTP or HTTPS XMLTV guide URL.')
		return
	account['epg_url'] = url
	bundled = _read_account(_bundled_credentials_path())
	if bundled: account['guide_override_base'] = _account_fingerprint(bundled)
	try:
		_save(account)
	except OSError:
		_error('Could not save the guide URL in the Kodi profile.')
		return
	ku.dialog.ok('Live TV', 'TV guide URL saved.')


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
		_item('Browse channels and guide', {'mode': 'livetv.refine'})
		_item('TV guide', {'mode': 'livetv.guide'})
		_item('All channels', {'mode': 'livetv.channels'})
		for category in categories:
			if not isinstance(category, dict) or not category.get('category_id'): continue
			_item(str(category.get('category_name') or 'Other'), {'mode': 'livetv.channels', 'category': str(category['category_id'])})
	except (OSError, ValueError, TypeError):
		_error('Could not load Live TV categories. Check the server URL and account details, then try again.')
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
		if category: _item('TV guide for this category', {'mode': 'livetv.guide_channels', 'category': category})
		if page: _item('Previous page', {**page_params, 'page': str(page - 1)})
		for stream in streams:
			stream_id = str(stream.get('stream_id') or '')
			name = str(stream.get('name') or 'Live channel')
			_item(name, _play_params(stream_id, name), False, stream.get('stream_icon'))
		if has_next: _item('Next page', {**page_params, 'page': str(page + 1)})
	except (OSError, ValueError, TypeError):
		_error('Could not load channels. Check the account details and try again.')
		_finish(False)
		return
	_finish()


def _guide_url(account):
	return account.get('epg_url') or account['server'] + '/xmltv.php?' + urlencode({'username': account['username'], 'password': account['password']})


def _guide_cache_path():
	return os.path.join(ku.translate_path(ku.profile_path), 'livetv-guide.json')


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


def _parse_guide(source, now, wanted=None, search_term=None):
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
				if channel and (wanted is None or channel in wanted) and (search_term is None or search_term in title.casefold()):
					start, stop = _guide_timestamp(node.get('start')), _guide_timestamp(node.get('stop'))
				else:
					start = stop = None
				if start is not None and stop is not None and stop > (now if search_term is not None else now - 86400) and start < now + 3 * 86400 and stop > start:
					count += 1
					if search_term is None and count > 200000: raise ValueError('Too many programmes')
					if search_term is None or count <= _SEARCH_GUIDE_LIMIT:
						programmes.setdefault(channel, []).append([start, stop, title])
				if root is not None: root.clear()
			elif node.tag == 'channel' and root is not None: root.clear()
	parser.close()
	for entries in programmes.values(): entries.sort(key=lambda entry: entry[0])
	return programmes


def _parse_raw_guide(raw, now, wanted, search_term=None):
	raw.seek(0)
	magic = raw.read(2)
	raw.seek(0)
	if magic == b'\x1f\x8b':
		with gzip.GzipFile(fileobj=raw) as decoded: return _parse_guide(decoded, now, wanted, search_term)
	return _parse_guide(raw, now, wanted, search_term)


class _GuideProgrammes(dict):
	def __init__(self, data, created):
		super().__init__(data)
		self.created = created


def _download_guide(url, now, wanted=None, search_term=None):
	profile = ku.translate_path(ku.profile_path)
	os.makedirs(profile, mode=0o700, exist_ok=True)
	key = hashlib.sha256(url.encode('utf-8')).hexdigest()
	path = os.path.join(profile, 'livetv-xmltv-' + key + '.cache')
	stale_created = None
	try:
		stat = os.stat(path)
		age = time.time() - stat.st_mtime
		if 0 <= age < _GUIDE_STALE_SECONDS and 0 < stat.st_size <= _GUIDE_DOWNLOAD_LIMIT: stale_created = stat.st_mtime
		if stale_created is not None and age < _GUIDE_FRESH_SECONDS:
			try:
				with open(path, 'rb') as raw: return _GuideProgrammes(_parse_raw_guide(raw, now, wanted, search_term), stat.st_mtime)
			except (OSError, ValueError, ET.ParseError, EOFError, zlib.error):
				pass
	except OSError:
		pass
	fd, temporary = tempfile.mkstemp(prefix='.livetv-xmltv-', dir=profile)
	try:
		with os.fdopen(fd, 'w+b') as raw:
			with urlopen(Request(url, headers={'User-Agent': 'BINGIE Live TV', 'Accept-Encoding': 'identity'}), timeout=30) as response:
				size = 0
				deadline = time.monotonic() + 180
				while True:
					chunk = response.read(65536)
					if not chunk: break
					if time.monotonic() > deadline: raise ValueError('Guide download timed out')
					size += len(chunk)
					if size > _GUIDE_DOWNLOAD_LIMIT: raise ValueError('Guide too large')
					raw.write(chunk)
			programmes = _parse_raw_guide(raw, now, wanted, search_term)
			created = os.fstat(raw.fileno()).st_mtime
		os.replace(temporary, path)
		try:
			for name in os.listdir(profile):
				if re.fullmatch(r'livetv-xmltv-[0-9a-f]{64}\.cache', name) and name != os.path.basename(path):
					try: os.unlink(os.path.join(profile, name))
					except OSError: pass
		except OSError:
			pass
		return _GuideProgrammes(programmes, created)
	except (OSError, ValueError, ET.ParseError, EOFError, zlib.error):
		if stale_created is not None:
			try:
				with open(path, 'rb') as raw: stale = _parse_raw_guide(raw, now, wanted, search_term)
				ku.dialog.notification('Live TV', 'Showing saved guide data; refresh failed.')
				return _GuideProgrammes(stale, stale_created)
			except (OSError, ValueError, ET.ParseError, EOFError, zlib.error):
				pass
		raise
	finally:
		if os.path.exists(temporary): os.unlink(temporary)


def _guide_data(account, wanted=None):
	now = time.time()
	url = _guide_url(account)
	key = hashlib.sha256(url.encode('utf-8')).hexdigest()
	cache = None
	try:
		with open(_guide_cache_path(), encoding='utf-8') as f: cache = json.load(f)
		if not isinstance(cache, dict) or cache.get('key') != key or not isinstance(cache.get('created'), (int, float)) or not isinstance(cache.get('programmes'), dict): cache = None
	except (OSError, ValueError):
		pass
	covered = cache and (cache.get('wanted') is None or wanted is not None and wanted.issubset(cache['wanted']))
	if covered and 0 <= now - cache['created'] < _GUIDE_FRESH_SECONDS: return cache['programmes']
	try:
		programmes = _download_guide(url, now, wanted)
	except (OSError, ValueError, ET.ParseError, EOFError, zlib.error):
		if covered and 0 <= now - cache['created'] < _GUIDE_STALE_SECONDS:
			ku.dialog.notification('Live TV', 'Showing saved guide data; refresh failed.')
			return cache['programmes']
		raise
	path = _guide_cache_path()
	created = getattr(programmes, 'created', now)
	if cache and wanted is not None and cache.get('wanted') is not None and cache['created'] == created and 0 <= now - created < _GUIDE_FRESH_SECONDS:
		merged = dict(cache['programmes'])
		merged.update(programmes)
		if sum(len(entries) for entries in merged.values()) <= 200000:
			programmes = merged
			wanted = wanted.union(cache['wanted'])
	try:
		fd, temporary = tempfile.mkstemp(prefix='.livetv-guide-', dir=os.path.dirname(path))
		try:
			with os.fdopen(fd, 'w', encoding='utf-8') as f: json.dump({'key': key, 'created': created, 'wanted': sorted(wanted) if wanted is not None else None, 'programmes': programmes}, f, separators=(',', ':'))
			os.replace(temporary, path)
		finally:
			if os.path.exists(temporary): os.unlink(temporary)
	except OSError:
		pass
	return programmes


def _guide_time(timestamp):
	return datetime.fromtimestamp(timestamp, timezone.utc).astimezone().strftime('%a %H:%M')


def _soccer_kickoff(fixture):
	if not fixture: return ''
	try:
		value = fixture.get('date', '')
		if not isinstance(value, str): return ''
		kickoff = datetime.fromisoformat(value.replace('Z', '+00:00'))
		if kickoff.utcoffset() is None: return ''
		from zoneinfo import ZoneInfo
		return kickoff.astimezone(ZoneInfo('America/Vancouver')).strftime('%a %H:%M')
	except (ValueError, OverflowError, OSError, ImportError, KeyError): return ''


def _schedule_state(fixture, now):
	if fixture.get('status') in _fixture_service.LIVE_STATUSES: return 'Live', 0
	if fixture.get('status') != 'Scheduled': return '', 0
	try:
		start = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00'))
		if start.utcoffset() is None: return '', 0
		kickoff = start.timestamp()
		return ('Upcoming', kickoff) if 0 < kickoff - now <= 24 * 3600 else ('', 0)
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
	except (OSError, ValueError, TypeError):
		_error('Could not search Live TV channels. Check the account and try again.')
		_finish(False)
		return
	streams = [s for s in streams if isinstance(s, dict) and str(s.get('stream_id') or '').isdecimal()]
	by_epg = {}
	event_feeds = {}
	channel_matches = []
	for stream in streams:
		name = str(stream.get('name') or 'Live channel')
		epg_id = str(stream.get('epg_channel_id') or '')
		if epg_id: by_epg.setdefault(epg_id, []).append(stream)
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
	guide_count = 0
	guide_truncated = False
	try:
		now = time.time()
		if by_epg:
			url, wanted = _guide_url(account), set(by_epg)
			programmes = _cached_guide_search(ku.translate_path(ku.profile_path), url, now, wanted, needle, lambda: _download_guide(url, now, wanted, search_term=needle))
		else: programmes = {}
		matches = heapq.nsmallest(_SEARCH_RESULT_LIMIT + 1, ((start, stop, title, epg_id, stream) for epg_id, entries in programmes.items() for start, stop, title in entries for stream in by_epg.get(epg_id, [])), key=lambda entry: entry[0])
		guide_truncated = len(matches) > _SEARCH_RESULT_LIMIT or sum(map(len, programmes.values())) >= _SEARCH_GUIDE_LIMIT
		for start, stop, title, epg_id, stream in matches[:_SEARCH_RESULT_LIMIT]:
			name = str(stream.get('name') or 'Live channel')
			when = 'Now' if start <= now < stop else _guide_time(start)
			_item('Guide: %s — %s — %s (channel schedule)' % (when, title, name), {'mode': 'livetv.schedule', 'id': str(stream['stream_id']), 'epg': epg_id, 'name': name}, True, stream.get('stream_icon'))
		guide_count = min(len(matches), _SEARCH_RESULT_LIMIT)
	except (OSError, ValueError, TypeError, ET.ParseError, EOFError, zlib.error):
		ku.dialog.notification('Live TV', 'TV guide unavailable; showing channel name matches only.')
	if len(rows) > _SEARCH_RESULT_LIMIT or guide_truncated:
		ku.dialog.notification('Live TV', 'Showing the first search results. Try a more specific name.')
	elif not channel_matches and not guide_count:
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


def _live_soccer_fixtures(include_upcoming=False):
	"""Get one authoritative live-fixture set, falling back when SportAPI is unavailable."""
	profile = ku.translate_path(getattr(ku, 'profile_path', ''))
	base = os.path.join(ku.translate_path(getattr(ku, 'addon_path', '')), 'resources', 'private')
	sport, sport_status = _sportapi_service.get(profile, bundled=os.path.join(base, _sportapi_service.KEY_FILE)) if profile else ([], 'not configured')
	football, football_status = ([], 'not needed') if sport_status.startswith('updated ') else (_fixture_service.get(profile, bundled=os.path.join(base, _fixture_service.KEY_FILE)) if profile else ([], 'not configured'))
	available = sport_status.startswith('updated ') or football_status.startswith('updated ')
	selected = sport if sport_status.startswith('updated ') else football if football_status.startswith('updated ') else []
	source = 'sportapi' if sport_status.startswith('updated ') else 'api-football'
	live = [item for item in selected if isinstance(item, dict) and _selected_soccer_fixture(item, source) and (item.get('status') in _fixture_service.LIVE_STATUSES or (include_upcoming and _schedule_state(item, time.time())[0] == 'Upcoming'))]
	canadian, canadian_status = _fixture_service.get_canadian_premier(profile) if profile else ([], 'unavailable')
	seen = {_soccer_fixture_key(item) for item in live}
	def add_fixture(item):
		key = _soccer_fixture_key(item)
		if key in seen: return
		event = (item['home'], item['away'])
		if any(_fixtures_agree(item, existing) and _fixture_service.match(event, '', [existing]) for existing in live): return
		live.append(item)
		seen.add(key)
	for item in canadian:
		if not include_upcoming and item.get('status') not in _fixture_service.LIVE_STATUSES: continue
		add_fixture(item)
	championship_live_status = 'updated'
	try:
		for item in _canadian_championship.get_live(cache_dir=profile or None): add_fixture(item)
	except (OSError, HTTPException, ValueError, TypeError): championship_live_status = 'unavailable'
	championship_status = 'updated'
	if include_upcoming:
		try:
			for item in _canadian_championship.get_schedule(cache_dir=profile or None): add_fixture(item)
		except (OSError, HTTPException, ValueError, TypeError): championship_status = 'unavailable'
	status = ('API-Football: %s · SportAPI: %s' % (football_status, sport_status)) if available else 'Live fixtures unavailable'
	if canadian_status != 'updated' and available: status += ' · Canadian Premier League unavailable'
	if championship_live_status != 'updated' and available: status += ' · Canadian Championship live status unavailable'
	if championship_status != 'updated' and available: status += ' · Canadian Championship schedule unavailable'
	return live, football, sport, status


def _soccer_feed_event(name):
	"""Extract two teams from a provider event label, never a team-only channel."""
	if not isinstance(name, str) or re.search(r'\b(?:end|ended|replay|delayed)\b', name, re.I): return None
	known = _soccer_event(name)
	if known: return known
	for part in name.split('|')[:2]:
		part = part.strip()
		ending = re.search(r'\s*\(([^()]*)\)\s*$', part)
		if ending:
			# Keep identity markers when removing a parenthesized time or feed label.
			markers = re.findall(r'\b(?:women|womens|ladies|wfc|w|reserve|reserves|ii|iii|b|(?:u|under)\s*[- ]?\s*\d{1,2})\b', ending.group(1), re.I)
			part = part[:ending.start()] + (' ' + ' '.join(markers) if markers else '')
		match = re.fullmatch(r"([\w .&'’\-]{2,80}?)\s+(?:vs\.?|v\.?)\s+([\w .&'’\-]{2,80}?)", part, re.I)
		if match:
			event = tuple(' '.join(side.casefold().split()) for side in match.groups())
			if event[0] != event[1] and all(re.search(r'[^\W\d_]', side) for side in event): return event
	return None


def _soccer_schedule_agrees(name, fixture):
	"""Reject explicitly scheduled labels that conflict with the fixture kickoff."""
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
			from zoneinfo import ZoneInfo
			start = start.astimezone(ZoneInfo('Europe/London'))
		else: start = start.astimezone(timezone.utc)
		return start.strftime('%a').casefold() == daytime.group(1).casefold() and start.hour == int(daytime.group(2)) and start.minute == int(daytime.group(3))
	except (KeyError, TypeError, ValueError, OSError, ImportError): return False


def _soccer_fixture_groups(streams, fixtures):
	"""Join provider event labels to a unique live fixture using both team identities."""
	index = {}
	owners = {}
	for fixture in fixtures or []:
		if not isinstance(fixture, dict) or not _schedule_state(fixture, time.time())[0]: continue
		for side in ('home', 'away'):
			try:
				name = fixture[side]
				canonical = _fixture_service._team(name)[0]
				for key in _fixture_service.team_keys(name): owners.setdefault(key, set()).add(canonical)
			except (KeyError, TypeError, AttributeError): continue
	def keys(name):
		canonical = _fixture_service._team(name)[0]
		return {key for key in _fixture_service.team_keys(name) if key == canonical or len(owners.get(key, ())) == 1}
	for fixture in fixtures or []:
		if not isinstance(fixture, dict) or not _schedule_state(fixture, time.time())[0]: continue
		try:
			home, away = (keys(fixture[side]) for side in ('home', 'away'))
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
		home, away = (keys(side) for side in event)
		candidates = {}
		for home_key in home:
			for away_key in away:
				for fixture in index.get((home_key, away_key), []): candidates[(id(fixture), False)] = fixture
				for fixture in index.get((away_key, home_key), []): candidates[(id(fixture), True)] = fixture
		matched = []
		for (_, reverse), fixture in candidates.items():
			oriented = event[::-1] if reverse else event
			if re.search(r'\bWSL\b', name, re.I): oriented = (oriented[0] + ' Women', oriented[1])
			if _fixture_service.match(oriented, scope, [fixture]) and _soccer_schedule_agrees(name, fixture): matched.append(fixture)
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


def _sports_dashboard(account, sport, reload):
	try:
		profile = ku.translate_path(getattr(ku, 'profile_path', ''))
		try:
			fixtures = _american_fixtures.get_schedule(sport, cache_dir=profile or None)
		except (OSError, ValueError, TypeError):
			ku.set_property('LiveTV.Soccer.FixtureStatus', 'Live fixtures unavailable')
			_item('Fixtures unavailable — browse channels', {'mode': 'livetv.refine'})
			_finish()
			return
		ku.set_property('LiveTV.Soccer.FixtureStatus', 'Live fixtures updated')
		groups, streams = _sports_catalog_groups(account, sport, reload, fixtures=fixtures)
		speed_context = _speed_context()
		now = time.time()
		rows = []
		for fixture in fixtures:
			state, kickoff = _schedule_state(fixture, now)
			if not state: continue
			event = tuple(' '.join(fixture[side]['name'].casefold().split()) for side in ('home', 'away'))
			scope = str(fixture.get('league') or '')
			rows.append((state, kickoff, event, scope, fixture, groups.get((event, scope), {})))
		# The American feed key has no kickoff. Do not attach a feed to the wrong
		# game if the same teams meet twice in the schedule window.
		duplicate_keys = {key for key in groups if sum((row[2], row[3]) == key for row in rows) > 1}
		for state, kickoff, event, scope, fixture, feeds in sorted(rows, key=lambda row: (row[0] != 'Live', row[1], row[2])):
			if (event, scope) in duplicate_keys: feeds = {}
			ordered = sorted(feeds.values(), key=lambda stream: (0 if re.search(r'\bCA\s*:', stream['name'], re.I) else 1, stream['name'].casefold(), int(stream['stream_id'])))
			by_ref = {_sports_ref(stream): stream for stream in ordered}
			ordered = [by_ref[ref] for ref in _speed_order(list(by_ref), speed_context)]
			stream = ordered[0] if ordered else None
			title = '%s vs %s' % (fixture['home']['name'], fixture['away']['name'])
			count = len(feeds)
			feed_label = ('%d %s' % (count, 'feed' if count == 1 else 'feeds')) if count else ('Feeds unavailable' if ku.get_property('LiveTV.Sports.CatalogUnavailable') else 'No feeds found yet')
			standings_sport = 'nhl' if sport == 'hockey' else 'nba' if sport == 'basketball' else 'nfl'
			standings_event = _sports._event(title) or event
			standings_key = '|'.join((standings_sport, *standings_event))
			context = []
			if count > 1:
				feed_url = ku.build_url({'mode': 'livetv.match_feeds', 'sport': sport, 'home': event[0], 'away': event[1], 'scope': scope})
				context.append(('Choose another feed', 'Container.Update(%s)' % feed_url))
			_item(title + (' — ' + feed_label if not count else ''), _play_params(_sports_ref(stream), stream['name'], sport) if stream else {}, False, stream.get('stream_icon') if stream else None, playable=bool(stream), extra_context=context,
				extra_properties={'LiveTV.Match': title, 'LiveTV.League': scope.upper(), 'LiveTV.Feeds': feed_label, 'LiveTV.MatchState': state, 'LiveTV.Kickoff': _soccer_kickoff(fixture) if state == 'Upcoming' else '',
					'LiveTV.StandingsKey': standings_key, 'LiveTV.StandingsMatchKey': standings_key})
		if not rows:
			message = 'Fixtures unavailable — browse channels' if ku.get_property('LiveTV.Soccer.FixtureStatus') == 'Live fixtures unavailable' else 'No live or upcoming %s matches — browse channels' % sport
			_item(message, {'mode': 'livetv.refine'})
	except (OSError, ValueError, TypeError):
		_error('Could not load Live TV channels.')
		_finish(False)
		return
	_finish()
	if xbmc.getCondVisibility('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.soccer) + !Control.IsVisible(523)'):
		xbmc.executebuiltin('Container.SetViewMode(523)')


def soccer(params):
	sport = params.get('sport', 'soccer')
	if sport not in ('soccer', 'hockey', 'football', 'basketball'): sport = 'soccer'
	ku.set_property('LiveTV.Sport', sport)
	ku.set_property('LiveTV.Sport.Title', 'LIVE ' + sport.upper())
	action = params.get('action', '')
	reload = ku.get_property('LiveTV.Soccer.Reload') if not action else ''
	if reload: ku.clear_property('LiveTV.Soccer.Reload')
	if params.get('refresh') == '1': reload = 'refresh'
	account = _sports_primary()
	if not account:
		_error('No Live TV account is available.')
		if not params.get('action'): _finish(False)
		return
	league = str(ku.get_property('LiveTV.Soccer.League') or params.get('league') or 'All')
	if action == 'refresh':
		xbmc.executebuiltin('ClearProperty(ShowViewSubMenu,Home)')
		xbmc.executebuiltin('SetFocus(523)')
		ku.set_property('LiveTV.Soccer.Reload', 'refresh')
		xbmc.executebuiltin('Container.Refresh')
		return
	if sport != 'soccer':
		_sports_dashboard(account, sport, reload)
		return
	try:
		catalog_status = [None, True]
		def status(created, stale):
			catalog_status[:] = [created, stale]
			when = datetime.fromtimestamp(created).astimezone().strftime('%a %H:%M')
			ku.set_property('LiveTV.Soccer.Status', ('Saved channels from %s; refresh failed' if stale else 'Updated %s') % when)
		fixtures, _, sport_fixtures, status_text = _live_soccer_fixtures(include_upcoming=True)
		groups, streams = _sports_catalog_groups(account, 'soccer', reload, on_status=status, fixtures=fixtures)
		speed_context = _speed_context()
		ku.set_property('LiveTV.Soccer.FixtureStatus', status_text)
		matches = {}
		standings_keys = {}
		standings_teams = {}
		fixtures_by_key = {_soccer_fixture_key(fixture): fixture for fixture in fixtures}
		for key in fixtures_by_key:
			matches[key] = fixtures_by_key.get(key)
			matched = matches[key]
			second = [item for item in sport_fixtures if item.get('status') in _fixture_service.LIVE_STATUSES and _soccer_fixture_key(item) == key]
			sport_match = second[0] if len(second) == 1 else None
			if matched and sport_match and _fixtures_agree(matched, sport_match) and (matched.get('status') in _fixture_service.LIVE_STATUSES) == (sport_match.get('status') in _fixture_service.LIVE_STATUSES):
				league_id = sport_match.get('league_id')
				if isinstance(league_id, int) and not isinstance(league_id, bool) and league_id > 0:
					standings_keys[key] = str(league_id)
					home_id, away_id = sport_match.get('home_id'), sport_match.get('away_id')
					if all(isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 100000000 for value in (home_id, away_id)) and home_id != away_id:
						standings_teams[key] = '%d:%d' % (home_id, away_id)
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
		if league not in ['All'] + leagues: league = 'All'
		ku.set_property('LiveTV.Soccer.League', league)
		rows = []
		now = time.time()
		for (event, scope), matched in matches.items():
			feeds = groups.get((event, scope), {})
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
		for title, match_league, stream, count, event, scope, matched, state, kickoff in sorted(rows, key=lambda row: (row[7] != 'Live', row[8], row[1].casefold(), row[0].casefold())):
			feed_label = ('%d %s' % (count, 'feed' if count == 1 else 'feeds')) if count else ('Feeds unavailable' if ku.get_property('LiveTV.Sports.CatalogUnavailable') else 'No feeds found yet')
			label = '%s — %s — %s' % (match_league, title, feed_label)
			context = []
			if count > 1:
				feed_url = ku.build_url({'mode': 'livetv.match_feeds', 'sport': 'soccer', 'home': event[0], 'away': event[1], 'scope': scope})
				context.append(('Choose another feed', 'Container.Update(%s)' % feed_url))
			_item(label, _play_params(_sports_ref(stream), stream['name'], 'soccer') if stream else {}, False, stream.get('stream_icon') if stream else None, playable=bool(stream), extra_context=context, extra_properties={'LiveTV.Match': title, 'LiveTV.League': match_league, 'LiveTV.Feeds': feed_label, 'LiveTV.Recognition': 'Fixture matched', 'LiveTV.Kickoff': _soccer_kickoff(matched) if state == 'Upcoming' else '', 'LiveTV.MatchState': state, 'LiveTV.StandingsKey': standings_keys.get((event, scope), ''), 'LiveTV.StandingsMatchKey': standings_teams.get((event, scope), '')})
		if not rows: _item('Fixtures unavailable — browse channels' if status_text == 'Live fixtures unavailable' else 'No live or upcoming soccer matches — browse channels', {'mode': 'livetv.refine'})
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
	if not isinstance(home, str) or not isinstance(away, str) or not isinstance(scope, str) or not home or not away:
		_finish(False)
		return
	try:
		sport = params.get('sport', '')
		if sport:
			groups, streams = _sports_catalog_groups(account, sport)
		else:
			streams = _catalog(account, 'get_live_streams')
			groups = _soccer_groups(streams)
		feeds = groups.get(((home, away), scope), {})
		choices = list(feeds.items())
		choices.sort(key=lambda entry: (0 if re.search(r'\bCA\s*:', str(entry[1].get('name') or ''), re.I) else 1, str(entry[1].get('name') or '').casefold(), int(str(entry[1].get('stream_id') or entry[0]).rsplit(':', 1)[-1])))
		by_ref = dict(choices)
		choices = [(ref, by_ref[ref]) for ref in _speed_order(list(by_ref), _speed_context())]
		if choices:
			stream_id, first = choices[0]
			name = str(first.get('name') or 'Live channel')
			feed_label = 'Feed 1' if ':' in stream_id else name.split('|')[-1].strip()
			_item('Play first feed — ' + feed_label, _play_params(stream_id, name, sport), False, first.get('stream_icon'))
			for number, (stream_id, stream) in enumerate(choices, 1):
				name = str(stream.get('name') or 'Live channel')
				_item(('Feed %d' % number if ':' in stream_id else name.split('|')[-1].strip()) + ' (play channel)', _play_params(stream_id, name, sport), False, stream.get('stream_icon'))
		else:
			ku.dialog.notification('Live TV', 'No matching live feeds are currently available.')
	except (OSError, ValueError, TypeError):
		_error('Could not load matching feeds. Try again.')
		_finish(False)
		return
	_finish()


def guide(params):
	account = _load()
	if not account:
		_error('No Live TV account is available. Add a valid private account file to the BINGIE package.')
		_finish(False)
		return
	try:
		categories = _catalog(account, 'get_live_categories')
		if not isinstance(categories, list): raise ValueError('Invalid response')
		_item('Search Live TV', {'mode': 'livetv.search', 'isFolder': 'false'}, False, None, False)
		_item('All channels', {'mode': 'livetv.guide_channels'})
		for category in categories:
			if isinstance(category, dict) and category.get('category_id'):
				_item(str(category.get('category_name') or 'Other'), {'mode': 'livetv.guide_channels', 'category': str(category['category_id'])})
	except (OSError, ValueError, TypeError):
		_error('Could not load TV guide categories. Check the Live TV account and try again.')
		_finish(False)
		return
	_finish()


def guide_channels(params):
	account = _load()
	if not account:
		_error('No Live TV account is available. Add a valid private account file to the BINGIE package.')
		_finish(False)
		return
	try:
		category = params.get('category', '')
		streams = _catalog(account, 'get_live_streams', **({'category_id': category} if category else {}))
		if not isinstance(streams, list): raise ValueError('Invalid response')
		wanted = {str(stream['epg_channel_id']) for stream in streams if isinstance(stream, dict) and stream.get('epg_channel_id')}
		programmes = _guide_data(account, wanted) if wanted else {}
		streams, page, has_next = _channel_page(streams, params)
		page_params = {'mode': 'livetv.guide_channels', 'category': category}
		now = time.time()
		if page: _item('Previous page', {**page_params, 'page': str(page - 1)})
		for stream in streams:
			stream_id = str(stream.get('stream_id') or '')
			name = str(stream.get('name') or 'Live channel')
			epg_id = str(stream.get('epg_channel_id') or '')
			entries = programmes.get(epg_id, [])
			current = next((entry for entry in entries if entry[0] <= now < entry[1]), None)
			upcoming = next((entry for entry in entries if entry[0] > now), None)
			if entries:
				label = name + (' — Now: ' + current[2] if current else ' — No current listing') + (' | Next: ' + _guide_time(upcoming[0]) + ' ' + upcoming[2] if upcoming else '')
				_item(label, {'mode': 'livetv.schedule', 'id': stream_id, 'epg': epg_id, 'name': name}, True, stream.get('stream_icon'))
			else:
				_item(name + ' — No guide data (play channel)', _play_params(stream_id, name), False, stream.get('stream_icon'))
		if has_next: _item('Next page', {**page_params, 'page': str(page + 1)})
	except (OSError, ValueError, TypeError, ET.ParseError, EOFError, zlib.error):
		_error('Could not load the TV guide. Check the guide URL in Settings or choose a smaller category. Channels remain available in Live TV.')
		_finish(False)
		return
	_finish()


def schedule(params):
	account = _load()
	stream_id = params.get('id', '')
	if not account or not stream_id.isdecimal():
		_error('This channel is unavailable.')
		_finish(False)
		return
	try:
		epg_id = params.get('epg', '')
		entries = _guide_data(account, {epg_id}).get(epg_id, [])
		now = time.time()
		name = str(params.get('name') or 'channel')
		_item('Play ' + name, _play_params(stream_id, name), False)
		for start, stop, title in entries:
			if stop > now:
				_item(_guide_time(start) + '–' + _guide_time(stop) + '  ' + title + ' (play live channel)', _play_params(stream_id, name), False)
	except (OSError, ValueError, TypeError, ET.ParseError, EOFError, zlib.error):
		_error('Could not load this channel’s schedule. Check the TV guide URL and try again.')
		_finish(False)
		return
	_finish()


def play(params):
	xbmcgui.Window(10000).clearProperty(_FAILOVER_PROPERTY)
	reference = params.get('id', '')
	provider, separator, stream_id = reference.partition(':')
	account = _account_for_feed(provider) if separator else _load()
	if not separator: stream_id = reference
	if not account or not stream_id.isdecimal():
		_error('This channel cannot be played. Check your Live TV account.')
		xbmcplugin.setResolvedUrl(_handle(), False, xbmcgui.ListItem())
		return
	failover = _prepare_failover(account, reference if separator else stream_id, params.get('name', ''), params.get('sport', ''), defer=True)
	url = '%s/live/%s/%s/%s.ts' % (account['server'], quote(account['username'], safe=''), quote(account['password'], safe=''), stream_id)
	item = xbmcgui.ListItem(path=url)
	item.setMimeType('video/mp2t')
	item.setContentLookup(False)
	if failover:
		failover['started_at'] = time.monotonic()
		xbmcgui.Window(10000).setProperty(_FAILOVER_PROPERTY, json.dumps(failover))
	xbmcplugin.setResolvedUrl(_handle(), True, item)
