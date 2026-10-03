from time import time_ns
from modules import kodi_utils


MY_LIST_SCHEMA = """CREATE TABLE IF NOT EXISTS my_list (
	mediatype TEXT NOT NULL CHECK (mediatype IN ('movie', 'tvshow')), tmdb_id INTEGER NOT NULL CHECK (tmdb_id > 0),
	title TEXT NOT NULL, saved_at INTEGER NOT NULL, PRIMARY KEY (mediatype, tmdb_id))"""


def normalize_identity(mediatype, tmdb_id, tvshow_id=None):
	mediatype = str(mediatype or '').strip().lower()
	if mediatype in ('episode', 'season'):
		mediatype, tmdb_id = 'tvshow', tvshow_id if tvshow_id is not None else tmdb_id
	if mediatype == 'tv_show': mediatype = 'tvshow'
	if mediatype not in ('movie', 'tvshow'): raise ValueError('My List requires a movie or TV show')
	if isinstance(tmdb_id, bool) or not str(tmdb_id or '').strip().isdigit(): raise ValueError('My List requires a positive TMDb ID')
	tmdb_id = int(str(tmdb_id).strip())
	if tmdb_id <= 0: raise ValueError('My List requires a positive TMDb ID')
	return mediatype, tmdb_id


class MyList:
	def __init__(self, database_file=None):
		self.database_file = database_file if database_file is not None else kodi_utils.watched_db

	def _connect(self):
		dbcon = kodi_utils.database_connect(self.database_file, timeout=1, isolation_level=None)
		try: dbcon.execute(MY_LIST_SCHEMA)
		except Exception:
			dbcon.close()
			raise
		return dbcon

	def contains(self, mediatype, tmdb_id, tvshow_id=None):
		identity = normalize_identity(mediatype, tmdb_id, tvshow_id)
		dbcon = self._connect()
		try: return dbcon.execute('SELECT 1 FROM my_list WHERE mediatype = ? AND tmdb_id = ?', identity).fetchone() is not None
		finally: dbcon.close()

	def add(self, mediatype, tmdb_id, title='', tvshow_id=None):
		mediatype, tmdb_id = normalize_identity(mediatype, tmdb_id, tvshow_id)
		title = str(title or '').strip() or '%s %s' % ('Movie' if mediatype == 'movie' else 'TV Show', tmdb_id)
		dbcon = self._connect()
		try:
			dbcon.execute('INSERT OR IGNORE INTO my_list (mediatype, tmdb_id, title, saved_at) VALUES (?, ?, ?, ?)', (mediatype, tmdb_id, title, time_ns()))
			return True
		finally: dbcon.close()

	def remove(self, mediatype, tmdb_id, tvshow_id=None):
		identity = normalize_identity(mediatype, tmdb_id, tvshow_id)
		dbcon = self._connect()
		try:
			dbcon.execute('DELETE FROM my_list WHERE mediatype = ? AND tmdb_id = ?', identity)
			return True
		finally: dbcon.close()

	def items(self, mediatype, page=1, limit=None):
		mediatype, _ = normalize_identity(mediatype, 1)
		page = max(1, int(page))
		if limit is not None: limit = max(1, int(limit))
		dbcon = self._connect()
		try:
			count = dbcon.execute('SELECT COUNT(*) FROM my_list WHERE mediatype = ?', (mediatype,)).fetchone()[0]
			total_pages = max(1, (count + limit - 1) // limit) if limit else 1
			page = min(page, total_pages)
			query = 'SELECT tmdb_id, title, saved_at FROM my_list WHERE mediatype = ? ORDER BY saved_at DESC, tmdb_id ASC'
			params = (mediatype,)
			if limit:
				query += ' LIMIT ? OFFSET ?'
				params += (limit, (page - 1) * limit)
			return [{'media_id': str(item[0]), 'title': item[1], 'saved_at': item[2]} for item in dbcon.execute(query, params)], total_pages
		finally: dbcon.close()


def get_my_list(_watched_info, mediatype, page_no):
	from modules import settings
	return MyList().items(mediatype, page_no, settings.page_limit() if settings.paginate() else None)
