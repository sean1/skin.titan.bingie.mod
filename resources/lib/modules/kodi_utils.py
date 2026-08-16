import json
import sqlite3 as database
from threading import Lock
from urllib.parse import urlencode, urlparse, parse_qsl
import xbmc, xbmcgui, xbmcplugin, xbmcvfs
from xbmcaddon import Addon
from modules.source_search import EXTERNAL_PROVIDERS

addon_object, window, execJSONRPC = Addon(), xbmcgui.Window(10000), xbmc.executeJSONRPC
player, xbmc_player, monitor, xbmc_monitor = xbmc.Player(), xbmc.Player, xbmc.Monitor(), xbmc.Monitor
dialog, progressDialog, progressDialogBG = xbmcgui.Dialog(), xbmcgui.DialogProgress(), xbmcgui.DialogProgressBG()
get_addoninfo, get_infolabel, get_visibility = addon_object.getAddonInfo, xbmc.getInfoLabel, xbmc.getCondVisibility
current_addon_id = get_addoninfo('id')
addon_path, profile_path = (get_addoninfo(i).rstrip('/\\') + '/' for i in ('path', 'profile'))
window_xml_info_action, window_xml_dialog = xbmcgui.ACTION_SHOW_INFO, xbmcgui.WindowXMLDialog
window_xml_closing_actions = (xbmcgui.ACTION_PARENT_DIR, xbmcgui.ACTION_PREVIOUS_MENU, xbmcgui.ACTION_STOP, xbmcgui.ACTION_NAV_BACK)
window_xml_selection_actions = (xbmcgui.ACTION_SELECT_ITEM, xbmcgui.ACTION_MOUSE_START)
window_xml_context_actions = (xbmcgui.ACTION_CONTEXT_MENU, xbmcgui.ACTION_MOUSE_RIGHT_CLICK, xbmcgui.ACTION_MOUSE_LONG_CLICK)
window_xml_left_action, window_xml_right_action = xbmcgui.ACTION_MOVE_LEFT, xbmcgui.ACTION_MOVE_RIGHT
window_xml_up_action, window_xml_down_action = xbmcgui.ACTION_MOVE_UP, xbmcgui.ACTION_MOVE_DOWN

navigator_db   = profile_path + 'navigator.db'
watched_db     = profile_path + 'watched.db'
views_db       = profile_path + 'views.db'
trakt_db       = profile_path + 'traktcache.db'
maincache_db   = profile_path + 'maincache.db'
metacache_db   = profile_path + 'metacache.db'
debridcache_db = profile_path + 'debridcache.db'
external_db    = profile_path + 'providerscache.db'
persisted_settings_db = profile_path + 'persisted_settings.db'
scrapers_path  = addon_path + 'resources/lib/scrapers/'
databases_path = profile_path
indicators_dict = {0: watched_db}

def logger(heading, function):
	xbmc.log('>> %s <<: %s' % (heading, function), 1)

def argv1():
	try: return __import__('sys').argv[1]
	except: return '-1'

def parsed_query(url):
	try: return dict(parse_qsl(urlparse(url).query))
	except: return dict()

def database_connect(file, **kwargs):
	return database.connect(translate_path(file), **kwargs)

def media_path(*args):
	path = addon_path + 'resources/skins/Default/media/'
	return '%s%s' % (path, '/'.join(args)) if args else path

def get_property(prop):
	return window.getProperty(prop)

def set_property(prop, value):
	return window.setProperty(prop, value)

def clear_property(prop):
	return window.clearProperty(prop)

def addon(addon_id=None):
	return addon_object if addon_id is None else Addon(id=addon_id)

def addon_installed(addon_id):
	return get_visibility('System.AddonIsEnabled(%s)' % addon_id)

def add_item(handle, url, listitem, isFolder):
	xbmcplugin.addDirectoryItem(handle, url, listitem, isFolder)

def add_items(handle, item_list):
	xbmcplugin.addDirectoryItems(handle, item_list)

