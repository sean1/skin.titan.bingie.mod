"""Refresh saved Live TV browse catalogs without blocking their directory."""
import json
import time
import uuid
from threading import Thread
from queue import Empty, SimpleQueue

from modules import kodi_utils as ku
from modules.livetv_catalog import FRESH_SECONDS, _key, catalog_lock_supported, saved_catalog

_REQUESTS = 'LiveTV.Browse.RefreshRequests'
_STATUS = 'LiveTV.Browse.RefreshStatus'
_MAX_REQUESTS = 16
_COOLDOWN = 60
_TIMEOUT = 45


def _requests():
	try:
		value = json.loads(ku.get_property(_REQUESTS) or '[]')
		return value if isinstance(value, list) else []
	except (TypeError, ValueError): return []


def request(account, action, params):
	key = _key(account, action, params)
	entries = [entry for entry in _requests() if isinstance(entry, dict) and entry.get('key') != key]
	entries.append({'id': uuid.uuid4().hex, 'key': key, 'action': action, 'params': params})
	ku.set_property(_REQUESTS, json.dumps(entries[-_MAX_REQUESTS:], separators=(',', ':')))


def _statuses():
	try:
		value = json.loads(ku.get_property(_STATUS) or '{}')
		return value if isinstance(value, dict) else {}
	except (TypeError, ValueError): return {}


def status(account, action, params):
	return _statuses().get(_key(account, action, params), '')


def _set_status(key, value):
	statuses = _statuses()
	statuses.pop(key, None)
	statuses[key] = value
	ku.set_property(_STATUS, json.dumps(dict(list(statuses.items())[-_MAX_REQUESTS:]), separators=(',', ':')))


class BrowseRefresh:
	def __init__(self):
		self.worker = None
		self.results = SimpleQueue()
		self.seen = []
		self.attempted = {}

	def tick(self):
		while True:
			try: key, result = self.results.get_nowait()
			except Empty: break
			_set_status(key, result)
		if self.worker is not None and self.worker.is_alive(): return
		entries = [entry for entry in _requests() if isinstance(entry, dict) and entry.get('id') not in self.seen]
		if not entries: return
		from menus import livetv
		primary = livetv._load()
		accounts = [account for _, account in livetv._sports_accounts(primary)]
		if primary: accounts.append(primary)
		if not accounts: return
		profile = ku.translate_path(ku.profile_path)
		if not catalog_lock_supported(profile): return
		now = time.monotonic()
		for entry in entries:
			if not isinstance(entry, dict): continue
			identity, action, params, key = (entry.get(name) for name in ('id', 'action', 'params', 'key'))
			if not isinstance(identity, str) or identity in self.seen or action not in ('get_live_categories', 'get_live_streams') or not isinstance(params, dict): continue
			if params and (action != 'get_live_streams' or set(params) != {'category_id'} or not isinstance(params['category_id'], str)): continue
			self.seen = (self.seen + [identity])[-64:]
			account = next((value for value in accounts if key == _key(value, action, params)), None)
			if account is None or now - self.attempted.get(key, float('-inf')) < _COOLDOWN: continue
			self.attempted = {name: when for name, when in self.attempted.items() if now - when < _COOLDOWN}
			self.attempted[key] = now
			self.worker = Thread(target=self._work, args=(account, action, params, key, self.results), daemon=True)
			self.worker.start()
			return

	@staticmethod
	def _work(account, action, params, key, results):
		from menus import livetv
		try: livetv._catalog(account, action, notify_stale=False, _deadline=time.monotonic() + _TIMEOUT, **params)
		except Exception: pass
		try:
			saved = saved_catalog(account, action, ku.translate_path(ku.profile_path), **params)
			fresh = bool(saved and 0 <= time.time() - saved[1] < FRESH_SECONDS)
		except (OSError, ValueError, TypeError, OverflowError): fresh = False
		results.put((key, 'ready' if fresh else 'unavailable'))
