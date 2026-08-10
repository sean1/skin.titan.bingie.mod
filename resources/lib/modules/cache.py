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
packages_path = kodi_utils.packages_path
database_connect = kodi_utils.database_connect

def _filter_items_without_markers(items, markers, serialize):
	filtered = []
	for item in items:
		serialized = serialize(item).lower()
		if all(marker not in serialized for marker in markers): filtered.append(item)
	return filtered

def _trunc(file):
	try:
		with open(kodi_utils.translate_path(file), 'w') as f: pass
	except: return 0
	return 1

def check_databases():
	if not kodi_utils.path_exists(databases_path): kodi_utils.make_directory(databases_path)
	migration_count = 0
	if kodi_utils.get_setting('database.merge_status') != 'true': migration_count += _trunc(maincache_db)
	dbcon = database_connect(maincache_db) # Main Cache
	dbcon.execute("""CREATE TABLE IF NOT EXISTS maincache (id TEXT UNIQUE, expires INTEGER, data TEXT)""")
	dbcon.close()
	dbcon = database_connect(navigator_db) # Navigator
	dbcon.execute("""CREATE TABLE IF NOT EXISTS navigator (list_name TEXT, list_type TEXT, list_contents TEXT, UNIQUE (list_name, list_type))""")
	dbcon.close()
	if kodi_utils.get_setting('database.merge_status') != 'true': migration_count += _trunc(metacache_db)
	dbcon = database_connect(metacache_db) # Meta Cache
	dbcon.execute("""CREATE TABLE IF NOT EXISTS metadata (db_type TEXT not null, tmdb_id TEXT not null, imdb_id TEXT, tvdb_id TEXT, expires INTEGER, meta TEXT, UNIQUE (db_type, tmdb_id))""")
	dbcon.execute("""CREATE TABLE IF NOT EXISTS season_metadata (tmdb_id TEXT not null UNIQUE, expires INTEGER, meta TEXT)""")
	dbcon.execute("""CREATE TABLE IF NOT EXISTS function_cache (string_id TEXT not null, expires INTEGER, data TEXT)""")
	dbcon.execute("""CREATE TABLE IF NOT EXISTS metadata_claims (db_type TEXT not null, id_type TEXT not null, media_id TEXT not null, owner TEXT not null, claimed_at INTEGER not null, UNIQUE (db_type, id_type, media_id))""")
	dbcon.execute("""CREATE INDEX IF NOT EXISTS pov_select_id_media ON metadata (tmdb_id, db_type)""")
	dbcon.close()
	dbcon = database_connect(views_db) # Views
	dbcon.execute("""CREATE TABLE IF NOT EXISTS views (view_type TEXT, view_id TEXT, UNIQUE (view_type))""")
	dbcon.close()
	dbcon = database_connect(debridcache_db) # Debrid Cache
	dbcon.execute("""CREATE TABLE IF NOT EXISTS debrid_data (hash TEXT not null, debrid TEXT not null, cached TEXT, expires INTEGER, UNIQUE (hash, debrid))""")
	dbcon.close()
	if kodi_utils.get_setting('database.merge_status') != 'true': migration_count += _trunc(external_db)
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
	if kodi_utils.get_setting('database.merge_status') != 'true' and migration_count == 3:
		kodi_utils.set_setting('database.merge_status', 'true')
	remove_old_databases()

def purge_removed_service_data():
	setting_id = 'migration.removed_services.6_08_03'
	if kodi_utils.get_setting(setting_id) == 'true': return True
	try:
		from caches.main_cache import clear_main_cache_property
		dbcon = database_connect(maincache_db)
		dbcur = dbcon.cursor()
		query = """SELECT id FROM maincache WHERE id LIKE ? OR id LIKE ? OR id LIKE ? OR id LIKE ? OR id LIKE ? OR id IN (?, ?)"""
		params = (
			'pov_lite_pm_%', 'pov_lite_ad_%', 'pov_lite_tb_%', 'pov_lite_oc_%', 'pov_lite_EASYNEWS_SEARCH_%',
			'torbox_usenet_queries', 'easynews_video_queries'
		)
		dbcur.execute(query, params)
		cache_keys = [str(i[0]) for i in dbcur.fetchall()]
		if cache_keys: dbcur.executemany("""DELETE FROM maincache WHERE id = ?""", [(i,) for i in cache_keys])
		dbcon.commit()
		dbcon.close()
		for item in cache_keys: clear_main_cache_property(item)

		dbcon = database_connect(debridcache_db)
		dbcon.execute("""DELETE FROM debrid_data WHERE debrid IN (?, ?, ?, ?)""", ('pm', 'ad', 'tb', 'oc'))
		dbcon.commit()
		dbcon.close()
		kodi_utils.set_setting(setting_id, 'true')
		return True
	except Exception as e:
		kodi_utils.logger('purge_removed_service_data error', str(e))
		return False