def set_content(handle, content):
	xbmcplugin.setContent(handle, content)

def set_category(handle, category):
	xbmcplugin.setPluginCategory(handle, category)

def set_sort_method(handle, method):
	if method == 'episodes': sort_method = xbmcplugin.SORT_METHOD_EPISODE
	elif method == 'files': sort_method = xbmcplugin.SORT_METHOD_FILE
	elif method == 'label': sort_method = xbmcplugin.SORT_METHOD_LABEL_IGNORE_THE#label
	else: sort_method = xbmcplugin.SORT_METHOD_UNSORTED
	xbmcplugin.addSortMethod(handle, sort_method)

def end_directory(handle, cacheToDisc=None):
	if cacheToDisc is None: cacheToDisc = get_property('pov_lite_kodi_menu_cache') == 'true'
	xbmcplugin.endOfDirectory(handle, cacheToDisc=cacheToDisc)

def set_resolvedurl(handle, item):
	xbmcplugin.setResolvedUrl(handle, True, item)

def make_cast_list(cast=None):
	if not cast: return []
	return [xbmc.Actor(**actor) for actor in cast]

def make_playlist(_type='video'):
	return xbmc.PlayList(xbmc.PLAYLIST_VIDEO) if _type == 'video' else xbmc.PlayList(xbmc.PLAYLIST_MUSIC)

def convert_language(lang, format='long'):
	return xbmc.convertLanguage(lang, xbmc.ISO_639_2 if format == 'long' else xbmc.ISO_639_1)

def supported_media():
	return xbmc.getSupportedMedia('video')

def translate_path(path):
	return xbmcvfs.translatePath(path)

def path_exists(path):
	return xbmcvfs.exists(path)

def make_directory(path):
	xbmcvfs.mkdir(path)

def make_directorys(path):
	xbmcvfs.mkdirs(path)

def open_file(file, mode='r'):
	return xbmcvfs.File(file, mode)

def delete_file(_file):
	xbmcvfs.delete(_file)

def rename_file(old, new):
	xbmcvfs.rename(old, new)

def list_dirs(location):
	return xbmcvfs.listdir(location)

def make_listitem():
	return xbmcgui.ListItem(offscreen=True)

def local_string(string):
	try: _string = int(string)
	except: return string
	try: _string = str(addon_object.getLocalizedString(_string))
	except: _string = addon_object.getLocalizedString(_string)
	return _string or string

def sleep(time):
	return xbmc.sleep(time)

def execute_builtin(command):
	return xbmc.executebuiltin(command)

def get_kodi_version():
	return int(get_infolabel('System.BuildVersion')[0:2])

def skin_location():
	return get_addoninfo('path')

def current_skin():
	return xbmc.getSkinDir()

def current_window_id():
	return xbmcgui.Window(xbmcgui.getCurrentWindowId())

def get_video_database_path():
	version = {19: '119', 20: '121', 21: '131', 22: '146'}[get_kodi_version()]
	return 'special://profile/Database/MyVideos%s.db' % version

def show_busy_dialog():
	return execute_builtin('ActivateWindow(busydialognocancel)')

def hide_busy_dialog():
	execute_builtin('Dialog.Close(busydialognocancel)')
	execute_builtin('Dialog.Close(busydialog)')

def close_all_dialog():
	execute_builtin('Dialog.Close(all,true)')

def container_content():
	return get_infolabel('Container.Content')

def external_browse():
	container = '%s %s' % (get_infolabel('Container.PluginName'), get_infolabel('Container.FolderPath'))
	return current_addon_id not in container

def widget_refresh():
	return execute_builtin('UpdateLibrary(video,special://skin/foo)')

def container_refresh():
	return execute_builtin('Container.Refresh')

def ok_dialog(heading=None, text='', highlight='dodgerblue', ok_label=local_string(32839), top_space=True):
	heading = heading or get_addoninfo('name')
	if isinstance(heading, int): heading = local_string(heading)
	if isinstance(text, int): text = local_string(text)
	if not text: top_space, text = True, local_string(32760)
	if top_space: text = '[CR]%s' % text
	return dialog.ok(heading, text)

