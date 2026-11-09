"""Conservatively associate provider archive programmes with F1 sessions.

The caller supplies decoded guide text and a provider-confirmed retention period;
this module performs no network requests and does not construct playback URLs.
"""
import calendar
from bisect import bisect_left
import math
import re
import unicodedata
from datetime import datetime

_SESSION_PATTERNS = {
	'FP1': r'\b(?:fp\s*1|(?:free\s+)?practice\s*(?:1|one)|(?:opening|first)\s+(?:formula\s*(?:1|one)\s+)?(?:free\s+)?practice)\b',
	'FP2': r'\b(?:fp\s*2|(?:free\s+)?practice\s*(?:2|two)|second\s+(?:formula\s*(?:1|one)\s+)?(?:free\s+)?practice)\b',
	'FP3': r'\b(?:fp\s*3|(?:free\s+)?practice\s*(?:3|three)|third\s+(?:formula\s*(?:1|one)\s+)?(?:free\s+)?practice)\b',
	'SS': r'\b(?:sprint\s+(?:qualifying|shootout))\b',
	'SR': r'\b(?:sprint\s+race)\b',
	'QUAL': r'(?<!sprint )\bqualifying\b',
	'RACE': r'(?<!sprint )\b(?:the\s+)?race\b',
}
_REJECT = re.compile(r'\b(?:highlights?|recaps?|previews?|look\s+ahead|upcoming|next|interviews?|analysis|review|replays?|repeats?|encore|reruns?)\b', re.I)
_TITLE_EVENT = re.compile(r'^(?:live\s+)?(.+?)\s+f1\s+(?:gp|grand prix)$')
_DESCRIPTION_EVENTS = (
	re.compile(r'\b(?:opening|first|second|third)\s+formula\s*(?:1|one)\s+practice\s+session\s+of\s+(.+?)\s+grand prix\s+weekend\b'),
	re.compile(r"\bstarting grid for (?:sunday|saturday)'s\s+(.+?)\s+grand prix\s+is set with f1 qualifying\b"),
	re.compile(r"^it's lights out for the\s+(.+?)\s+grand prix\."),
)
_WALL_CLOCK = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\Z')


def _text(value):
	if not isinstance(value, str) or len(value) > 2000 or any(ord(char) < 32 and char not in '\t\r\n' for char in value): return ''
	return ' '.join(''.join(char for char in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(char)).split())


def _number(value):
	if isinstance(value, bool): return None
	try:
		if isinstance(value, str) and not value.isdecimal(): return None
		result = float(value)
		return result if math.isfinite(result) and result.is_integer() else None
	except (ValueError, TypeError, OverflowError): return None


def _guide_clock(programme):
	"""Return the provider wall-clock offset and epoch for a valid guide start."""
	if not isinstance(programme, dict): return None
	start, wall = _number(programme.get('start_timestamp')), programme.get('start')
	if start is None or not 0 < start < 253402300800 or not isinstance(wall, str) or not _WALL_CLOCK.fullmatch(wall): return None
	try: wall_epoch = calendar.timegm(datetime.strptime(wall, '%Y-%m-%d %H:%M:%S').timetuple())
	except (ValueError, OverflowError): return None
	offset = wall_epoch - int(start)
	if abs(offset) > 14 * 3600 or offset % 900: return None
	return int(start), offset


