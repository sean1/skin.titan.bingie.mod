from datetime import datetime, timedelta
from caches import BaseCache, maincache_db
from caches.window_property_cache import WindowPropertyCache
# from modules.kodi_utils import logger

BASE_GET = 'SELECT data, expires FROM maincache WHERE id = ? AND expires > ?'
BASE_SET = 'INSERT OR REPLACE INTO maincache VALUES (?, ?, ?)'
BASE_DELETE = 'DELETE FROM maincache WHERE id = ?'
LIKE_SELECT, LIKE_SELECT_ADD = 'SELECT id FROM maincache WHERE %s', 'id LIKE ?'
MEMORY_CACHE_KEY = 'pov_lite_maincache_%s'
memory_cache = WindowPropertyCache('pov_lite_maincache_registry', 48)

def clear_main_cache_property(string):
	memory_cache.delete(MEMORY_CACHE_KEY % string)

class MainCache(BaseCache):
	db_file = maincache_db

	def __init__(self):
		self.dbcon, self.dbcur = None, None

	def _ensure_db(self):
		if self.dbcon is None: super().__init__()

	def get(self, string):
		result = None
		try:
			current_time = self._get_timestamp(datetime.now())
			cache_data = self.get_memory_cache(string, current_time)
			if cache_data is not None: return cache_data
			self._ensure_db()
			self.dbcur.execute(BASE_GET, (string, current_time))
			data = self.dbcur.fetchone()
			if not data: return result
			result, expiry = self.jsloads(data[0]), data[1]
			if result is not None: self.set_memory_cache(result, string, expiry)
		except: pass
		return result

	def set(self, string, data, expiration):
		if data is None: return
		try:
			self._ensure_db()
			expires = self._get_timestamp(datetime.now() + expiration)
			self.dbcur.execute(BASE_SET, (string, int(expires), self.jsdumps(data)))
			self.set_memory_cache(data, string, int(expires))
		except: pass

	def get_memory_cache(self, string, current_time):
		result = None
		try:
			prop_string = MEMORY_CACHE_KEY % string
			cachedata = memory_cache.get(prop_string)
			if cachedata:
				cachedata = self.jsloads(cachedata)
				if cachedata[0] > current_time: result = cachedata[1]
				else: memory_cache.delete(prop_string)
		except:
			try: memory_cache.delete(MEMORY_CACHE_KEY % string)
			except: pass
		return result

	def set_memory_cache(self, data, string, expires):
		if data is None: return
		try:
			cachedata = (expires, data)
			cachedata = self.jsdumps(cachedata)
			memory_cache.set(MEMORY_CACHE_KEY % string, cachedata)
		except: pass

	def delete(self, string, dbcon=None):
		try:
			self._ensure_db()
			self.dbcur.execute(BASE_DELETE, (string,))
			self.delete_memory_cache(string)
		except: pass

	def delete_memory_cache(self, string):
		clear_main_cache_property(string)

	def delete_all_lists(self):
		from modules.meta_lists import media_lists
		self._ensure_db()
		items = ' OR '.join(LIKE_SELECT_ADD for i in media_lists)
		self.dbcur.execute(LIKE_SELECT % items, media_lists)
		results = self.dbcur.fetchall()
		try:
			for item in results:
				try:
					self.dbcur.execute(BASE_DELETE, (str(item[0]),))
					self.delete_memory_cache(str(item[0]))
				except: pass
			self.dbcur.execute("""VACUUM""")
		except: pass

def cache_object(function, string, url, expiration=24, json=False):
	maincache = MainCache()
	cache = maincache.get(string)
	if cache is not None: return cache
	if not isinstance(url, list): url = (url,)
	if json: result = function(*url).json()
	else: result = function(*url)
	if isinstance(expiration, (int, float)): expiration = timedelta(hours=expiration)
	if result is not None: maincache.set(string, result, expiration)
	return result