def confirm_dialog(heading=None, text='', highlight='dodgerblue', ok_label=local_string(32839), cancel_label=local_string(32840), top_space=True, default_control=11):
	heading = heading or get_addoninfo('name')
	if isinstance(heading, int): heading = local_string(heading)
	if isinstance(text, int): text = local_string(text)
	if isinstance(ok_label, int): ok_label = local_string(ok_label)
	if isinstance(cancel_label, int): cancel_label = local_string(cancel_label)
	if not text: text = '[CR]%s' % local_string(32580)
	elif top_space: text = '[CR]%s' % text
	return dialog.yesno(heading, text, cancel_label, ok_label)

def select_dialog(function_list, **kwargs):
	def _builder():
		for count, item in enumerate(items, 1):
			line1 = '%s. %s' % (count, item['line1']) if enum else item['line1']
			line2 = '[I]%s[/I]' % (item.get('line2') or item['line1'])
			listitem = make_listitem()
			listitem.setLabel(line1.upper())
			listitem.setLabel2(line2.upper())
			listitem.setArt({'icon': item.get('icon') or default_icon})
			yield listitem
	default_icon = get_addoninfo('icon')
	items = json.loads(kwargs.get('items') or '[]')
	heading = kwargs.get('heading') or get_addoninfo('name')
	enum = kwargs.get('enumerate', 'false') == 'true'
	details = kwargs.get('multi_line', 'true') == 'true'
	multi_choice = kwargs.get('multi_choice', 'false') == 'true'
	if multi_choice:
		preselect = kwargs.get('preselect') or []
		selection = dialog.multiselect(heading, list(_builder()), preselect=preselect, useDetails=details)
	else:
		preselect = kwargs.get('preselect') if kwargs.get('preselect') is not None else -1
		selection = dialog.select(heading, list(_builder()), preselect=preselect, useDetails=details)
	if selection in (-1, None) or (selection == [] and kwargs.get('allow_empty', 'false') != 'true'): return None
	if multi_choice: return [function_list[i] for i in selection]
	return function_list[selection]

def show_text(heading, text=None, file=None, font_size='small', kodi_log=False):
	if isinstance(heading, int): heading = local_string(heading)
	heading = heading.replace('[B]', '').replace('[/B]', '')
	if file:
		with open_file(file) as f: text = f.readBytes().decode('utf-8-sig')
	if kodi_log and confirm_dialog(
		text=local_string(32855),
		ok_label=local_string(32824),
		cancel_label=local_string(32828)
	):
		lines = []
		for line in text.splitlines(keepends=True):
			if line[0].isdigit(): lines += [line]
			else: lines[-1] += line
		text = ''.join(i for i in reversed(lines) if any(x in i.lower() for x in ('exception', 'error')))
	if not text: return notification(32760)
	return dialog.textviewer(heading, text)

def notification(line1, time=3000, icon=None, sound=False):
	if isinstance(line1, int): line1 = local_string(line1)
	icon = icon or get_addoninfo('icon')
	dialog.notification(get_addoninfo('name'), line1, icon, time, sound)

def choose_view(view_type, content):
	from sys import argv
	__handle__ = int(argv[1])
	label = local_string(32547)
	fanart = get_addoninfo('fanart')
	icon = media_path('settings.png')
	params_url = build_url({'mode': 'set_view', 'view_type': view_type})
	listitem = make_listitem()
	listitem.setLabel(label)
	listitem.setArt({'icon': icon, 'poster': icon, 'thumb': icon, 'fanart': fanart, 'banner': icon})
	add_item(__handle__, params_url, listitem, False)
	set_content(__handle__, content)
	end_directory(__handle__)
	set_view_mode(view_type, content)

