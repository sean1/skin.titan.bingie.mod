import json
from threading import Lock
from caches import clear_property, get_property, set_property


class WindowPropertyCache:
	def __init__(self, registry_key, limit):
		self.registry_key = registry_key
		self.limit = limit
		self.lock = Lock()

	def get(self, key):
		with self.lock:
			entry = next((item for item in self._registry() if item[0] == key), None)
			if entry is None: return ''
			try:
				stored_key, value = json.loads(get_property(self._slot_key(entry[1])))
				return value if stored_key == key else ''
			except: return ''

	def set(self, key, value):
		with self.lock:
			entries = self._registry()
			entry = next((item for item in entries if item[0] == key), None)
			if entry is None:
				used_slots = {item[1] for item in entries}
				free_slot = next((slot for slot in range(self.limit) if slot not in used_slots), None)
				if free_slot is None:
					_, free_slot = entries.pop(0)
				entry = key, free_slot
			else: entries.remove(entry)
			entries.append(entry)
			set_property(self._slot_key(entry[1]), json.dumps((key, value), separators=(',', ':')))
			self._store_registry(entries)

	def delete(self, key):
		with self.lock:
			entries = self._registry()
			entry = next((item for item in entries if item[0] == key), None)
			if entry is None: return
			entries.remove(entry)
			try:
				stored_key, _ = json.loads(get_property(self._slot_key(entry[1])))
				if stored_key == key: clear_property(self._slot_key(entry[1]))
			except: pass
			self._store_registry(entries)

	def _registry(self):
		try:
			registry = get_property(self.registry_key)
			if not registry: return []
			entries, seen_keys, seen_slots = [], set(), set()
			for item in reversed(json.loads(registry)):
				if not isinstance(item, list) or len(item) != 2: continue
				key, slot = item
				if not isinstance(key, str) or not key or not isinstance(slot, int) or not 0 <= slot < self.limit: continue
				if key in seen_keys or slot in seen_slots: continue
				entries.append((key, slot))
				seen_keys.add(key)
				seen_slots.add(slot)
			return list(reversed(entries))
		except: return []

	def _store_registry(self, entries):
		if entries: set_property(self.registry_key, json.dumps(entries, separators=(',', ':')))
		else: clear_property(self.registry_key)

	def _slot_key(self, slot):
		return '%s_slot_%d' % (self.registry_key, slot)
