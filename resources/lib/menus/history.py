from urllib.parse import unquote
from modules import kodi_utils
# from modules.kodi_utils import logger

search_params = {
	'movie': {'mediatype': 'movie'},
	'tvshow': {'mediatype': 'tv_show'},
	'people': {'search_type': 'people'},
	'tmdb_collections': {'search_type': 'tmdb_collections', 'mediatype': 'movie'}
}
query_params = {
	'people': {'mode': 'person_search'},
	'tmdb_collections': {'mode': 'build_movie_list', 'action': 'tmdb_movies_search_collections'}
}

def search_history(params):
	return get_search_term(search_params[params['action']])

def get_search_term(params):
	kodi_utils.close_all_dialog()
	mediatype = params.get('mediatype', '')
	search_type = params.get('search_type', 'media_title')
	params_query = params.get('query', '')
	if search_type not in query_params:
		if mediatype == 'movie':
			url_params = {'mode': 'build_movie_list', 'action': 'tmdb_movies_search'}
		else: url_params = {'mode': 'build_tvshow_list', 'action': 'tmdb_tv_search'}
	else: url_params = dict(query_params[search_type])
	query = params_query or kodi_utils.dialog.input('BINGIE Lite')
	if not query.strip(): return
	query = unquote(query)
	url_params['query'] = query
	if kodi_utils.external_browse():
		return kodi_utils.execute_builtin('ActivateWindow(Videos,%s,return)' % kodi_utils.build_url(url_params))
	return kodi_utils.execute_builtin('Container.Update(%s)' % kodi_utils.build_url(url_params))