def purge_history_data():
	setting_id = 'migration.removed_history.6_08_38'
	if kodi_utils.get_setting(setting_id) == 'true': return True
	try:
		from caches.main_cache import clear_main_cache_property
		search_keys = ('movie_queries', 'tvshow_queries', 'people_queries', 'tmdb_collections_queries')
		dbcon = database_connect(maincache_db)
		dbcur = dbcon.cursor()
		query = """SELECT id FROM maincache WHERE id LIKE ? OR id LIKE ? OR id IN (?, ?, ?, ?)"""
		params = ('pov_lite_discover_movie_%', 'pov_lite_discover_tvshow_%') + search_keys
		dbcur.execute(query, params)
		cache_keys = set(search_keys)
		cache_keys.update(str(i[0]) for i in dbcur.fetchall())
		dbcur.executemany("""DELETE FROM maincache WHERE id = ?""", [(i,) for i in cache_keys])
		dbcon.commit()
		dbcon.close()
		for item in cache_keys: clear_main_cache_property(item)
		kodi_utils.set_setting(setting_id, 'true')
		return True
	except Exception as e:
		kodi_utils.logger('purge_history_data error', str(e))
		return False

def purge_removed_list_data():
	try:
		from caches.main_cache import clear_main_cache_property
		dbcon = database_connect(maincache_db)
		dbcur = dbcon.cursor()
		dbcur.execute("""SELECT id FROM maincache WHERE id LIKE ?""", ('tmdblist_%',))
		cache_keys = [str(i[0]) for i in dbcur.fetchall()]
		if cache_keys: dbcur.executemany("""DELETE FROM maincache WHERE id = ?""", [(i,) for i in cache_keys])
		dbcon.commit()
		dbcon.close()
		for item in cache_keys: clear_main_cache_property(item)

		from caches.navigator_cache import navigator_cache
		markers = (
			'mdblist', 'mdbl_', 'tmdblist', 'tmdb_manager', 'build_tmdb_list', 'tmdb_watchlist', 'tmdb_favorites', 'tmdb_recommendations',
			'navigator.downloads', 'navigator.folder_navigator', '"mode": "downloader"', '"mode": "browser_image"',
			'navigator.favorites', 'favorites_choice', 'favorites_movies', 'favorites_tvshows', 'favorites.png'
		)
		rows = navigator_cache.dbcur.execute('SELECT list_name, list_type, list_contents FROM navigator').fetchall()
		for list_name, list_type, list_contents in rows:
			items = navigator_cache.jsloads(list_contents)
			filtered = _filter_items_without_markers(items, markers, navigator_cache.jsdumps)
			changed = len(filtered) != len(items)
			if list_name == 'RootList' and list_type == 'default' and not any(item.get('action') == 'dropped_tvshows' for item in filtered):
				from modules.menu_lists import root_list
				dropped_item = next(item for item in root_list if item.get('action') == 'dropped_tvshows')
				filtered.insert(min(6, len(filtered)), dropped_item)
				changed = True
			if changed: navigator_cache.set_list(list_name, list_type, filtered)
		return True
	except Exception as e:
		kodi_utils.logger('purge_removed_list_data error', str(e))
		return False

