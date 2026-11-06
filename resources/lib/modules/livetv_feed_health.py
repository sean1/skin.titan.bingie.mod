"""Keep short-lived, account-scoped playback outcomes for feed labels."""
import json
import math
import re
import time

from modules import kodi_utils as ku


_PROPERTY = 'LiveTV.FeedRecentResults'
_TTL = 5 * 60
_LIMIT = 100
_FEED_ID = re.compile(r'(?:[A-Za-z0-9_-]+:)?[0-9]+\Z')


def _read(accounts):
	if not isinstance(accounts, str) or not re.fullmatch(r'[0-9a-f]{64}', accounts): return {}
	try:
		data = json.loads(ku.get_property(_PROPERTY) or '{}')
		if not isinstance(data, dict) or data.get('accounts') != accounts or not isinstance(data.get('feeds'), dict): return {}
		now = time.time()
		return {feed_id: entry for feed_id, entry in data['feeds'].items() if isinstance(feed_id, str) and _FEED_ID.fullmatch(feed_id)
			and isinstance(entry, dict) and entry.get('success') in (True, False) and isinstance(entry.get('success'), bool)
			and isinstance(entry.get('time'), (int, float)) and not isinstance(entry.get('time'), bool)
			and math.isfinite(entry['time']) and 0 <= now - entry['time'] < _TTL}
	except (TypeError, ValueError, OverflowError): return {}


def record(accounts, feed_id, success):
	"""Record an observed playback outcome; the service is the sole writer."""
	if not isinstance(accounts, str) or not re.fullmatch(r'[0-9a-f]{64}', accounts) or not isinstance(feed_id, str) or not _FEED_ID.fullmatch(feed_id) or not isinstance(success, bool): return
	if feed_id.startswith('primary:'): feed_id = feed_id[8:]
	feeds = _read(accounts)
	feeds.pop('primary:' + feed_id, None)
	feeds[feed_id] = {'success': success, 'time': time.time()}
	feeds = dict(sorted(feeds.items(), key=lambda item: item[1]['time'], reverse=True)[:_LIMIT])
	ku.set_property(_PROPERTY, json.dumps({'accounts': accounts, 'feeds': feeds}, separators=(',', ':')))


def recent(accounts):
	"""Return recent outcome labels without changing playback eligibility."""
	results = {}
	for feed_id, entry in sorted(_read(accounts).items(), key=lambda item: item[1]['time']):
		if feed_id.startswith('primary:'): feed_id = feed_id[8:]
		label = 'Played recently' if entry['success'] else 'Failed recently'
		results[feed_id] = label
		if feed_id.isdecimal(): results['primary:' + feed_id] = label
	return results
