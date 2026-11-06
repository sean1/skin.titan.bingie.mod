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

def get_cached(string):
	try:
		row = TraktCache().dbcur.execute(TC_BASE_GET, (string,)).fetchone()
		return json.loads(row[0]) if row else None
	except Exception: return None

def set_cached(string, value):
	try: TraktCache().dbcur.execute(TC_BASE_SET, (string, json.dumps(value)))
	except Exception: pass

def clear_all_trakt_cache_data():
	try:
		dbcur = TraktCache().dbcur
		dbcur.execute(BASE_DELETE)
		dbcur.execute("""VACUUM""")
		return True
	except: return False
