from modules import kodi_utils

POV_ROUTES = {
	'play_trailer': lambda p: _import('modules.trailers', 'play')(p),
	'smart_play_media': lambda p: _import('modules.episode_tools', 'SmartPlay')(p),
	'play_media': lambda p: _import('modules.sources', 'Sources').factory(p),
	'media_play': lambda p: _import('modules.debrid', 'play_from_cloud')(p),

	'options_menu_choice': lambda p: _import('modules.dialogs', 'options_menu')(p),
	'extras_menu_choice': lambda p: _import('modules.dialogs', 'extras_menu')(p),
	'show_media_info': lambda p: _import('modules.dialogs', 'show_media_info')(p),
	'subtitle_settings': lambda p: _import('modules.dialogs', 'subtitle_settings_menu')(),
	'hydrate_media_info': lambda p: _import('modules.dialogs', 'hydrate_media_info')(p),
	'play_from_info': lambda p: _import('modules.dialogs', 'play_from_info')(p),
	'pov_page_back': lambda p: _import('modules.dialogs', 'pov_page_back')(p),
	'random_choice': lambda p: _import('modules.dialogs', 'random_choice')(p['mode'], p),

	'build_movie_list': lambda p: _import('menus.movies', 'Menu')(p).run(),
	'build_tvshow_list': lambda p: _import('menus.tvshows', 'Menu')(p).run(),
	'build_season_list': lambda p: _import('menus.seasons', 'Seasons')(p).run(),
	'build_episode_list': lambda p: _import('menus.seasons', 'Episodes')(p).run(),
	'build_in_progress_episode': lambda p: _import('menus.episodes', 'Menu')(p).run(),
	'build_next_episode': lambda p: _import('menus.episodes', 'Menu')(p).run(),
	'build_navigate_to_page': lambda p: _import('modules.dialogs', 'build_navigate_to_page')(p),
	'build_popular_people': lambda p: _import('menus.people', 'popular_people')(),
	'build_person_credits': lambda p: _import('menus.people', 'build_person_credits')(p),
	'build_media_cast': lambda p: _import('menus.people', 'build_media_cast')(p),

	'clear_all_cache': lambda p: _import('modules.cache', 'clear_all_cache')(),
	'clear_cache': lambda p: _import('modules.cache', 'clear_cache')(p.get('cache')),
	'clean_databases': lambda p: _import('modules.cache', 'clean_databases')(),
	'clear_streams': lambda p: _import('modules.tuneup', 'clear_streams')(),
	'clear_thumbnails': lambda p: _import('modules.tuneup', 'clear_thumbnails')(),

	'search_history': lambda p: _import('menus.history', 'search_history')(p),
	'get_search_term': lambda p: _import('menus.history', 'get_search_term')(p),
	'person_search': lambda p: _import('menus.people', 'person_search')(p['query']),
	'show_person_info': lambda p: _import('menus.people', 'show_person_info')(p),
	'hydrate_person_info': lambda p: _import('menus.people', 'hydrate_person_info')(p),
	'person_data_dialog': lambda p: _import('menus.people', 'person_data_dialog')(p),

	'mark_as_watched_unwatched_episode': lambda p: _import('caches.watched_cache', 'mark_as_watched_unwatched_episode')(p),
	'mark_as_watched_unwatched_season': lambda p: _import('caches.watched_cache', 'mark_as_watched_unwatched_season')(p),
	'mark_as_watched_unwatched_tvshow': lambda p: _import('caches.watched_cache', 'mark_as_watched_unwatched_tvshow')(p),
	'mark_as_watched_unwatched_movie': lambda p: _import('caches.watched_cache', 'mark_as_watched_unwatched_movie')(p),
	'watched_unwatched_erase_bookmark': lambda p: _import('caches.progress_cache', 'erase_bookmark')(
		p.get('mediatype'), p.get('tmdb_id'), p.get('season', ''), p.get('episode', ''), p.get('refresh', 'false')
	),

	'choose_view': lambda p: _import('modules.kodi_utils', 'choose_view')(p['view_type'], p.get('content', '')),
	'set_view': lambda p: _import('modules.kodi_utils', 'set_view')(p['view_type']),
	'clear_view': lambda p: _import('modules.kodi_utils', 'clear_view')(p['view_type']),
	'show_text': lambda p: _import('modules.kodi_utils', 'show_text')(
		p.get('heading'), p.get('text'), p.get('file'), p.get('font_size', 'small'), p.get('kodi_log', 'false') == 'true'
	),

	'toggle_provider': lambda p: _import('modules.utils', 'toggle_provider')(),
	'upload_logfile': lambda p: _import('modules.kodi_utils', 'upload_logfile')(),
	'myservices': lambda p: _import('modules.myservices', 'authorize')(),
	'refer_link': lambda p: _import('modules.myservices', 'refer_link')(p['query']),
}

def _import(module_path, attr_name):
	mod = __import__(module_path, fromlist=[attr_name])
	return getattr(mod, attr_name)

def _run_class_method(cls_module, cls_name, params, mode):
	cls = _import(cls_module, cls_name)
	method_name = mode.split('.')[-1]
	method = getattr(cls(params), method_name, None)
	if callable(method): return method()

def _run_debrid_method(cls_module, cls_name, params, mode):
	cls = _import(cls_module, cls_name)
	method_name = mode.split('.')[-1]
	method = getattr(cls(), method_name, None)
	if callable(method): return method(params)

def _run_dynamic_func(module_path, mode, params):
	from modules.utils import manual_function_import
	func_name = mode.split('.')[-1]
	function = manual_function_import(module_path, func_name)
	return function(params)

def routing(sys_obj):
	params = kodi_utils.parsed_query(sys_obj.argv[2])
	if params.get('action') in ('search', 'manualsearch', 'download'): return _import('subtitle_service', 'run')(sys_obj)
	mode = params.get('mode', 'navigator.main')

	if mode in POV_ROUTES: return POV_ROUTES[mode](params)

	if mode.startswith('navigator.'): return _run_class_method('menus.navigator', 'Navigator', params, mode)

	if mode.startswith('discover.'): return _run_class_method('menus.discover', 'Discover', params, mode)

	if mode.startswith('menu_editor.'): return _run_class_method('modules.menu_editor', 'MenuEditor', params, mode)

	if '_image' in mode: return _import('menus.images', 'Images')().run(params)

	if mode.startswith('build_'):
		if mode.startswith('build_trakt_'): return _run_dynamic_func('menus.trakt', mode, params)

	if mode.startswith('real_debrid.'): return _run_debrid_method('menus.real_debrid', 'Menu', params, 'run')

class Router:
	def run(self, sys):
		return routing(sys)
