import json
import re

from modules import kodi_utils


CACHE_SETTINGS = {
	'filecache.buffermode': 4,
	'filecache.readfactor': 0,
	'filecache.chunksize': 262144,
	'smb.chunksize': 256,
}
CACHE_TIERS = (32, 48, 64, 96, 128, 192, 256, 384, 512)


def _memory_from_proc(path='/proc/meminfo'):
	try:
		with open(path, encoding='utf-8') as memory_file:
			values = {match.group(1): int(match.group(2)) / 1024 for line in memory_file if (match := re.match(r'^(MemTotal|MemAvailable):\s+(\d+)\s+kB$', line))}
		return values.get('MemAvailable'), values.get('MemTotal')
	except (OSError, ValueError):
		return None, None


def _memory_label_mb(label):
	match = re.search(r'([\d.,]+)\s*(KB|MB|GB)', label or '', re.IGNORECASE)
	if not match: return None
	value = float(match.group(1).replace(',', ''))
	return value * {'KB': 1 / 1024, 'MB': 1, 'GB': 1024}[match.group(2).upper()]


def memory_mb():
	available, total = _memory_from_proc()
	if available is not None and total is not None: return available, total
	return _memory_label_mb(kodi_utils.get_infolabel('System.Memory(free)')), _memory_label_mb(kodi_utils.get_infolabel('System.Memory(total)'))


def select_cache_mb(available_mb, total_mb):
	if available_mb is None or total_mb is None or available_mb <= 0 or total_mb <= 0: return None
	if available_mb < 256: free_cap = 32
	elif available_mb < 384: free_cap = 64
	elif available_mb < 768: free_cap = 128
	elif available_mb < 1536: free_cap = 256
	else: free_cap = 512
	limit = min(free_cap, total_mb / 8)
	return max((tier for tier in CACHE_TIERS if tier <= limit), default=32)


def _rpc(method, params):
	request = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
	try: return json.loads(kodi_utils.execJSONRPC(request))
	except (TypeError, ValueError): return {}


def tune():
	available_mb, total_mb = memory_mb()
	cache_mb = select_cache_mb(available_mb, total_mb)
	if cache_mb is None:
		kodi_utils.logger('BINGIE streaming cache', 'Memory information unavailable; keeping Kodi cache settings')
		return False
	targets = {**CACHE_SETTINGS, 'filecache.memorysize': cache_mb}
	changed = []
	for setting, value in targets.items():
		current = _rpc('Settings.GetSettingValue', {'setting': setting}).get('result', {}).get('value')
		if current == value: continue
		result = _rpc('Settings.SetSettingValue', {'setting': setting, 'value': value})
		if result.get('result') == 'OK': changed.append('%s=%s' % (setting, value))
	if changed: kodi_utils.logger('BINGIE streaming cache', 'Applied %s (available %.0f MB, total %.0f MB)' % (', '.join(changed), available_mb, total_mb))
	return bool(changed)
