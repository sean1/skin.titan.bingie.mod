from modules import kodi_utils
# logger = kodi_utils.logger

ls = kodi_utils.local_string
navigator_db = kodi_utils.navigator_db
watched_db = kodi_utils.watched_db
views_db = kodi_utils.views_db
trakt_db = kodi_utils.trakt_db
maincache_db = kodi_utils.maincache_db
metacache_db = kodi_utils.metacache_db
debridcache_db = kodi_utils.debridcache_db
external_db = kodi_utils.external_db
databases_path = kodi_utils.databases_path
database_connect = kodi_utils.database_connect

def check_databases():
	if not kodi_utils.path_exists(databases_path): kodi_utils.make_directory(databases_path)
	dbcon = database_connect(maincache_db) # Main Cache
	dbcon.execute("""CREATE TABLE IF NOT EXISTS maincache (id TEXT UNIQUE, expires INTEGER, data TEXT)""")
	dbcon.close()
	dbcon = database_connect(navigator_db) # Navigator
	dbcon.execute("""CREATE TABLE IF NOT EXISTS navigator (list_name TEXT, list_type TEXT, list_contents TEXT, UNIQUE (list_name, list_type))""")
	dbcon.close()
	dbcon = database_connect(metacache_db) # Meta Cache
	dbcon.execute("""CREATE TABLE IF NOT EXISTS metadata (db_type TEXT not null, tmdb_id TEXT not null, imdb_id TEXT, tvdb_id TEXT, expires INTEGER, meta TEXT, UNIQUE (db_type, tmdb_id))""")
	dbcon.execute("""CREATE TABLE IF NOT EXISTS season_metadata (tmdb_id TEXT not null UNIQUE, expires INTEGER, meta TEXT)""")
	dbcon.execute("""CREATE TABLE IF NOT EXISTS function_cache (string_id TEXT not null, expires INTEGER, data TEXT)""")
	dbcon.execute("""CREATE TABLE IF NOT EXISTS metadata_claims (db_type TEXT not null, id_type TEXT not null, media_id TEXT not null, owner TEXT not null, claimed_at INTEGER not null, UNIQUE (db_type, id_type, media_id))""")
	dbcon.execute("""CREATE INDEX IF NOT EXISTS pov_select_imdb_media ON metadata (db_type, imdb_id, tmdb_id)""")
	dbcon.execute("""CREATE INDEX IF NOT EXISTS pov_select_tvdb_media ON metadata (db_type, tvdb_id, tmdb_id)""")
	dbcon.close()
	dbcon = database_connect(views_db) # Views
	dbcon.execute("""CREATE TABLE IF NOT EXISTS views (view_type TEXT, view_id TEXT, UNIQUE (view_type))""")
	dbcon.close()
	dbcon = database_connect(debridcache_db) # Debrid Cache
	dbcon.execute("""CREATE TABLE IF NOT EXISTS debrid_data (hash TEXT not null, debrid TEXT not null, cached TEXT, expires INTEGER, UNIQUE (hash, debrid))""")
	dbcon.close()
	dbcon = database_connect(external_db) # External Providers Cache
	dbcon.execute("""CREATE TABLE IF NOT EXISTS results_data (provider TEXT, db_type TEXT, tmdb_id TEXT, title TEXT, year INTEGER, season TEXT, episode TEXT, expires INTEGER, results TEXT, UNIQUE (provider, db_type, tmdb_id, title, year, season, episode))""")
	dbcon.close()
	watched_schema = (
	"""CREATE TABLE IF NOT EXISTS watched_status (db_type TEXT, media_id TEXT, season INTEGER, episode INTEGER, last_played TEXT, title TEXT, UNIQUE (db_type, media_id, season, episode))""",
	"""CREATE TABLE IF NOT EXISTS progress (db_type TEXT, media_id TEXT, season INTEGER, episode INTEGER, resume_point TEXT, curr_time TEXT, last_played TEXT, resume_id INTEGER, title TEXT, UNIQUE (db_type, media_id, season, episode))""",
	"""CREATE INDEX IF NOT EXISTS pov_ws_in_progress_episodes ON watched_status (db_type, media_id, season DESC, episode DESC)"""
	)
	dbcon = database_connect(watched_db) # Watched Status
	for i in watched_schema: dbcon.execute(i)
	dbcon.execute("""CREATE TABLE IF NOT EXISTS dropped (db_type TEXT, tmdb_id TEXT, title TEXT, UNIQUE (db_type, tmdb_id))""")
	dbcon.close()
	dbcon = database_connect(trakt_db) # Trakt
	dbcon.execute("""CREATE TABLE IF NOT EXISTS trakt_data (id TEXT UNIQUE, data TEXT)""")
	dbcon.close()
