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
PROVIDER_OUTCOME_VERSION = 1


class ProviderOutcome:
	VALID_STATUSES = frozenset(('success', 'empty', 'failed', 'partial'))

	def __init__(self, sources=None, status=None):
		self.sources = list(sources or [])
		self.status = status or ('success' if self.sources else 'empty')
		if self.status not in self.VALID_STATUSES: raise ValueError('Invalid provider outcome: %s' % self.status)

	@property
	def cacheable(self):
		return self.status in ('success', 'empty')

	def serialize(self):
		return {'provider_outcome': PROVIDER_OUTCOME_VERSION, 'status': self.status, 'sources': self.sources}

	@classmethod
	def from_provider(cls, value, instance=None):
		if isinstance(value, cls): return value
		if instance is not None:
			if getattr(instance, 'scrape_failed', False): return cls(value, 'partial' if value else 'failed')
			if getattr(instance, 'scrape_partial', False): return cls(value, 'partial')
		return cls(value)

	@classmethod
	def from_cache(cls, value):
		if isinstance(value, dict) and value.get('provider_outcome') == PROVIDER_OUTCOME_VERSION:
			status, sources = value.get('status'), value.get('sources')
			if status not in ('success', 'empty') or not isinstance(sources, list): return None
			return cls(sources, status)
		if isinstance(value, list) and value: return cls(value, 'success')
		return None


def split_external_providers(source_dict):
	core, fallback = [], []
	for item in source_dict:
		(core if item[0] in CORE_EXTERNAL_PROVIDERS else fallback).append(item)
	return core, fallback


def external_worker_count(source_count, debrid_count=0, exhaustive=False):
	required_workers = max(1, source_count, debrid_count)
	return required_workers if exhaustive else min(MAX_EXTERNAL_WORKERS, required_workers)


class RequestCoalescer:
	def __init__(self, grace_seconds=1.0, raise_errors=False):
		self.grace_seconds = grace_seconds
		self.raise_errors = raise_errors
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
			try:
				result = fetch()
				future.set_result(result)
			except Exception as exc:
				if self.raise_errors: future.set_exception(exc)
				else:
					result = []
					future.set_result(result)
			with self._lock:
				entry = self._entries.get(key)
				if entry and entry[0] is future: entry[1] = monotonic() + self.grace_seconds
			return future.result()
		try: return future.result(timeout=timeout)
		except TimeoutError:
			if self.raise_errors: raise
			return []
