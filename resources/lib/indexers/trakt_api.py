import requests
import time
from caches import trakt_cache
from caches.main_cache import cache_object
from modules import kodi_utils
from modules.utils import get_datetime

logger = kodi_utils.logger
get_setting = kodi_utils.get_setting
EXPIRES_2_DAYS = 48
READ_TOKEN = get_setting('trakt.client_id')
base_url = 'https://api.trakt.tv/%s'
timeout = 10.05
session = requests.Session()
session.headers.update({'User-Agent': kodi_utils.xbmc.getUserAgent()})
retry = requests.adapters.Retry(total=2, connect=2, read=2, status=1, backoff_factor=0.25, status_forcelist=(429, 502, 503, 504))
session.mount('https://api.trakt.tv', requests.adapters.HTTPAdapter(pool_maxsize=100, max_retries=retry))

def call_trakt(path, params=None, pagination=False, page=1, pagination_details=False):
	if isinstance(path, dict): return call_trakt(str(path.pop('path')), **path)
	else: path = str(path)
	headers = {'Content-Type': 'application/json', 'trakt-api-key': READ_TOKEN, 'trakt-api-version': '2'}
	try:
		response = session.get(base_url % path, params=params, headers=headers, timeout=timeout)
		result = response.json() if 'json' in response.headers.get('Content-Type', '') else response.text
		if not response.ok: response.raise_for_status()
		if pagination_details: return result, response.headers
		if pagination: return result, int(response.headers.get('X-Pagination-Page-Count', page))
		return result
	except requests.RequestException as e:
		logger('trakt error', str(e))

def trakt_movies_trending(page_no):
	params = {'limit': 20, 'page': page_no}
	string = 'trakt_movies_trending_%s' % page_no
	url = {'path': 'movies/trending', 'params': params, 'pagination': True}
	return cache_object(call_trakt, string, url, expiration=EXPIRES_2_DAYS)

def trakt_movies_trending_recent(page_no):
	year = get_datetime().year
	params = {'languages': 'en', 'limit': 20, 'page': page_no, 'years': '%s-%s' % (year - 1, year)}
	string = 'trakt_movies_trending_recent_limit20_%s' % page_no
	url = {'path': 'movies/trending', 'params': params, 'pagination': True}
	return cache_object(call_trakt, string, url, expiration=EXPIRES_2_DAYS)

def trakt_movies_most_watched(page_no):
	params = {'limit': 20, 'page': page_no}
	string = 'trakt_movies_most_watched_%s' % page_no
	url = {'path': 'movies/watched/weekly', 'params': params, 'pagination': True}
	return cache_object(call_trakt, string, url, expiration=EXPIRES_2_DAYS)

def trakt_tv_trending(page_no):
	params = {'limit': 20, 'page': page_no}
	string = 'trakt_tv_trending_%s' % page_no
	url = {'path': 'shows/trending', 'params': params, 'pagination': True}
	return cache_object(call_trakt, string, url, expiration=EXPIRES_2_DAYS)

def trakt_tv_trending_recent(page_no):
	year = get_datetime().year
	params = {'languages': 'en', 'limit': 20, 'page': page_no, 'years': '%s-%s' % (year - 1, year)}
	string = 'trakt_tv_trending_recent_limit20_%s' % page_no
	url = {'path': 'shows/trending', 'params': params, 'pagination': True}
	return cache_object(call_trakt, string, url, expiration=EXPIRES_2_DAYS)

def trakt_tv_most_watched(page_no):
	params = {'limit': 20, 'page': page_no}
	string = 'trakt_tv_most_watched_%s' % page_no
	url = {'path': 'shows/watched/weekly', 'params': params, 'pagination': True}
	return cache_object(call_trakt, string, url, expiration=EXPIRES_2_DAYS)

def trakt_moviesanime_trending(page_no):
	params = {'limit': 20, 'page': page_no, 'genres': 'anime'}
	string = 'trakt_moviesanime_trending_%s' % page_no
	url = {'path': 'movies/trending', 'params': params, 'pagination': True}
	return cache_object(call_trakt, string, url, expiration=EXPIRES_2_DAYS)

def trakt_moviesanime_most_watched(page_no):
	params = {'limit': 20, 'page': page_no, 'genres': 'anime'}
	string = 'trakt_moviesanime_most_watched_%s' % page_no
	url = {'path': 'movies/watched/all', 'params': params, 'pagination': True}
	return cache_object(call_trakt, string, url, expiration=EXPIRES_2_DAYS)

def trakt_tvanime_trending(page_no):
	params = {'limit': 20, 'page': page_no, 'genres': 'anime'}
	string = 'trakt_tvanime_trending_%s' % page_no
	url = {'path': 'shows/trending', 'params': params, 'pagination': True}
	return cache_object(call_trakt, string, url, expiration=EXPIRES_2_DAYS)

def trakt_tvanime_most_watched(page_no):
	params = {'limit': 20, 'page': page_no, 'genres': 'anime'}
	string = 'trakt_tvanime_most_watched_%s' % page_no
	url = {'path': 'shows/watched/all', 'params': params, 'pagination': True}
	return cache_object(call_trakt, string, url, expiration=EXPIRES_2_DAYS)

def trakt_trending_popular_lists(list_type):
	string = 'trakt_%s_user_lists' % list_type
	url = {'path': 'lists/%s' % list_type, 'params': {'limit': 250}}
	return cache_object(call_trakt, string, url)

def trakt_search_lists(search_title, page):
	params = {'limit': 100, 'page': page, 'query': search_title}
	return call_trakt('search/list', params=params, pagination=True)

def get_trakt_list_page(list_type, list_id, user, slug, page, limit):
	"""Return one display page and its total, keeping complete and page caches separate."""
	from modules.utils import paginate_list
	if page < 1 or not 1 <= limit <= 250: raise ValueError('Invalid list page')
	legacy_key = 'trakt_list_contents_%s_%s_%s' % (list_type, user, slug)
	full = trakt_cache.get_cached(legacy_key)
	if isinstance(full, list): return paginate_list(full, page, limit)
	url = 'users/%s/lists/%s/items' % (user, list_id)
	key_prefix = 'trakt_list_page_v2_%s_%s_%s_%s_%s_' % (list_type, user, list_id, slug, limit)
	key = key_prefix + str(page)
	def valid(data):
		if not isinstance(data, dict) or not isinstance(data.get('items'), list) or len(data['items']) > limit: return False
		created = data.get('created')
		if not isinstance(created, (int, float)) or isinstance(created, bool) or not 0 <= time.time() - created < 300: return False
		pages = data.get('pages')
		if not isinstance(pages, int) or isinstance(pages, bool) or pages < 0: return False
		return (page == 1 and pages in (0, 1) and not data['items']) or (pages >= page and bool(data['items']) and (page == pages or len(data['items']) == limit))
	data = trakt_cache.get_cached(key)
	if valid(data): return data['items'], max(1, data['pages'])
	try:
		items, headers = call_trakt(url, params={'limit': limit, 'page': page}, pagination_details=True)
		if int(headers['X-Pagination-Limit']) != limit: raise ValueError('Invalid pagination limit')
		if 'X-Pagination-Page' in headers and int(headers['X-Pagination-Page']) != page: raise ValueError('Invalid pagination page')
		data = {'items': items, 'pages': int(headers['X-Pagination-Page-Count']), 'created': time.time()}
		if not valid(data): raise ValueError('Invalid pagination')
	except (KeyError, TypeError, ValueError, OverflowError): raise ValueError('List page unavailable') from None
	trakt_cache.set_cached(key, data)
	return data['items'], max(1, data['pages'])
