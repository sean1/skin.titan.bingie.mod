"""Use bundled time zone data when the host has no IANA database."""
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_BUNDLED = {'America/Toronto', 'America/Vancouver', 'Europe/London'}


def get_timezone(name):
	try:
		return ZoneInfo(name)
	except ZoneInfoNotFoundError:
		if name not in _BUNDLED: raise
		path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'timezones', *name.split('/'))
		with open(path, 'rb') as data: return ZoneInfo.from_file(data, key=name)
