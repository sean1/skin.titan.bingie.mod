from modules import kodi_utils
from time import monotonic_ns


SMARTPLAY_SCHEMA = """CREATE TABLE IF NOT EXISTS smartplay_cursor (tmdb_id INTEGER PRIMARY KEY, season INTEGER NOT NULL CHECK (season > 0), episode INTEGER NOT NULL CHECK (episode > 0))"""
GET_CURSOR = """SELECT season, episode FROM smartplay_cursor WHERE tmdb_id = ?"""
ADVANCE_CURSOR = """
	INSERT INTO smartplay_cursor (tmdb_id, season, episode) VALUES (?, ?, ?)
	ON CONFLICT(tmdb_id) DO UPDATE SET season = excluded.season, episode = excluded.episode
	WHERE excluded.season > smartplay_cursor.season OR (excluded.season = smartplay_cursor.season AND excluded.episode > smartplay_cursor.episode)
"""
DELETE_PROGRESS = """DELETE FROM progress WHERE db_type = 'episode' AND media_id = ? AND season = ? AND episode = ?"""


def _positive_int(value, field):
	if isinstance(value, bool): raise ValueError('%s must be a positive integer' % field)
	try: normalized = int(value)
	except (TypeError, ValueError): raise ValueError('%s must be a positive integer' % field)
	if normalized <= 0 or isinstance(value, float) and value != normalized: raise ValueError('%s must be a positive integer' % field)
	return normalized


def _nonnegative_int(value, field):
	if isinstance(value, bool): raise ValueError('%s must be a non-negative integer' % field)
	try: normalized = int(value)
	except (TypeError, ValueError): raise ValueError('%s must be a non-negative integer' % field)
	if normalized < 0 or isinstance(value, float) and value != normalized: raise ValueError('%s must be a non-negative integer' % field)
	return normalized


def _identity(tmdb_id, season=None, episode=None):
	values = [_positive_int(tmdb_id, 'tmdb_id')]
	if season is not None: values.append(_positive_int(season, 'season'))
	if episode is not None: values.append(_positive_int(episode, 'episode'))
	return tuple(values)


def _connect():
	return kodi_utils.database_connect(kodi_utils.watched_db, timeout=1, isolation_level=None)


def initialize():
	dbcon = _connect()
	try: dbcon.execute(SMARTPLAY_SCHEMA)
	finally: dbcon.close()


def lookup(tmdb_id):
	(tmdb_id,) = _identity(tmdb_id)
	dbcon = _connect()
	try: return dbcon.execute(GET_CURSOR, (tmdb_id,)).fetchone()
	finally: dbcon.close()


def _advance(dbcur, tmdb_id, season, episode):
	dbcur.execute(ADVANCE_CURSOR, (tmdb_id, season, episode))
	return dbcur.rowcount > 0


def _delete_progress(dbcur, tmdb_id, season, episode):
	dbcur.execute(DELETE_PROGRESS, (str(tmdb_id), season, episode))
	return dbcur.rowcount > 0


def advance(tmdb_id, season, episode):
	tmdb_id, season, episode = _identity(tmdb_id, season, episode)
	dbcon = _connect()
	try:
		dbcur = dbcon.cursor()
		dbcur.execute('BEGIN IMMEDIATE')
		advanced = _advance(dbcur, tmdb_id, season, episode)
		dbcon.commit()
		return advanced
	except Exception:
		dbcon.rollback()
		raise
	finally: dbcon.close()


def advance_and_delete_progress(tmdb_id, season, episode, completed_season, completed_episode):
	tmdb_id, season, episode = _identity(tmdb_id, season, episode)
	_, completed_season, completed_episode = _identity(tmdb_id, completed_season, completed_episode)
	dbcon = _connect()
	try:
		dbcur = dbcon.cursor()
		dbcur.execute('BEGIN IMMEDIATE')
		advanced = _advance(dbcur, tmdb_id, season, episode)
		deleted = _delete_progress(dbcur, tmdb_id, completed_season, completed_episode)
		dbcon.commit()
		return advanced, deleted
	except Exception:
		dbcon.rollback()
		raise
	finally: dbcon.close()


def delete_episode_progress(tmdb_id, season, episode):
	tmdb_id = _positive_int(tmdb_id, 'tmdb_id')
	season = _nonnegative_int(season, 'season')
	episode = _positive_int(episode, 'episode')
	dbcon = _connect()
	try:
		dbcur = dbcon.cursor()
		dbcur.execute('BEGIN IMMEDIATE')
		deleted = _delete_progress(dbcur, tmdb_id, season, episode)
		dbcon.commit()
		return deleted
	except Exception:
		dbcon.rollback()
		raise
	finally: dbcon.close()


def complete_episode(tmdb_id, season, episode):
	season = _nonnegative_int(season, 'season')
	if season == 0: advanced, deleted = False, delete_episode_progress(tmdb_id, season, episode)
	else: advanced, deleted = advance_and_delete_progress(tmdb_id, season, episode, season, episode)
	if deleted:
		if kodi_utils.external_browse(): kodi_utils.set_property('BingieProgressRefreshEpisode', str(monotonic_ns()))
		else: kodi_utils.container_refresh()
	return advanced, deleted
