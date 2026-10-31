"""Persist per-sport standings refresh times across Kodi sessions."""
import math
import os
import tempfile
import time


_SPORTS = {'soccer', 'hockey', 'football', 'basketball', 'tennis', 'f1'}


def _path(profile, sport):
	if sport not in _SPORTS or not isinstance(profile, str) or not profile: raise ValueError('Invalid standings refresh scope')
	return os.path.join(profile, 'livetv-standings-refresh-' + sport)


def get_refresh_time(profile, sport):
	try:
		with open(_path(profile, sport), encoding='ascii') as source: value = float(source.read(64))
		return value if math.isfinite(value) and value > 0 else 0.0
	except (OSError, ValueError, TypeError, UnicodeError): return 0.0


def mark_refresh(profile, sport):
	path = _path(profile, sport)
	os.makedirs(profile, mode=0o700, exist_ok=True)
	value = time.time()
	fd, temporary = tempfile.mkstemp(prefix='.livetv-standings-refresh-', dir=profile)
	try:
		with os.fdopen(fd, 'w', encoding='ascii') as target: target.write(repr(value))
		os.replace(temporary, path)
	finally:
		if os.path.exists(temporary): os.unlink(temporary)
	return value
