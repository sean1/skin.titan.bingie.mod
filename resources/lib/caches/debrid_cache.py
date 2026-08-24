from datetime import datetime, timedelta
from caches import BaseCache, debridcache_db
# from modules.kodi_utils import logger

GET_MANY = 'SELECT * FROM debrid_data WHERE hash in (%s)'
SET_MANY = """INSERT INTO debrid_data VALUES (?, ?, ?, ?) ON CONFLICT(hash, debrid) DO UPDATE SET cached = excluded.cached, expires = excluded.expires
	WHERE debrid_data.cached != 'True' OR excluded.cached = 'True'"""
REMOVE_MANY = 'DELETE FROM debrid_data WHERE hash = ? AND debrid = ? AND expires = ?'
CLEAR = 'DELETE FROM debrid_data'
CLEAR_DEBRID = 'DELETE FROM debrid_data WHERE debrid = ?'

class DebridCache(BaseCache):
	db_file = debridcache_db

	def get_many(self, hash_list):
		result = None
		try:
			if not hash_list: return result
			current_time = self._get_timestamp(datetime.now())
			self.dbcur.execute(GET_MANY % (', '.join('?' for _ in hash_list)), hash_list)
			cache_data = self.dbcur.fetchall()
			if cache_data:
				result = [i for i in cache_data if i[3] > current_time]
				expired = [i for i in cache_data if i[3] <= current_time]
				if expired: self.remove_many(expired)
				if not result: result = None
		except: pass
		return result

	def set_many(self, hash_list, debrid):
		try:
			expires = self._get_timestamp(datetime.now() + timedelta(hours=24))
			insert_list = [(i[0], debrid, i[1], expires) for i in hash_list]
			self.dbcur.executemany(SET_MANY, insert_list)
		except: pass

	def remove_many(self, old_cached_data):
		try:
			old_cached_data = [(str(i[0]), i[1], i[3]) for i in old_cached_data]
			self.dbcur.executemany(REMOVE_MANY, old_cached_data)
		except: pass

	def clear_database(self):
		try:
			self.dbcur.execute(CLEAR)
			self.dbcur.execute("""VACUUM""")
			return 'success'
		except: return 'failure'

	def clear_debrid_results(self, debrid):
		try:
			self.dbcur.execute(CLEAR_DEBRID, (debrid,))
			self.dbcur.execute("""VACUUM""")
			return True
		except: return False