def purge_removed_personal_trakt_data():
	setting_id = 'migration.removed_personal_trakt.6_08_09'
	if kodi_utils.get_setting(setting_id) == 'true': return True
	try:
		dbcon = database_connect(trakt_db)
		dbcon.execute("""DELETE FROM trakt_data""")
		dbcon.execute("""DROP TABLE IF EXISTS watched_status""")
		dbcon.execute("""DROP TABLE IF EXISTS progress""")
		dbcon.commit()
		dbcon.close()

		from caches.navigator_cache import navigator_cache
		markers = (
			'trakt_manager', 'trakt_account_info', 'get_trakt_lists', 'trakt_collection', 'trakt_watchlist',
			'trakt_favorites', 'trakt_recommendations', 'trakt_droplist', 'build_my_calendar_trakt',
			'build_my_anime_calendar', 'navigator.trakt_lists', 'navigator.trakt_watchlists',
			'navigator.trakt_collections', 'navigator.trakt_favorites', 'navigator.trakt_recommendations',
			'"list_type": "my_lists"', '"list_type": "liked_lists"'
		)
		rows = navigator_cache.dbcur.execute('SELECT list_name, list_type, list_contents FROM navigator').fetchall()
		for list_name, list_type, list_contents in rows:
			items = navigator_cache.jsloads(list_contents)
			filtered = _filter_items_without_markers(items, markers, navigator_cache.jsdumps)
			changed = len(filtered) != len(items)
			if list_type == 'default':
				for item in filtered:
					if item.get('mode') == 'navigator.my_content' and item.get('name') != 'Trakt Lists':
						item['name'] = 'Trakt Lists'
						changed = True
			if changed: navigator_cache.set_list(list_name, list_type, filtered)
		kodi_utils.clear_property('script.trakt.ids')
		kodi_utils.clear_property('pov_lite_traktmonitor_first_run')
		kodi_utils.set_setting(setting_id, 'true')
		return True
	except Exception as e:
		kodi_utils.logger('purge_removed_personal_trakt_data error', str(e))
		return False

def migrate_tmdb_native_lists():
	setting_id = 'migration.tmdb_native_lists.2_03_03'
	if kodi_utils.get_setting(setting_id) == 'true': return True
	try:
		from caches.navigator_cache import navigator_cache
		from modules.menu_lists import main_menus
		deprecated_actions = {
			'AnimeList', 'tmdb_oscar_winners', 'tmdb_movies_blockbusters', 'tmdb_movies_latest_releases', 'tmdb_movies_premieres',
			'tmdb_tv_premieres', 'tmdb_tv_upcoming', 'trakt_movies_trending', 'trakt_movies_trending_recent', 'trakt_movies_most_watched',
			'trakt_tv_trending', 'trakt_tv_trending_recent', 'trakt_tv_most_watched', 'trakt_moviesanime_trending', 'trakt_moviesanime_most_watched',
			'trakt_tvanime_trending', 'trakt_tvanime_most_watched'
		}
		deprecated_modes = {'navigator.my_content', 'build_anime_calendar', 'navigator.anime_genres', 'navigator.anime_years'}
		rows = navigator_cache.dbcur.execute('SELECT list_name, list_type, list_contents FROM navigator').fetchall()
		for list_name, list_type, list_contents in rows:
			if list_name == 'AnimeList': continue
			items = navigator_cache.jsloads(list_contents) or []
			if list_type == 'default' and list_name in main_menus:
				filtered = main_menus[list_name]
			else:
				filtered = [
					item for item in items
					if item.get('action') not in deprecated_actions
					and not str(item.get('action', '')).startswith(('tmdb_moviesanime_', 'tmdb_tvanime_'))
					and item.get('mode') not in deprecated_modes
				]
			if filtered != items: navigator_cache.set_list(list_name, list_type, filtered)
		navigator_cache.dbcur.execute('DELETE FROM navigator WHERE list_name = ?', ('AnimeList',))
		for list_type in ('default', 'edited'): navigator_cache.delete_memory_cache('AnimeList', list_type)
		kodi_utils.set_setting(setting_id, 'true')
		return True
	except Exception as e:
		kodi_utils.logger('migrate_tmdb_native_lists error', str(e))
		return False

def remove_old_databases():
	# The embedded POV databases share the skin's profile directory. Unknown files
	# in that directory belong to the skin unless an explicit legacy target says otherwise.
	return

def remove_old_packages():
	files = kodi_utils.list_dirs(packages_path)[1]
	for item in files:
		if '.pov' in item and item.endswith('zip'):
			try: kodi_utils.delete_file(packages_path + item)
			except: pass

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
	remove_old_databases()
	remove_old_packages()
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