def match_archives(fixtures, programmes, retention_days, now):
	"""Return fixture ID to matching archive entries from one provider channel.

	Each programme must contain decoded title/description, `start` in the
	provider's YYYY-MM-DD HH:MM:SS wall clock, numeric start_timestamp and
	stop_timestamp, and has_archive equal to 1. `retention_days` is the
	provider-confirmed per-channel limit and `now` is Unix time. Results contain
	only start, start_timestamp and stop_timestamp; the caller retains the
	provider/channel identity and must recheck availability before playback.
	Only explicitly recognized title/description formats are accepted. Each fixture
	must provide other_event_aliases from the rest of the season. A distinct nearby guide entry must corroborate the wall-clock offset; this
	does not prove the provider uses that clock for timeshift. Generic
	descriptions and reruns are deliberately excluded.
	"""
	if not isinstance(fixtures, list) or not isinstance(programmes, list): return {}
	try:
		if isinstance(retention_days, bool) or isinstance(now, bool): return {}
		retention, now = float(retention_days), float(now)
		if not math.isfinite(retention) or not 0 < retention <= 365 or not math.isfinite(now): return {}
	except (TypeError, ValueError, OverflowError): return {}
	valid = []
	for fixture in fixtures:
		if not isinstance(fixture, dict) or fixture.get('status') != 'Finished': continue
		identity, event, code, date = fixture.get('id'), fixture.get('event_id'), fixture.get('session_code'), fixture.get('date')
		if not isinstance(identity, str) or not identity or not isinstance(event, str) or not event or code not in _SESSION_PATTERNS or not isinstance(date, str): continue
		try:
			when = datetime.fromisoformat(date.replace('Z', '+00:00'))
			if when.utcoffset() is None: continue
			start = when.timestamp()
		except (ValueError, TypeError, OverflowError, OSError): continue
		aliases = fixture.get('aliases')
		if not isinstance(aliases, list): aliases = []
		aliases = {_text(alias) for alias in aliases + [fixture.get('event')]}
		aliases = {alias for alias in aliases if len(alias) >= 4}
		other = fixture.get('other_event_aliases')
		if not isinstance(other, list): continue
		other = {_text(alias) for alias in other}
		other = {alias for alias in other if len(alias) >= 4 and alias not in aliases}
		if aliases: valid.append((identity, event, code, start, aliases, when.year, other))
	if len({item[0] for item in valid}) != len(valid): return {}
	owners = {}
	for _, event, _, _, aliases, _, _ in valid:
		for alias in aliases: owners.setdefault(alias, set()).add(event)
	clocks = {}
	for programme in programmes:
		clock = _guide_clock(programme)
		if clock: clocks.setdefault(clock[1], set()).add(clock[0])
	clocks = {offset: sorted(starts) for offset, starts in clocks.items()}
	result = {}
	for programme in programmes:
		if not isinstance(programme, dict) or programme.get('has_archive') not in (1, '1') or isinstance(programme.get('has_archive'), bool): continue
		start, stop = _number(programme.get('start_timestamp')), _number(programme.get('stop_timestamp'))
		wall = programme.get('start')
		if start is None or stop is None or not isinstance(wall, str) or not _WALL_CLOCK.fullmatch(wall): continue
		clock = _guide_clock(programme)
		if clock is None: continue
		starts = clocks.get(clock[1], [])
		index = bisect_left(starts, clock[0])
		if not ((index > 0 and clock[0] - starts[index - 1] <= 3 * 86400) or (index + 1 < len(starts) and starts[index + 1] - clock[0] <= 3 * 86400)): continue
		if not 0 < start < stop <= now or stop - start > 8 * 3600 or start < now - retention * 86400: continue
		title, description = _text(programme.get('title')), _text(programme.get('description', ''))
		if not title or _REJECT.search(title + ' ' + description): continue
		text = title + ' ' + description
		title_event = _TITLE_EVENT.fullmatch(title)
		description_events = [match.group(1).strip() for pattern in _DESCRIPTION_EVENTS for match in pattern.finditer(description)]
		if not title_event or len(description_events) != 1: continue
		codes = {code for code, pattern in _SESSION_PATTERNS.items() if re.search(pattern, text)}
		if _DESCRIPTION_EVENTS[2].search(description): codes.add('RACE')
		if len(codes) != 1: continue
		years = {int(year) for year in re.findall(r'\b20\d{2}\b', text)}
		candidates = []
		for identity, event, code, fixture_start, aliases, year, other in valid:
			if code not in codes or not start <= fixture_start < stop or (years and years != {year}): continue
			if title_event.group(1).strip() not in aliases or description_events[0] not in aliases: continue
			if any(re.search(r'(?<!\w)' + re.escape(alias).replace(r'\ ', r'\s+') + r'(?!\w)', text) for alias in other): continue
			if any(owners[alias] == {event} and re.search(r'(?<!\w)' + re.escape(alias).replace(r'\ ', r'\s+') + r'(?!\w)', text) for alias in aliases): candidates.append((identity, event))
		if len(candidates) != 1: continue
		identity, event = candidates[0]
		if any(event not in event_ids and re.search(r'(?<!\w)' + re.escape(alias).replace(r'\ ', r'\s+') + r'(?!\w)', text) for alias, event_ids in owners.items()): continue
		result.setdefault(identity, []).append({'start': wall, 'start_timestamp': int(start), 'stop_timestamp': int(stop)})
	return result