def set_view(view_type):
	view_id = str(current_window_id().getFocusId())
	dbcon = database_connect(views_db, isolation_level=None)
	dbcur = dbcon.cursor()
	dbcur.execute("""PRAGMA synchronous = OFF""")
	dbcur.execute("""PRAGMA journal_mode = OFF""")
	dbcur.execute("""INSERT OR REPLACE INTO views VALUES (?, ?)""", (view_type, view_id))
	set_view_property(view_type, view_id)
	notification(get_infolabel('Container.Viewmode').upper(), 1500)

def set_view_property(view_type, view_id):
	set_property('pov_lite_%s' % view_type, view_id)

def set_view_properties():
	dbcon = database_connect(views_db, isolation_level=None)
	dbcur = dbcon.cursor()
	dbcur.execute("""SELECT * FROM views""")
	view_ids = dbcur.fetchall()
	for item in view_ids: set_property('pov_lite_%s' % item[0], item[1])

def set_view_mode(view_type, content='files', is_widget=None):
	if is_widget is True or (is_widget is None and external_browse()): return
	view_id = get_property('pov_lite_%s' % view_type)
	if not view_id: return
	try:
		for _ in range(60):
			sleep(50)
			if container_content() != content: continue
			return execute_builtin('Container.SetViewMode(%s)' % view_id)
	except: pass

def clear_view(view_type):
	if not confirm_dialog(): return
	try:
		dbcon = database_connect(views_db, isolation_level=None)
		dbcur = dbcon.cursor()
		dbcur.execute("""PRAGMA synchronous = OFF""")
		dbcur.execute("""PRAGMA journal_mode = OFF""")
		dbcur.execute("""SELECT view_type FROM views""")
		for item in dbcur.fetchall(): clear_property('pov_lite_%s' % item[0])
		dbcur.execute("""DELETE FROM views""")
		dbcur.execute("""VACUUM""")
		dbcon = database_connect('special://profile/Database/ViewModes6.db')
		dbcur = dbcon.cursor()
		dbcur.execute("""DELETE FROM view WHERE path LIKE ?""", ('plugin://%s/%%' % current_addon_id,))
		dbcon.commit()
		dbcon.close()
	except: return notification(32574, 1500)
	notification(32576, 1500)

def build_url(url_params):
	return 'plugin://%s/?%s' % (current_addon_id, urlencode(url_params))

def add_dir(__handle__, url_params, list_name, iconImage=None, fanartImage=None, isFolder=True):
	if 'new_page' in url_params: list_name = f"{list_name} {url_params['new_page']}"
	fanart = fanartImage or get_addoninfo('fanart')
	icon = iconImage or media_path('item_next.png')
	url = build_url(url_params)
	listitem = make_listitem()
	listitem.setLabel(list_name)
	listitem.setArt({'icon': icon, 'poster': icon, 'thumb': icon, 'fanart': fanart, 'banner': icon})
	add_item(__handle__, url, listitem, isFolder)

def remove_meta_keys(dict_item, dict_removals):
	for k in dict_removals: dict_item.pop(k, None)
	return dict_item

def volume_checker(volume_setting=None):
	try: # 0% == -60db, 100% == 0db
		if get_visibility('Player.Muted'): return
		from modules.utils import string_alphanum_to_num
		if not volume_setting: volume_setting = get_setting('volumecheck.percent', '100')
		max_volume = int(min(int(volume_setting), 100))
		current_volume_db = int(string_alphanum_to_num(get_infolabel('Player.Volume').split('.')[0]))
		current_volume_percent = int(100 - ((float(current_volume_db)/60)*100))
		if current_volume_percent > max_volume: execute_builtin('SetVolume(%d)' % int(max_volume))
	except: pass

def focus_index(index, sleep_time=100):
	sleep(sleep_time)
	current_window = current_window_id()
	focus_id = current_window.getFocusId()
	try: current_window.getControl(focus_id).selectItem(index)
	except: pass

def clean_settings_window_properties():
	clear_property('pov_lite_settings')
	notification(32576, 1500)

