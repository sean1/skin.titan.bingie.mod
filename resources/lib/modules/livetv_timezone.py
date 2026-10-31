"""Load bundled time zone data without Kodi's native ZoneInfo implementation."""
import os
from functools import lru_cache
from zoneinfo import ZoneInfo


_BUNDLED = {'America/Toronto', 'America/Vancouver', 'Europe/London'}


@lru_cache(maxsize=3)
def _bundled_timezone(name):
	from zoneinfo._zoneinfo import ZoneInfo as PythonZoneInfo
	path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'timezones', *name.split('/'))
	with open(path, 'rb') as data: return PythonZoneInfo.from_file(data, key=name)


def get_timezone(name):
	if name in _BUNDLED: return _bundled_timezone(name)
	return ZoneInfo(name)
