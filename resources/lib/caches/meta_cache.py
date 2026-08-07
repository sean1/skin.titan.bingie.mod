from datetime import datetime, timedelta
from time import monotonic, sleep, time
from uuid import uuid4
from caches import BaseCache, metacache_db
from caches.window_property_cache import WindowPropertyCache
from modules import kodi_utils
# from modules.kodi_utils import logger

GET_MOVIE_SHOW = 'SELECT meta, expires FROM metadata WHERE db_type = ? AND %s = ? and expires > ?'
GET_SEASON = 'SELECT meta, expires FROM season_metadata WHERE tmdb_id = ? AND expires > ?'
GET_FUNCTION = 'SELECT data, expires FROM function_cache WHERE string_id = ? AND expires > ?'
GET_ALL = 'SELECT db_type, tmdb_id, meta FROM metadata'
SET_MOVIE_SHOW = 'INSERT OR REPLACE INTO metadata VALUES (?, ?, ?, ?, ?, ?)'
SET_SEASON = 'INSERT OR REPLACE INTO season_metadata VALUES (?, ?, ?)'
SET_FUNCTION = 'INSERT INTO function_cache VALUES (?, ?, ?)'
DELETE_MOVIE_SHOW = 'DELETE FROM metadata WHERE db_type = ? AND %s = ?'
DELETE_SEASON = 'DELETE FROM season_metadata WHERE tmdb_id = ?'
DELETE_SEASONS = 'DELETE FROM season_metadata WHERE tmdb_id LIKE ?'
DELETE_FUNCTION = 'DELETE FROM function_cache WHERE string_id = ?'
DELETE_ALL = 'DELETE FROM %s'
DELETE_STALE_CLAIM = 'DELETE FROM metadata_claims WHERE db_type = ? AND id_type = ? AND media_id = ? AND claimed_at <= ?'
SET_CLAIM = 'INSERT OR IGNORE INTO metadata_claims VALUES (?, ?, ?, ?, ?)'
GET_CLAIM = 'SELECT owner, claimed_at FROM metadata_claims WHERE db_type = ? AND id_type = ? AND media_id = ?'
RENEW_CLAIM = 'UPDATE metadata_claims SET claimed_at = ? WHERE db_type = ? AND id_type = ? AND media_id = ? AND owner = ?'
DELETE_CLAIM = 'DELETE FROM metadata_claims WHERE db_type = ? AND id_type = ? AND media_id = ? AND owner = ?'
movie_show, id_types = ('movie', 'tvshow'), ('tmdb_id', 'imdb_id', 'tvdb_id')
prop_dict = {'meta': 'pov_lite_meta_%s_%s_%s', 'meta_season': 'pov_lite_meta_season_%s'}
memory_cache = WindowPropertyCache('pov_lite_metacache_registry', 32)
CLAIM_ACQUIRED, CLAIM_HELD, CLAIM_BUSY, CLAIM_UNAVAILABLE = range(4)
CLAIM_WAIT_SECONDS, CLAIM_STALE_SECONDS, CLAIM_POLL_SECONDS = 18.0, 20, 0.1