def normalize_menu_data():
	try:
		from caches.navigator_cache import navigator_cache
		hidden_actions = {'watched_movies', 'watched_tvshows'}
		rows = navigator_cache.dbcur.execute('SELECT list_name, list_type, list_contents FROM navigator').fetchall()
		for list_name, list_type, list_contents in rows:
			items = navigator_cache.jsloads(list_contents)
			filtered = [item for item in items if item.get('action') not in hidden_actions]
			changed = len(filtered) != len(items)
			if list_name == 'RootList' and list_type == 'default' and not any(item.get('action') == 'dropped_tvshows' for item in filtered):
				from modules.menu_lists import root_list
				dropped_item = next(item for item in root_list if item.get('action') == 'dropped_tvshows')
				filtered.insert(min(6, len(filtered)), dropped_item)
				changed = True
			if changed: navigator_cache.set_list(list_name, list_type, filtered)
		return True
	except Exception as e:
		kodi_utils.logger('normalize_menu_data error', str(e))
		return False

def clean_databases(current_time=None, database_check=True, silent=False):
	if database_check: check_databases()
	if not current_time: from datetime import datetime
	current_time = current_time or int(datetime.now().timestamp())
	for db, tables in (
		(maincache_db, ('maincache',)),
		(external_db, ('results_data',)),
		(debridcache_db, ('debrid_data',)),
		(metacache_db, ('function_cache', 'season_metadata', 'metadata'))
	): purge_database(db, tables, current_time)
	dbcon = database_connect(watched_db, isolation_level=None)
	dbcon.execute("""VACUUM""")
	dbcon.close()
	limit_metacache_database()
	if not silent: kodi_utils.notification(32576, 1500)

def purge_database(db, tables, expiry):
	if isinstance(tables, str): tables = (tables,)
	dbcon = database_connect(db)
	dbcur = dbcon.cursor()
	dbcur.execute("""PRAGMA synchronous = OFF""")
	dbcur.execute("""PRAGMA journal_mode = OFF""")
	for table in tables: dbcur.execute("""DELETE FROM %s WHERE expires <= ?""" % table, (expiry,))
	dbcon.commit()
	dbcur.execute("""VACUUM""")
	dbcon.close()

def limit_metacache_database(max_size=50):
	with kodi_utils.open_file(metacache_db) as f: fsize = f.size()
	size = round(float(fsize)/1048576, 1)
	if size < max_size: return
	dbcon = database_connect(metacache_db)
	dbcur = dbcon.cursor()
	dbcur.execute("""PRAGMA synchronous = OFF""")
	dbcur.execute("""PRAGMA journal_mode = OFF""")
	dbcur.execute("""DELETE FROM metadata WHERE ROWID IN (SELECT ROWID FROM metadata ORDER BY ROWID DESC LIMIT -1 OFFSET 4000)""")
	dbcur.execute("""DELETE FROM function_cache WHERE ROWID IN (SELECT ROWID FROM function_cache ORDER BY ROWID DESC LIMIT -1 OFFSET 100)""")
	dbcur.execute("""DELETE FROM season_metadata WHERE ROWID IN (SELECT ROWID FROM season_metadata ORDER BY ROWID DESC LIMIT -1 OFFSET 100)""")
	dbcon.commit()
	dbcur.execute("""VACUUM""")

def clear_cache(cache_type, silent=False):
	def _confirm():
		return silent or kodi_utils.confirm_dialog()
	success = True
	if cache_type == 'meta':
		if not _confirm(): return
		from caches.meta_cache import MetaCache
		MetaCache().delete_all()
	elif cache_type == 'internal_scrapers':
		if not _confirm(): return
		clear_cache('rd_cloud', silent=True)
	elif cache_type == 'external_scrapers':
		if not _confirm(): return
		from caches.providers_cache import ExternalProvidersCache
		from caches.debrid_cache import DebridCache
		data = ExternalProvidersCache().delete_cache()
		debrid_cache = DebridCache().clear_database()
		success = (data, debrid_cache) == ('success', 'success')
	elif cache_type == 'trakt':
		if not _confirm(): return
		from caches.trakt_cache import clear_all_trakt_cache_data
		success = clear_all_trakt_cache_data()
	elif cache_type == 'imdb':
		if not _confirm(): return
		from indexers.imdb_api import clear_imdb_cache
		success = clear_imdb_cache()
	elif cache_type == 'rd_cloud':
		if not _confirm(): return
		from debrids.real_debrid_api import RealDebridAPI
		success = RealDebridAPI().clear_cache()
	else: # 'list'
		if not _confirm(): return
		from caches.main_cache import MainCache
		MainCache().delete_all_lists()
	if not silent and success: kodi_utils.notification(32576, 1500)

def clear_all_cache():
	if not kodi_utils.confirm_dialog(): return
	line = '[CR]%s: [B]%s %s[/B]'
	caches = (
		('external_scrapers', ls(32118)), ('internal_scrapers', ls(32096)),
		('trakt', ls(32037)), ('imdb', ls(32064)), ('list', ls(32815)), ('meta', ls(32527))
	)
	len_caches = len(caches)
	kodi_utils.progressDialog.create('BINGIE Lite', '')
	for count, (cache_type, cache_label) in enumerate(caches, 1):
		try:
			if kodi_utils.progressDialog.iscanceled(): break
			args = int(count / len_caches * 100), line % (ls(32816), cache_label, ls(32524))
			kodi_utils.progressDialog.update(*args)
			clear_cache(cache_type, silent=True)
			kodi_utils.sleep(200)
		except: kodi_utils.notification(32574, 1500)
	kodi_utils.progressDialog.close()
