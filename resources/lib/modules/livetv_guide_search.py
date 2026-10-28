"""Keep a small, private cache of recent XMLTV search results."""
import hashlib
import json
import os
import re
import tempfile
import time

_RESULT_TTL = 5 * 60
_RAW_TTL = 2 * 60 * 60
_MAX_SIZE = 16 * 1024 * 1024
_MAX_FILES = 8
_RESULT_LIMIT = 5000
_NAME = re.compile(r'livetv-guide-search-[0-9a-f]{64}\.json\Z')


def _stamp(path):
	try:
		stat = os.stat(path)
		if stat.st_size <= 0 or not 0 <= time.time() - stat.st_mtime < _RAW_TTL: return None
		return [stat.st_mtime_ns, stat.st_size]
	except OSError:
		return None


def _identity(url, wanted, term):
	key = hashlib.sha256()
	for value in (url, term, *(sorted(wanted) if wanted is not None else [])):
		value = str(value).encode('utf-8')
		key.update(len(value).to_bytes(8, 'big'))
		key.update(value)
	return key.hexdigest()


def _filter(data, now):
	"""Keep the same current/upcoming time window as the streaming parser."""
	if not isinstance(data, dict): raise ValueError('Invalid guide cache')
	result = {}
	for channel, entries in data.items():
		if not isinstance(channel, str) or not isinstance(entries, list): raise ValueError('Invalid guide cache')
		kept = []
		for entry in entries:
			if not isinstance(entry, list) or len(entry) != 3: raise ValueError('Invalid guide cache')
			start, stop, title = entry
			if not isinstance(start, (int, float)) or not isinstance(stop, (int, float)) or not isinstance(title, str): raise ValueError('Invalid guide cache')
			if stop > now and start < now + 3 * 86400 and stop > start: kept.append(entry)
		if kept: result[channel] = kept
	return result


def cached_search(profile, url, now, wanted, term, loader):
	"""Use a recent exact-query result only while its source XML is unchanged.

	A miss always delegates to the existing XMLTV parser, including its network and
	stale-feed handling. Cached results expire quickly so the moving three-day
	search window cannot remain stale for the full XMLTV refresh interval.
	"""
	key = _identity(url, wanted, term)
	raw_key = hashlib.sha256(url.encode('utf-8')).hexdigest()
	raw_path = os.path.join(profile, 'livetv-xmltv-' + raw_key + '.cache')
	path = os.path.join(profile, 'livetv-guide-search-' + key + '.json')
	stamp = _stamp(raw_path)
	if stamp is not None:
		try:
			if os.path.getsize(path) > _MAX_SIZE: raise ValueError('Guide cache too large')
			with open(path, encoding='utf-8') as source: cache = json.load(source)
			age = time.time() - cache['created']
			if cache['version'] == 1 and cache['raw'] == stamp and 0 <= age < _RESULT_TTL: return _filter(cache['programmes'], now)
		except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
			pass
	programmes = loader()
	# The parser stops at this limit. Keeping a filtered version could hide its
	# truncation notice or fail to show later matches as earlier entries expire.
	if sum(len(entries) for entries in programmes.values()) >= _RESULT_LIMIT: return programmes
	new_stamp = _stamp(raw_path)
	if new_stamp is None or (stamp is not None and stamp != new_stamp): return programmes
	try:
		payload = json.dumps({'version': 1, 'created': time.time(), 'raw': new_stamp, 'programmes': programmes}, separators=(',', ':')).encode('utf-8')
		if len(payload) > _MAX_SIZE: return programmes
		os.makedirs(profile, mode=0o700, exist_ok=True)
		fd, temporary = tempfile.mkstemp(prefix='.livetv-guide-search-', dir=profile)
		try:
			with os.fdopen(fd, 'wb') as target: target.write(payload)
			os.replace(temporary, path)
		finally:
			if os.path.exists(temporary): os.unlink(temporary)
		try:
			entries = sorted(((os.stat(os.path.join(profile, name)).st_mtime_ns, name) for name in os.listdir(profile) if _NAME.fullmatch(name)), reverse=True)
			for _, name in entries[_MAX_FILES:]:
				try: os.unlink(os.path.join(profile, name))
				except OSError: pass
		except OSError:
			pass
	except (OSError, ValueError, TypeError, OverflowError):
		pass
	return programmes