class MetaCache(BaseCache):
	db_file = metacache_db

	def __init__(self):
		self.dbcon, self.dbcur = None, None

	def _ensure_db(self):
		if self.dbcon is None: super().__init__()

	def _set_PRAGMAS(self):
		self.dbcur.executescript("""
			PRAGMA busy_timeout = 5000;
			PRAGMA synchronous = OFF;
			PRAGMA journal_mode = OFF;
			PRAGMA mmap_size = 268435456;
		""")

	def get(self, mediatype, id_type, media_id):
		meta = None
		try:
			media_id = str(media_id)
			current_time = self._get_timestamp(datetime.now())
			cache_data = self.get_memory_cache(mediatype, id_type, media_id, current_time)
			if cache_data is not None: return cache_data
			self._ensure_db()
			if mediatype in movie_show:
				self.dbcur.execute(GET_MOVIE_SHOW % id_type, (mediatype, media_id, current_time))
			else: self.dbcur.execute(GET_SEASON, (media_id, current_time))
			data = self.dbcur.fetchone()
			if not data: return meta
			meta, expiry = self.jsloads(data[0]), data[1]
			if meta is not None: self.set_memory_cache(mediatype, id_type, meta, expiry, media_id)
		except: pass
		return meta

	def set(self, mediatype, id_type, meta, expiration=30, tmdb_id=None):
		if meta is None: return
		try:
			self._ensure_db()
			expires = datetime.now() + timedelta(days=expiration)
			expires = self._get_timestamp(datetime.combine(expires, datetime.min.time()))
			if mediatype in movie_show:
				media_id, command = str(meta[id_type]), SET_MOVIE_SHOW
				args = mediatype, str(meta['tmdb_id']), meta['imdb_id'], str(meta['tvdb_id']), expires
			else:
				media_id, command = str(tmdb_id), SET_SEASON
				args = media_id, expires
			self.dbcur.execute(command, (*args, self.jsdumps(meta)))
		except: return
		self.set_memory_cache(mediatype, id_type, meta, expires, media_id)

	def get_or_claim(self, mediatype, id_type, media_id, wait_timeout=CLAIM_WAIT_SECONDS, stale_timeout=CLAIM_STALE_SECONDS):
		media_id, owner = str(media_id), uuid4().hex
		deadline = monotonic() + wait_timeout
		if self._abort_requested(): return self.get(mediatype, id_type, media_id), None, False
		while True:
			claim_status = self._claim(mediatype, id_type, media_id, owner, stale_timeout)
			if claim_status == CLAIM_UNAVAILABLE:
				if self._abort_requested(): return self.get(mediatype, id_type, media_id), None, False
				return None, None, True
			if claim_status == CLAIM_ACQUIRED:
				if self._abort_requested():
					self.release_claim(mediatype, id_type, media_id, owner)
					return self.get(mediatype, id_type, media_id), None, False
				meta = self.get(mediatype, id_type, media_id)
				if meta is None: return None, owner, True
				self.release_claim(mediatype, id_type, media_id, owner)
				return meta, None, False
			meta = self.get(mediatype, id_type, media_id)
			if meta is not None: return meta, None, False
			remaining = deadline - monotonic()
			if remaining <= 0: return self.get(mediatype, id_type, media_id), None, False
			delay = min(CLAIM_POLL_SECONDS, remaining)
			if self._wait_for_abort(delay): return self.get(mediatype, id_type, media_id), None, False

	def _claim(self, mediatype, id_type, media_id, owner, stale_timeout):
		try:
			self._ensure_db()
			now = int(time())
			stale_before = now - stale_timeout
			self.dbcur.execute(GET_CLAIM, (mediatype, id_type, media_id))
			claim = self.dbcur.fetchone()
			if claim and claim[1] > stale_before: return CLAIM_ACQUIRED if claim[0] == owner else CLAIM_HELD
			if claim: self.dbcur.execute(DELETE_STALE_CLAIM, (mediatype, id_type, media_id, stale_before))
			self.dbcur.execute(SET_CLAIM, (mediatype, id_type, media_id, owner, now))
			self.dbcur.execute(GET_CLAIM, (mediatype, id_type, media_id))
			claim = self.dbcur.fetchone()
			return CLAIM_ACQUIRED if claim and claim[0] == owner else CLAIM_HELD
		except Exception as exc:
			message = str(exc).lower()
			return CLAIM_BUSY if 'locked' in message or 'busy' in message else CLAIM_UNAVAILABLE

	def release_claim(self, mediatype, id_type, media_id, owner):
		if not owner: return
		self._ensure_db()
		for _ in range(3):
			try:
				self.dbcur.execute(DELETE_CLAIM, (mediatype, id_type, str(media_id), owner))
				return
			except Exception as exc:
				message = str(exc).lower()
				if 'locked' not in message and 'busy' not in message: return
				sleep(0.05)

	def renew_claim(self, mediatype, id_type, media_id, owner):
		if not owner: return True
		try:
			self._ensure_db()
			self.dbcur.execute(RENEW_CLAIM, (int(time()), mediatype, id_type, str(media_id), owner))
			return self.dbcur.rowcount == 1
		except: return False

	def _abort_requested(self):
		try: return kodi_utils.monitor.abortRequested()
		except: return False

	def _wait_for_abort(self, delay):
		try: return kodi_utils.monitor.waitForAbort(delay)
		except:
			sleep(delay)
			return False

	def delete(self, mediatype, id_type, media_id, meta=None, dbcon=None):
		try:
			self._ensure_db()
			media_id = str(media_id)
			if mediatype in movie_show:
				self.dbcur.execute(DELETE_MOVIE_SHOW % id_type, (mediatype, media_id))
				for item in id_types: self.delete_memory_cache(mediatype, item, meta[item])
				if mediatype == 'tvshow': self.dbcur.execute(DELETE_SEASONS, (media_id + '%',))
			else:
				self.dbcur.execute(DELETE_SEASON, (media_id,))
				self.delete_memory_cache(mediatype, id_type, media_id)
		except: pass

	def get_memory_cache(self, mediatype, id_type, media_id, current_time):
		result = None
		try:
			media_id = str(media_id)
			if mediatype in movie_show: prop_string = prop_dict.get('meta') % (mediatype, id_type, media_id)
			else: prop_string = prop_dict.get('meta_season') % media_id
			cachedata = memory_cache.get(prop_string)
			if cachedata:
				cachedata = self.jsloads(cachedata)
				if cachedata[0] > current_time: result = cachedata[1]
				else: memory_cache.delete(prop_string)
		except:
			try: memory_cache.delete(prop_string)
			except: pass
		return result

	def set_memory_cache(self, mediatype, id_type, meta, expires, media_id):
		if meta is None: return
		try:
			media_id = str(media_id)
			if mediatype in movie_show:
				cachedata, prop_string = (expires, meta), prop_dict.get('meta') % (mediatype, id_type, media_id)
			else: cachedata, prop_string = (expires, meta), prop_dict.get('meta_season') % media_id
			memory_cache.set(prop_string, self.jsdumps(cachedata))
		except: pass

	def delete_memory_cache(self, mediatype, id_type, media_id):
		try:
			if mediatype in movie_show: memory_cache.delete(prop_dict.get('meta') % (mediatype, id_type, media_id))
			else: memory_cache.delete(prop_dict.get('meta_season') % media_id)
		except: pass

	def get_function(self, prop_string):
		result = None
		try:
			self._ensure_db()
			current_time = self._get_timestamp(datetime.now())
			self.dbcur.execute(GET_FUNCTION, (prop_string, current_time))
			cache_data = self.dbcur.fetchone()
			if cache_data: result = self.jsloads(cache_data[0])
		except: pass
		return result

	def set_function(self, prop_string, result, expiration):
		if result is None: return
		try:
			self._ensure_db()
			expires = self._get_timestamp(datetime.now() + expiration)
			self.dbcur.execute(SET_FUNCTION, (prop_string, expires, self.jsdumps(result)))
		except: pass

	def delete_all_seasons_memory_cache(self, media_id, total_seasons=None):
		if not total_seasons: total_seasons = 100
		for item in range(total_seasons + 1):
			memory_cache.delete('%s_%s' % (prop_dict.get('meta_season') % str(media_id), str(item)))

	def delete_all(self):
		try:
			self._ensure_db()
			self.dbcur.execute(GET_ALL)
			all_entries = self.dbcur.fetchall()
			for i in all_entries:
				try:
					mediatype, tmdb_id = str(i[0]), str(i[1])
					if mediatype == 'tvshow':
						total_seasons = self.jsloads(i[2]).get('total_seasons')
						self.delete_all_seasons_memory_cache(tmdb_id, total_seasons)
					self.delete_memory_cache(mediatype, 'tmdb_id', tmdb_id)
				except: pass
			for table in ('metadata', 'season_metadata', 'function_cache', 'metadata_claims'):
				self.dbcur.execute(DELETE_ALL % table)
			self.dbcur.execute("""VACUUM""")
		except: pass

	def prefetch(self, limit=24):
		self._ensure_db()
		command = 'SELECT db_type, tmdb_id, meta, expires FROM metadata WHERE expires > ? ORDER BY ROWID DESC LIMIT ?'
		current_time = self._get_timestamp(datetime.now())
		for db_type, tmdb_id, meta, expires in self.dbcur.execute(command, (current_time, limit)).fetchall():
			try: self.set_memory_cache(db_type, 'tmdb_id', self.jsloads(meta), expires, tmdb_id)
			except: pass
		for i in (self.dbcur, self.dbcon): i.close()
		self.dbcon, self.dbcur = None, None

def cache_function(function, prop_string, url, expiration=96, json=False):
	metacache = MetaCache()
	data = metacache.get_function(prop_string)
	if data is not None: return data
	if json: result = function(url).json()
	else: result = function(url)
	if isinstance(expiration, (int, float)): expiration = timedelta(hours=expiration)
	if result is not None: metacache.set_function(prop_string, result, expiration)
	return result
