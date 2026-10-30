"""Best-effort startup observations for matching Live TV feeds."""
import hashlib
import json
import os
import tempfile
import time
from queue import SimpleQueue
from threading import Thread

from modules import kodi_utils as ku

_FILE = 'livetv-feed-startup.json'
_MAX_AGE = 7 * 86400
_MAX_ENTRIES = 500
_FAILURE_COOLDOWN = 300
_queue = SimpleQueue()
_worker = None


def _path():
	return os.path.join(ku.translate_path(ku.profile_path), _FILE)


def _read():
	try:
		path = _path()
		if os.path.getsize(path) > 100000: return {}
		with open(path, encoding='utf-8') as source: data = json.load(source)
		if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('feeds'), dict): return {}
		now = time.time()
		return {key: value for key, value in data['feeds'].items() if isinstance(key, str) and isinstance(value, dict) and isinstance(value.get('updated'), (int, float)) and 0 <= now - value['updated'] <= _MAX_AGE and ((isinstance(value.get('seconds'), (int, float)) and 0 <= value['seconds'] <= 30) or (isinstance(value.get('failed_until'), (int, float)) and now < value['failed_until'] <= now + _FAILURE_COOLDOWN))}
	except (OSError, ValueError, TypeError, UnicodeError, AttributeError): return {}


def key(ref, account):
	try:
		identity = [account[name] for name in ('server', 'username', 'password')]
		if not all(isinstance(value, str) and value for value in identity) or not isinstance(ref, str): return ''
		return hashlib.sha256(json.dumps(identity + [ref], separators=(',', ':')).encode()).hexdigest()
	except (TypeError, KeyError): return ''


def snapshot():
	return _read()


def order(refs, keys, values):
	"""Only compare feeds when at least two have fresh observations."""
	now = time.time()
	failed = [ref for ref in refs if isinstance(values.get(keys.get(ref), {}).get('failed_until'), (int, float)) and values[keys[ref]]['failed_until'] > now]
	available = [ref for ref in refs if ref not in failed]
	known = [ref for ref in available if isinstance(values.get(keys.get(ref), {}).get('seconds'), (int, float))]
	if len(known) >= 2:
		available = sorted(available, key=lambda ref: (0, values[keys[ref]]['seconds']) if ref in known else (1, 0))
	return available + failed


def _write_loop():
	while True:
		ref, seconds = _queue.get()
		try:
			values = _read()
			if seconds is None:
				values[ref] = {'updated': time.time(), 'failed_until': time.time() + _FAILURE_COOLDOWN}
			else:
				old = values.get(ref, {}).get('seconds')
				values[ref] = {'seconds': seconds if old is None else (old * 0.75 + seconds * 0.25), 'updated': time.time()}
			values = dict(sorted(values.items(), key=lambda item: item[1]['updated'], reverse=True)[:_MAX_ENTRIES])
			path = _path()
			os.makedirs(os.path.dirname(path), exist_ok=True)
			fd, temporary = tempfile.mkstemp(prefix='.livetv-feed-startup-', dir=os.path.dirname(path))
			try:
				with os.fdopen(fd, 'w', encoding='utf-8') as output: json.dump({'version': 1, 'feeds': values}, output, separators=(',', ':'))
				os.replace(temporary, path)
			finally:
				if os.path.exists(temporary): os.unlink(temporary)
		except (OSError, ValueError, TypeError, AttributeError): pass


def record(ref, seconds):
	global _worker
	if not isinstance(ref, str) or not ref or (seconds is not None and not 0 <= seconds <= 30): return
	if _worker is None:
		_worker = Thread(target=_write_loop, daemon=True)
		_worker.start()
	_queue.put((ref, seconds))
