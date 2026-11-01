"""Refresh provider-specific soccer guide snapshots without blocking navigation."""
import hashlib
import json
import os
import tempfile
import time

from modules.livetv_vpn_relay import open_url


_RENEW_SECONDS = 110 * 60
_CACHE_LIMIT = 16 * 1024 * 1024
_DEADLINE_SECONDS = 180


class _CancelableFile:
	def __init__(self, source, canceled, deadline):
		self.source, self.canceled, self.deadline = source, canceled, deadline

	def read(self, size=-1):
		if self.canceled.is_set() or time.monotonic() >= self.deadline: raise OSError('Sports guide canceled or timed out')
		return self.source.read(size)

	def __getattr__(self, name):
		return getattr(self.source, name)


def _fresh(path, key, now):
	try:
		if not 0 < os.path.getsize(path) <= _CACHE_LIMIT: return False
		with open(path, encoding='utf-8') as source: data = json.load(source)
		created = data.get('created')
		return data.get('key') == key and isinstance(created, (int, float)) and not isinstance(created, bool) and 0 <= now - created < _RENEW_SECONDS and isinstance(data.get('programmes'), dict)
	except (OSError, ValueError, TypeError, AttributeError): return False


def refresh(canceled, on_update):
	"""Fetch each enabled provider once; never alter the ordinary TV guide caches."""
	from menus import livetv
	from modules import kodi_utils as ku
	profile = ku.translate_path(ku.profile_path)
	if not profile: return
	os.makedirs(profile, mode=0o700, exist_ok=True)
	for _, account in livetv._sports_accounts(livetv._load()):
		if canceled.is_set(): return
		try:
			url = livetv._guide_url(account)
			key = hashlib.sha256(url.encode('utf-8')).hexdigest()
			path = livetv._sports_guide_cache_path(account)
			if _fresh(path, key, time.time()): continue
			deadline = time.monotonic() + _DEADLINE_SECONDS
			with tempfile.TemporaryFile(mode='w+b', dir=profile) as raw:
				with open_url(url, headers={'User-Agent': 'BINGIE Live TV', 'Accept-Encoding': 'identity'}, timeout=10) as response:
					size = 0
					while True:
						if canceled.is_set() or time.monotonic() >= deadline: raise OSError('Sports guide canceled or timed out')
						chunk = response.read1(65536) if hasattr(response, 'read1') else response.read(65536)
						if not chunk: break
						size += len(chunk)
						if size > livetv._GUIDE_DOWNLOAD_LIMIT: raise ValueError('Sports guide too large')
						raw.write(chunk)
				created = time.time()
				programmes = livetv._parse_raw_guide(_CancelableFile(raw, canceled, deadline), created, None, sports_window=True)
			if canceled.is_set() or time.monotonic() >= deadline: continue
			encoded = json.dumps({'key': key, 'created': created, 'programmes': programmes}, separators=(',', ':')).encode('utf-8')
			if len(encoded) > _CACHE_LIMIT: continue
			fd, temporary = tempfile.mkstemp(prefix='.livetv-sports-guide-', dir=profile)
			try:
				with os.fdopen(fd, 'wb') as output: output.write(encoded)
				if canceled.is_set(): return
				os.replace(temporary, path)
			finally:
				if os.path.exists(temporary): os.unlink(temporary)
			on_update()
		except Exception:
			# Provider URLs may contain credentials; never log the exception text.
			continue