def fetch_kodi_imagecache(image):
	result = None
	try:
		dbcon = database_connect('special://profile/Database/Textures13.db')
		dbcur = dbcon.cursor()
		dbcur.execute("""SELECT cachedurl FROM texture WHERE url = ?""", (image,))
		result = dbcur.fetchone()[0]
	except: pass
	return result

class SettingsManager:
	def __init__(self):
		self._cache = {}
		self._last_raw_string = None

	def _sync(self):
		current_raw = get_property('pov_lite_settings')
		if current_raw == self._last_raw_string: return
		try: self._cache = json.loads(current_raw)
		except Exception: self._cache = make_settings_dict()
		self._last_raw_string = current_raw

	def get(self, key, fallback=None):
		self._sync()
		value = self._cache.get(key, '')
		if value == '' and fallback is not None: return fallback
		return value

manager = SettingsManager()

FIXED_SETTINGS = {
	'amble.indicators': '',
	'auto_play_episode': 'false',
	'auto_play_movie': 'false',
	'auto_resume_episode': '0',
	'auto_resume_movie': '0',
	'auto_start_pov': 'false',
	'ad.enabled': 'true',
	'ad.expires': '0',
	'ad.priority': '10',
	'ad.torrent.enabled': 'true',
	'autoplay_next_check_threshold': '3',
	'autoplay_next_episode': 'true',
	'autoplay_next_show_window': 'true',
	'autoplay_next_window_percentage': '95',
	'autoplay_next_window_time': '20',
	'autoplay_next_window_timer_method': '0',
	'autoplay_quality_episode': 'SD, 720p, 1080p, 4K',
	'autoplay_quality_movie': 'SD, 720p, 1080p, 4K',
	'autoscrape_next_episode': 'false',
	'autoscrape_next_window_time': '20',
	'check.rd_cloud': 'false',
	'context.exit': '7',
	'context.extras': '2',
	'context.mark': '6',
	'context.options': '1',
	'datetime.offset': '0',
	'default_all_episodes': '1',
	'ext_dialog_highlight': 'cyan',
	'extras.enable_scrollbars': 'false',
	'extras.enabled_menus': '2050,2051,2052,2053,2054,2055,2056,2057,2058,2059,2060,2061,2062',
	'extras.exclude_non_acting_roles': 'true',
	'extras.manage': '[B]Browse...[/B]',
	'extras.open_action': '0',
	'fanart_image': 'pov_fanart.png',
	'filter_av1': '0',
	'filter_dv': '0',
	'filter_hdr': '0',
	'filter_hevc': '0',
	'get_rpdb_movies': 'false',
	'get_rpdb_series': 'false',
	'highlight.type': '2',
	'hoster.identify': 'dodgerblue',
	'ignore_articles': 'true',
	'ignore_results_filter': 'true',
	'image_resolutions': '2',
	'include_3d_results': 'false',
	'include_prerelease_results': 'false',
	'include_year_in_title': '0',
	'int_dialog_highlight': 'magenta',
	'kodi_menu_cache': 'true',
	'load_action': '1',
	'meta_language': 'en',
	'meta_language_display': 'English',
	'mpaa.select': '0',
	'mpaa_region': 'US',
	'nav_jump': '0',
	'nextep.include_airdate': 'true',
	'nextep.include_unaired': 'true',
	'nextep.sort_airing_today_to_top': 'false',
	'nextep.sort_order': '0',
	'nextep.sort_type': '0',
	'page_limit': '100',
	'paginate.lists': 'true',
	'pov.max_threads': '100',
	'provider.debrid_cloud_colour': 'darkviolet',
	'provider.ad_colour': 'darkorange',
	'provider.rd_cloud': 'false',
	'provider.rd_colour': 'seagreen',
	'provider.tb_colour': 'mediumturquoise',
	'rd.enabled': 'true',
	'rd.expires': '0',
	'rd.hoster.enabled': 'false',
	'rd.priority': '10',
	'rd.torrent.enabled': 'true',
	'rd_cloud.title_filter': 'false',
	'results.include.unknown.size': 'true',
	'results.language': 'English',
	'results.language_filter': 'false',
	'results.size.file': '10000',
	'results.size.speed': '20',
	'results.size_filter': '0',
	'results.sort_order': '1',
	'results.sort_order_display': 'Quality, Size, Provider',
	'results.sort_rdcloud_first': 'true',
	'results.sort_size': '0',
	'results.xml_style': 'List Default',
	'results_quality_episode': 'SD, 720p, 1080p, 4K',
	'results_quality_movie': 'SD, 720p, 1080p, 4K',
	'reuse_language_invoker': 'false',
	'rpdb_api_key': '',
	'rpdb_theme': '0',
	'scraper_1080p_highlight': 'cyan',
	'scraper_4k_highlight': 'magenta',
	'scraper_720p_highlight': 'mediumorchid',
	'scraper_SD_highlight': 'dodgerblue',
	'search.enable.yearcheck': 'false',
	'show_specials': 'false',
	'show_unaired': 'true',
	'show_unaired_watchlist': 'true',
	'single_ep_display': '0',
	'single_ep_format': '2',
	'skip_intro.enable': 'true',
	'smart_play.enabled': '0',
	'sort.collection': '0',
	'sort.progress': '0',
	'sort.watched': '0',
	'sort.watchlist': '0',
	'stingers.enable': 'true',
	'stingers.threshold': '10',
	'store_torrent.realdebrid': 'false',
	'store_torrent.alldebrid': 'false',
	'store_torrent.torbox': 'false',
	'tb.enabled': 'true',
	'tb.expires': '0',
	'tb.priority': '10',
	'tb.torrent.enabled': 'true',
	'thumb_fanart': 'false',
	'tmdb_read_token': (
		'eyJhbGciOiJIUzI1NiJ9.'
		'eyJhdWQiOiJkODQ4MzE2YTMzZTc5MDk1YmViOTQ1YTJiZDJkNTNiMSIsIm5iZiI6MTcxMTQ3Mzg1NC45MzQsInN1YiI6IjY2MDMwNGJlYjAyZjVlMDE3ZDIxNDAzZSIsInNjb3BlcyI6WyJhcGlfcmVhZCJdLCJ2ZXJzaW9uIjoxfQ.'
		'_uRCLIwLIE2gOlH8q04gcM3ywIXpxuZ-uyel-UMRRg4'
	),
	'torrent.display.uncached': 'false',
	'torrent.identify': 'magenta',
	'trakt.calendar_focus_today': 'true',
	'trakt.calendar_future_days': '7',
	'trakt.calendar_previous_days': '7',
	'trakt.calendar_sort_order': '0',
	'trakt.client_id': '6bc29124c3d9466e06a3ed19a7b5976fcb28311008401e1ce04cf08196f8b16a',
	'use_season_title': 'false',
	'volumecheck.enabled': 'false',
	'volumecheck.percent': '50',
	'widget_hide_watched': 'false',
}

