from concurrent.futures import Future, TimeoutError
from threading import Lock
from time import monotonic


EXTERNAL_PROVIDERS = (
	'bitmagnet', 'bitsearch', 'comet', 'dmm', 'mediafusion', 'nyaa',
	'piratebay', 'torrentdownload', 'torrentio', 'torrentsdb', 'torz', 'zilean'
)
CORE_EXTERNAL_PROVIDERS = frozenset(('comet', 'mediafusion', 'torrentio', 'zilean'))
CORE_CACHED_RESULT_TARGET = 8
MAX_EXTERNAL_WORKERS = 8


def split_external_providers(source_dict):
	core, fallback = [], []
	for item in source_dict:
		(core if item[0] in CORE_EXTERNAL_PROVIDERS else fallback).append(item)
	return core, fallback


def external_worker_count(source_count, debrid_count=0, exhaustive=False):
	required_workers = max(1, source_count, debrid_count)
	return required_workers if exhaustive else min(MAX_EXTERNAL_WORKERS, required_workers)


class RequestCoalescer:
	def __init__(self, grace_seconds=1.0):
		self.grace_seconds = grace_seconds
		self._entries = {}
		self._lock = Lock()

	def get(self, key, fetch, timeout):
		now = monotonic()
		with self._lock:
			self._entries = {
				entry_key: entry for entry_key, entry in self._entries.items()
				if not entry[0].done() or entry[1] > now
			}
			entry = self._entries.get(key)
			if entry is None:
				future, owner = Future(), True
				self._entries[key] = [future, float('inf')]
			else:
				future, owner = entry[0], False
		if owner:
			try: result = fetch()
			except Exception: result = []
			future.set_result(result)
			with self._lock:
				entry = self._entries.get(key)
				if entry and entry[0] is future: entry[1] = monotonic() + self.grace_seconds
			return result
		try: return future.result(timeout=timeout)
		except TimeoutError: return []
