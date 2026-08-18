from time import monotonic_ns
from modules import kodi_utils


DELETE_BM = 'DELETE FROM progress WHERE db_type = ? AND media_id = ? AND season = ? AND episode = ?'


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
	if not deleted or refresh == 'false': return
	if not kodi_utils.external_browse(): return kodi_utils.container_refresh()
	if refresh == 'progress': return kodi_utils.set_property('BingieProgressRefresh%s' % mediatype.title(), str(monotonic_ns()))
	if refresh == 'true': return kodi_utils.widget_refresh()
