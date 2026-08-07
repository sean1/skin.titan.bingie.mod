import requests
from concurrent.futures import ThreadPoolExecutor
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

def call_trakt(path, params=None, pagination=False, page=1):
	if isinstance(path, dict): return call_trakt(str(path.pop('path')), **path)
	else: path = str(path)
	headers = {'Content-Type': 'application/json', 'trakt-api-key': READ_TOKEN, 'trakt-api-version': '2'}
	try:
		response = session.get(base_url % path, params=params, headers=headers, timeout=timeout)
		result = response.json() if 'json' in response.headers.get('Content-Type', '') else response.text
		if not response.ok: response.raise_for_status()
		if pagination: return result, int(response.headers.get('X-Pagination-Page-Count', page))
		return result
	except requests.RequestException as e:
		logger('trakt error', str(e))

def _get_trakt_paginated_list(url):
	params = {'limit': 250, 'page': 1}
	try: items, pages = call_trakt(url, params=params, pagination=True)
	except: return []
	if pages <= 1: return items
	args = ({'path': url, 'params': {**params, 'page': page}} for page in range(2, pages + 1))
	with ThreadPoolExecutor() as executor:
		for result in executor.map(call_trakt, args):
			if isinstance(result, list): items.extend(result)
	return items

def trakt_calendar_days(current_date):
	from datetime import timedelta
	previous_days = int(get_setting('trakt.calendar_previous_days', '3'))
	future_days = int(get_setting('trakt.calendar_future_days', '7'))
	start = (current_date - timedelta(days=previous_days)).strftime('%Y-%m-%d')
	return start, str(previous_days + future_days)

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

def trakt_calendar_data(url):
	result = []
	seen = set()
	for item in call_trakt(url) or []:
		try:
			if item['episode']['season'] <= 0: continue
			season, episode = item['episode']['season'], item['episode']['number']
			sort_title = '%s s%02d e%02d' % (item['show']['title'], season, episode)
			if sort_title not in seen and not seen.add(sort_title): result.append({
				'sort_title': sort_title, 'first_aired': item['first_aired'],
				'media_ids': item['show']['ids'], 'season': season, 'episode': episode
			})
		except: pass
	return result

def trakt_anime_calendar(current_date):
	start, finish = trakt_calendar_days(current_date)
	string = 'trakt_anime_calendar_%s_%s' % (start, finish)
	url = {'path': 'calendars/all/shows/%s/%s' % (start, finish), 'params': {'genres': 'anime'}}
	return cache_object(trakt_calendar_data, string, url)

def get_trakt_list_contents(list_type, list_id, user, slug):
	string = 'trakt_list_contents_%s_%s_%s' % (list_type, user, slug)
	url = 'users/%s/lists/%s/items' % (user, list_id)
	return trakt_cache.cache_trakt_object(_get_trakt_paginated_list, string, url)