EXTERNAL_PROVIDER_SETTING_IDS = tuple('provider.external.%s.enabled' % provider for provider in EXTERNAL_PROVIDERS)

PERSISTED_SETTING_IDS = frozenset((
	'database.maintenance.due',
	'ad.account_id', 'ad.token',
	'rd.client_id', 'rd.refresh', 'rd.secret', 'rd.token', 'rd.username',
	'tb.account_id', 'tb.token'
) + EXTERNAL_PROVIDER_SETTING_IDS)

_settings_lock = Lock()
_persisted_settings_schema = """CREATE TABLE IF NOT EXISTS settings (id TEXT PRIMARY KEY, value TEXT NOT NULL)"""

def _persisted_settings_connection():
	make_directorys(profile_path)
	dbcon = database_connect(persisted_settings_db)
	dbcon.execute(_persisted_settings_schema)
	return dbcon

def _read_persisted_settings():
	dbcon = _persisted_settings_connection()
	try: return {str(item[0]): str(item[1]) for item in dbcon.execute("""SELECT id, value FROM settings""")}
	finally: dbcon.close()

def _write_persisted_settings(settings_dict, overwrite=True):
	if not settings_dict: return 0
	query = """INSERT OR REPLACE INTO settings VALUES (?, ?)""" if overwrite else """INSERT OR IGNORE INTO settings VALUES (?, ?)"""
	dbcon = _persisted_settings_connection()
	try:
		before = dbcon.total_changes
		dbcon.executemany(query, [(setting_id, str(value)) for setting_id, value in settings_dict.items()])
		dbcon.commit()
		return dbcon.total_changes - before
	finally: dbcon.close()

