"""Small private caches for an Xtream provider's live channel catalog."""
import hashlib
import json
import os
import re
import tempfile
import time
from urllib.parse import parse_qsl, unquote, urlsplit

FRESH_SECONDS = 120
STALE_SECONDS = 60 * 60
MAX_BYTES = 32 * 1024 * 1024
MAX_ENTRIES = 150000
MAX_FILES = 16
_FIELDS = {
	'get_live_streams': ('stream_id', 'name', 'epg_channel_id', 'category_id', 'stream_icon'),
	'get_live_categories': ('category_id', 'category_name'),
}


def _key(account, action, params):
	identity = [account.get('server', ''), account.get('username', ''), account.get('password', ''), action, params]
	return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _redact(value, secrets):
	for secret in secrets:
		if not secret: continue
		if len(secret) >= 4: value = value.replace(secret, '[redacted]')
		else: value = re.sub(r'(?<!\w)' + re.escape(secret) + r'(?!\w)', '[redacted]', value)
	return value


def _icon(value, secrets):
	if not isinstance(value, str): return None
	try:
		parts = urlsplit(value)
		if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.fragment: return None
		decoded = unquote(parts.netloc + parts.path + '?' + parts.query)
		if any(secret and (secret in decoded if len(secret) >= 4 else secret in re.split(r'[^\w]+', decoded)) for secret in secrets): return None
		for key, _ in parse_qsl(parts.query, keep_blank_values=True, max_num_fields=256):
			if key.casefold() not in ('v', 'version', 'w', 'width', 'h', 'height', 'size', 'dpr', 'format'): return None
		return value
	except ValueError:
		return None


def _clean(data, action, account):
	if not isinstance(data, list) or len(data) > MAX_ENTRIES: raise ValueError('Invalid channel catalog')
	secrets = (str(account.get('username') or ''), str(account.get('password') or ''))
	cleaned = []
	for entry in data:
		if not isinstance(entry, dict): continue
		item = {}
		for field in _FIELDS[action]:
			value = entry.get(field)
			if field == 'stream_icon':
				value = _icon(value, secrets)
			elif field in ('stream_id', 'category_id', 'epg_channel_id') and isinstance(value, (str, int)) and not isinstance(value, bool):
				# IDs must stay byte-for-byte intact; skip caching rather than alter one that contains a credential.
				if _redact(str(value), secrets) != str(value): return None
			elif isinstance(value, str):
				# Changing a name would change search and event matching. Avoid caching instead.
				if _redact(value, secrets) != value: return None
			elif not isinstance(value, int) or isinstance(value, bool):
				value = None
			if value is not None: item[field] = value
		cleaned.append(item)
	return cleaned


def _read(path, key, now):
	try:
		stat = os.stat(path)
		if stat.st_size <= 0 or stat.st_size > MAX_BYTES: return None
		with open(path, encoding='utf-8') as source: payload = json.load(source)
		if not isinstance(payload, dict) or payload.get('key') != key or not isinstance(payload.get('created'), (int, float)) or isinstance(payload.get('created'), bool): return None
		age = now - payload['created']
		if not 0 <= age < STALE_SECONDS or not isinstance(payload.get('items'), list) or len(payload['items']) > MAX_ENTRIES: return None
		return payload, age
	except (OSError, ValueError, TypeError, OverflowError):
		return None


def _write(profile, path, payload):
	temporary = None
	try:
		os.makedirs(profile, mode=0o700, exist_ok=True)
		encoded = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
		if len(encoded) > MAX_BYTES: return
		fd, temporary = tempfile.mkstemp(prefix='.livetv-catalog-', dir=profile)
		with os.fdopen(fd, 'wb') as target: target.write(encoded)
		os.replace(temporary, path)
		temporary = None
		files = []
		for name in os.listdir(profile):
			if re.fullmatch(r'livetv-catalog-[0-9a-f]{64}\.json', name):
				try:
					candidate = os.path.join(profile, name)
					files.append((os.stat(candidate).st_mtime, candidate))
				except OSError: pass
		for _, old in sorted(files, reverse=True)[MAX_FILES:]:
			if old != path:
				try: os.unlink(old)
				except OSError: pass
	except (OSError, TypeError, ValueError, OverflowError):
		pass
	finally:
		if temporary is not None:
			try: os.unlink(temporary)
			except OSError: pass


def get(account, action, fetch, profile, *, on_stale=None, on_status=None, now=None, force_refresh=False, **params):
	"""Return the catalog; refresh after two minutes, with a one-hour offline fallback."""
	if action not in _FIELDS: return fetch(account, action, **params)
	now = time.time() if now is None else now
	key = _key(account, action, params)
	path = os.path.join(profile, 'livetv-catalog-' + key + '.json')
	cached = _read(path, key, now)
	full_live_catalog = action == 'get_live_streams' and not params
	# An earlier empty response is not a known-good full catalogue.
	if full_live_catalog and cached and not cached[0]['items']: cached = None
	if cached and cached[1] < FRESH_SECONDS and not force_refresh:
		if on_status: on_status(cached[0]['created'], False)
		return cached[0]['items']
	try:
		data = fetch(account, action, **params)
		if full_live_catalog and data == []:
			# Some providers transiently return an empty list despite having channels.
			data = fetch(account, action, **params)
			if data == []: raise ValueError('Empty live channel catalog')
		clean = _clean(data, action, account)
	except (OSError, ValueError, TypeError):
		if not cached: raise
		if on_stale: on_stale()
		if on_status: on_status(cached[0]['created'], True)
		return cached[0]['items']
	if clean is not None: _write(profile, path, {'key': key, 'created': now, 'items': clean})
	if on_status: on_status(now, False)
	return data
