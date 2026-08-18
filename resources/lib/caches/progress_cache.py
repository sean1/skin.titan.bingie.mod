from datetime import datetime
from time import monotonic_ns
from modules import kodi_utils


DELETE_BM = 'DELETE FROM progress WHERE db_type = ? AND media_id = ? AND season = ? AND episode = ?'
SET_BM = 'INSERT OR REPLACE INTO progress VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)'


def _refresh_progress(mediatype, refresh):
	if refresh == 'false': return
	if not kodi_utils.external_browse(): return kodi_utils.container_refresh()
	if refresh == 'progress': return kodi_utils.set_property('BingieProgressRefresh%s' % mediatype.title(), str(monotonic_ns()))
	if refresh == 'true': return kodi_utils.widget_refresh()


def set_bookmark(mediatype, tmdb_id, curr_time, total_time, title, season='', episode='', refresh='true'):
	dbcon = None
	try:
		adjusted_current_time = float(curr_time) - 5
		resume_point = round(adjusted_current_time / float(total_time) * 100, 1)
		last_played = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
		dbcon = kodi_utils.database_connect(kodi_utils.watched_db, timeout=1, isolation_level=None)
		dbcon.execute(SET_BM, (mediatype, tmdb_id, season, episode, str(resume_point), str(curr_time), last_played, 0, title))
	except:
		kodi_utils.notification(32574)
		return False
	finally:
		if dbcon: dbcon.close()
	_refresh_progress(mediatype, refresh)
	return True


def erase_bookmark(mediatype, tmdb_id, season='', episode='', refresh='false'):
	dbcon = None
	try:
		if mediatype == 'episode': season, episode = int(season), int(episode)
		dbcon = kodi_utils.database_connect(kodi_utils.watched_db, timeout=1, isolation_level=None)
		dbcur = dbcon.cursor()
		dbcur.execute(DELETE_BM, (mediatype, tmdb_id, season, episode))
		deleted = dbcur.rowcount > 0
	except:
		kodi_utils.notification(32574)
		return
	finally:
		if dbcon: dbcon.close()
	if deleted: _refresh_progress(mediatype, refresh)
	return deleted