def get_setting(setting_id, fallback=None):
	if setting_id in FIXED_SETTINGS: return FIXED_SETTINGS[setting_id]
	try:
		if setting_id in PERSISTED_SETTING_IDS:
			if not path_exists(persisted_settings_db): make_settings_dict()
			value = _read_persisted_settings().get(setting_id, '')
		else: value = manager.get(setting_id, fallback)
	except: value = fallback if fallback is not None else ''
	if value == '' and fallback is not None: return fallback
	return value

def set_settings(settings_dict):
	if not settings_dict or any(setting_id not in PERSISTED_SETTING_IDS for setting_id in settings_dict): return False
	try:
		_write_persisted_settings(settings_dict)
		make_settings_dict()
		return True
	except Exception as e:
		logger('set_settings error', str(e))
		return False

def set_setting(setting_id, value):
	return set_settings({setting_id: value})

def make_settings_dict():
	try:
		settings_dict = _read_persisted_settings()
	except Exception as e:
		logger('make_settings_dict error', str(e))
		settings_dict = {}
	set_property('pov_lite_settings', json.dumps(settings_dict))
	return settings_dict

def clean_settings(silent=False):
	import xml.etree.ElementTree as ET
	profile_xml = profile_path + 'settings.xml'
	try:
		removed_settings = []
		with _settings_lock:
			file_exists = path_exists(profile_xml)
			if file_exists:
				with open_file(profile_xml) as xml_file: root = ET.fromstring(xml_file.read())
			else: root = ET.Element('settings', {'version': '2'})
			for parent in root.iter():
				for item in list(parent):
					if item.tag != 'setting' or item.get('id') not in FIXED_SETTINGS: continue
					removed_settings.append(item)
					parent.remove(item)
			if not file_exists or removed_settings:
				make_directorys(profile_path)
				with open_file(profile_xml, 'w') as xml_file: xml_file.write(ET.tostring(root, encoding='unicode'))
		make_settings_dict()
		text = local_string(32813) % len(removed_settings) if removed_settings else 32576
		if not silent: notification(text, 1500)
	except:
		if not silent: notification(32574, 1500)

def upload_logfile():
	# Thanks 123Venom
	log_file, url = 'special://logpath/kodi.log', 'https://paste.kodi.tv/'
	if not path_exists(log_file): return ok_dialog(text='Error. Log File Not Found.')
	from platform import python_version
	text = f"Kodi: {get_infolabel('System.BuildVersion')}[CR]Python: {python_version()}[CR]{local_string(32580)}"
	if not confirm_dialog(text=text, top_space=False): return
	show_busy_dialog()
	import requests
	try:
		with open_file(log_file) as f: text = f.readBytes().decode('utf-8-sig')
		response = requests.post('%s%s' % (url, 'documents'), data=text, timeout=10.0).json()
		if 'key' in response: ok_dialog(text=url + response['key'])
		else: ok_dialog(text='Error. Log Upload Failed')
	except: notification(32574, 1500)
	hide_busy_dialog()

def timeIt(func):
	# Thanks to 123Venom
	import time
	fnc_name = func.__name__
	def wrap(*args, **kwargs):
		started_at = time.perf_counter()
		result = func(*args, **kwargs)
		logger('%s.%s' % (__name__ , fnc_name), (time.perf_counter() - started_at))
		return result
	return wrap
