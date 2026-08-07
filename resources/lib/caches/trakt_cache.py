import json
from modules.kodi_utils import trakt_db, database_connect

timeout = 20
BASE_DELETE = 'DELETE FROM trakt_data'
TC_BASE_GET = 'SELECT data FROM trakt_data WHERE id = ?'
TC_BASE_SET = 'INSERT OR REPLACE INTO trakt_data (id, data) VALUES (?, ?)'

class TraktCache:
	def __init__(self):
		self.dbcon = database_connect(trakt_db, timeout=timeout, isolation_level=None)
		self.dbcur = self.dbcon.cursor()
		self.dbcur.execute("""PRAGMA synchronous = OFF""")
		self.dbcur.execute("""PRAGMA journal_mode = OFF""")
		self.dbcur.execute("""PRAGMA mmap_size = 268435456""")

def cache_trakt_object(function, string, url):
	dbcur = TraktCache().dbcur
	dbcur.execute(TC_BASE_GET, (string,))
	cached_data = dbcur.fetchone()
	try:
		if cached_data: return json.loads(cached_data[0])
	except: pass
	result = function(url)
	dbcur.execute(TC_BASE_SET, (string, json.dumps(result)))
	return result

def clear_all_trakt_cache_data():
	try:
		dbcur = TraktCache().dbcur
		dbcur.execute(BASE_DELETE)
		dbcur.execute("""VACUUM""")
		return True
	except: return False
