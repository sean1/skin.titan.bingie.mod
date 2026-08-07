import requests
from caches.main_cache import cache_object
from caches.meta_cache import cache_function
from modules import kodi_utils
from modules.settings import get_language
from modules.utils import chunks

ls, logger = kodi_utils.local_string, kodi_utils.logger
get_setting = kodi_utils.get_setting
EXPIRES_4_HOURS, EXPIRES_2_DAYS, EXPIRES_1_WEEK, EXPIRES_1_MONTH = 4, 48, 168, 672
DETAIL_SHELF_LIMIT = 15
READ_TOKEN = get_setting('tmdb_read_token')
movies_append = 'external_ids,videos,credits,release_dates,alternative_titles,translations,images'
tvshows_append = 'external_ids,videos,credits,content_ratings,alternative_titles,translations,images'
tmdb_image_base = 'https://image.tmdb.org/t/p/%s%s'
tmdb_image_prefix = tmdb_image_base.split('%s', 1)[0]

def resized_tmdb_image(image, resolution):
	if not isinstance(image, str) or not image.startswith(tmdb_image_prefix): return image
	try:
		path = image[len(tmdb_image_prefix):]
		return '%s%s%s' % (tmdb_image_prefix, resolution, path[path.index('/'):])
	except (TypeError, ValueError): return image
base_url = 'https://api.themoviedb.org/3'
timeout = 3.05
session = requests.Session()
retry = requests.adapters.Retry(total=2, connect=2, read=2, status=1, backoff_factor=0.25, status_forcelist=(429, 502, 503, 504))
session.mount('https://api.themoviedb.org', requests.adapters.HTTPAdapter(pool_maxsize=100, max_retries=retry))

def get_tmdb(url):
	try:
		response = session.get(url, headers={'Authorization': 'Bearer %s' % READ_TOKEN}, timeout=timeout)
		result = response.json() if 'json' in response.headers.get('Content-Type', '') else response.text
		if not response.ok: response.raise_for_status()
		return result
	except requests.RequestException as e:
		logger('tmdb error', str(e))

def tmdb_keyword_id(query):
	string = 'tmdb_keyword_id_%s' % query
	url = '%s/search/keyword?query=%s' % (base_url, query)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_1_WEEK)

def tmdb_company_id(query):
	string = 'tmdb_company_id_%s' % query
	url = '%s/search/company?query=%s' % (base_url, query)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_1_WEEK)

def tmdb_media_images(mediatype, tmdb_id):
	if mediatype == 'movies': mediatype = 'movie'
	string = 'tmdb_media_images_%s_%s' % (mediatype, tmdb_id)
	url = '%s/%s/%s/images' % (base_url, mediatype, tmdb_id)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_1_WEEK)

def tmdb_media_videos(mediatype, tmdb_id):
	if mediatype == 'movies': mediatype = 'movie'
	if mediatype in ('tvshow', 'tvshows'): mediatype = 'tv'
	string = 'tmdb_media_videos_%s_%s' % (mediatype, tmdb_id)
	url = '%s/%s/%s/videos' % (base_url, mediatype, tmdb_id)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_1_WEEK)

def tmdb_movies_discover(query, page_no):
	string = query % page_no
	url = query % page_no
	return cache_object(get_tmdb, string, url)

def tmdb_movies_collection(collection_id):
	string = 'tmdb_movies_collection_%s' % collection_id
	url = '%s/collection/%s?language=en-US' % (base_url, collection_id)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_1_WEEK)

def tmdb_movies_in_collection(tmdb_id, _page_no, collection_id=None):
	if not collection_id:
		string = 'tmdb_movie_collection_id_%s' % tmdb_id
		url = '%s/movie/%s?language=en-US' % (base_url, tmdb_id)
		movie = cache_object(get_tmdb, string, url, expiration=EXPIRES_1_WEEK) or {}
		collection_id = (movie.get('belongs_to_collection') or {}).get('id')
	data = tmdb_movies_collection(collection_id) if collection_id else {}
	parts = [i for i in (data or {}).get('parts', ()) if str(i.get('id')) != str(tmdb_id) and i.get('backdrop_path')]
	parts.sort(key=lambda k: k.get('release_date') or '9999')
	results = parts[:DETAIL_SHELF_LIMIT]
	return {'page': 1, 'total_pages': 1, 'total_results': len(results), 'results': results}

def tmdb_movies_title_year(title, year=None):
	if year:
		string = 'tmdb_movies_title_year_%s_%s' % (title, year)
		url = '%s/search/movie?language=en-US&query=%s&year=%s' % (base_url, title, year)
	else:
		string = 'tmdb_movies_title_year_%s' % title
		url = '%s/search/movie?language=en-US&query=%s' % (base_url, title)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_1_MONTH)

def tmdb_oscar_winners(page_no):
	from modules.meta_lists import oscar_winners
	results = [[{'id': x} for x in i] for i in chunks(oscar_winners, 20)]
	return {'page': page_no, 'total_pages': len(results), 'results': results[page_no - 1]}

def tmdb_movies_trending(page_no):
	string = 'tmdb_movies_trending_%s' % page_no
	url = '%s/trending/movie/week?language=en-US&page=%s' % (base_url, page_no)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_movies_popular(page_no):
	string = 'tmdb_movies_popular_global_%s' % page_no
	url = '%s/discover/movie?language=en-US&page=%s' % (base_url, page_no)
	url += '&sort_by=popularity.desc'
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_movies_blockbusters(page_no):
	string = 'tmdb_movies_blockbusters_%s' % page_no
	url = '%s/discover/movie?language=en-US&region=US&page=%s&sort_by=revenue.desc' % (base_url, page_no)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_movies_premieres(page_no):
	current_date, previous_date = get_dates(31, reverse=True)
	string = 'tmdb_movies_premieres_%s' % page_no
	url = '%s/discover/movie?language=en-US&region=US&page=%s' % (base_url, page_no)
	url += '&with_release_type=1|3|2&release_date.gte=%s&release_date.lte=%s' % (previous_date, current_date)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_movies_latest_releases(page_no):
	current_date, previous_date = get_dates(31, reverse=True)
	string = 'tmdb_movies_latest_releases_%s' % page_no
	url = '%s/discover/movie?language=en-US&region=US&page=%s' % (base_url, page_no)
	url += '&with_release_type=4|5&release_date.gte=%s&release_date.lte=%s' % (previous_date, current_date)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_movies_upcoming(page_no):
	current_date, future_date = get_dates(31, reverse=False)
	string = 'tmdb_movies_upcoming_%s' % page_no
	url = '%s/discover/movie?language=en-US&region=US&page=%s' % (base_url, page_no)
	url += '&with_release_type=3|2|1&release_date.gte=%s&release_date.lte=%s' % (current_date, future_date)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_movies_genres(genre_id, page_no):
	string = 'tmdb_movies_genres_%s_%s' % (genre_id, page_no)
	url = '%s/discover/movie?language=en-US&region=US&page=%s&with_genres=%s&sort_by=popularity.desc' % (base_url, page_no, genre_id)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_movies_year(year, page_no):
	string = 'tmdb_movies_year_%s_%s' % (year, page_no)
	url = '%s/discover/movie?language=en-US&region=US&page=%s' % (base_url, page_no)
	url += '&sort_by=popularity.desc&certification_country=US&primary_release_year=%s' % year
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_movies_networks(network_id, page_no):
	string = 'tmdb_movies_networks_%s_%s' % (network_id, page_no)
	url = '%s/discover/movie?language=en-US&region=US&page=%s' % (base_url, page_no)
	url += '&sort_by=popularity.desc&certification_country=US&with_companies=%s' % network_id
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_movies_similar(tmdb_id, page_no):
	string = 'tmdb_movies_similar_%s_%s' % (tmdb_id, page_no)
	url = '%s/movie/%s/similar?language=en-US&page=%s' % (base_url, tmdb_id, page_no)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_movies_recommendations(tmdb_id, page_no):
	string = 'tmdb_movies_recommendations_%s_%s' % (tmdb_id, page_no)
	url = '%s/movie/%s/recommendations?language=en-US&page=%s' % (base_url, tmdb_id, page_no)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def _more_like_this(tmdb_id, recommendations, similar_function):
	results, seen = [], {str(tmdb_id)}

	def add_items(data):
		for item in (data or {}).get('results', ()):
			item_id = item.get('id')
			item_key = str(item_id)
			if not item_id or item_key in seen or not item.get('backdrop_path'): continue
			seen.add(item_key)
			results.append(item)
			if len(results) == DETAIL_SHELF_LIMIT: break

	add_items(recommendations)
	if len(results) < DETAIL_SHELF_LIMIT: add_items(similar_function(tmdb_id, 1))
	return {'page': 1, 'total_pages': 1, 'total_results': len(results), 'results': results}

def tmdb_movies_more_like_this(tmdb_id, _page_no):
	return _more_like_this(tmdb_id, tmdb_movies_recommendations(tmdb_id, 1), tmdb_movies_similar)

def tmdb_movies_search(query, page_no):
	string = 'tmdb_movies_search_%s_%s' % (query, page_no)
	url = '%s/search/movie?language=en-US&query=%s&page=%s' % (base_url, query, page_no)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_4_HOURS)

def tmdb_movies_search_collections(query, page_no):
	string = 'tmdb_movies_search_collections_%s_%s' % (query, page_no)
	url = '%s/search/collection?language=en-US&query=%s&page=%s' % (base_url, query, page_no)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_1_WEEK)

def tmdb_tv_discover(query, page_no):
	string = url = query % page_no
	return cache_object(get_tmdb, string, url)

def tmdb_tv_title_year(title, year=None):
	if year:
		string = 'tmdb_tv_title_year_%s_%s' % (title, year)
		url = '%s/search/tv?query=%s&first_air_date_year=%s&language=en-US' % (base_url, title, year)
	else:
		string = 'tmdb_tv_title_year_%s' % title
		url = '%s/search/tv?query=%s&language=en-US' % (base_url, title)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_1_MONTH)

def tmdb_tv_trending(page_no):
	string = 'tmdb_tv_trending_%s' % page_no
	url = '%s/trending/tv/week?language=en-US&page=%s' % (base_url, page_no)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_tv_popular(page_no):
	string = 'tmdb_tv_popular_global_%s' % page_no
	url = '%s/discover/tv?language=en-US&page=%s' % (base_url, page_no)
	url += '&sort_by=popularity.desc&without_genres=10763,10767'
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_tv_premieres(page_no):
	current_date, previous_date = get_dates(31, reverse=True)
	string = 'tmdb_tv_premieres_%s' % page_no
	url = '%s/discover/tv?with_original_language=en&language=en-US&region=US&page=%s' % (base_url, page_no)
	url += '&sort_by=popularity.desc&first_air_date.gte=%s&first_air_date.lte=%s' % (previous_date, current_date)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_tv_upcoming(page_no):
	current_date, future_date = get_dates(31, reverse=False)
	string = 'tmdb_tv_upcoming_%s' % page_no
	url = '%s/discover/tv?with_original_language=en&language=en-US&region=US&page=%s' % (base_url, page_no)
	url += '&sort_by=popularity.desc&first_air_date.gte=%s&first_air_date.lte=%s' % (current_date, future_date)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_tv_genres(genre_id, page_no):
	string = 'tmdb_tv_genres_%s_%s' % (genre_id, page_no)
	url = '%s/discover/tv?page=%s' % (base_url, page_no)
	url += '&with_genres=%s&sort_by=popularity.desc&include_null_first_air_dates=false' % genre_id
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_tv_year(year, page_no):
	string = 'tmdb_tv_year_%s_%s' % (year, page_no)
	url = '%s/discover/tv?language=en-US&region=US&page=%s' % (base_url, page_no)
	url += '&sort_by=popularity.desc&include_null_first_air_dates=false&first_air_date_year=%s' % year
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_tv_networks(network_id, page_no):
	string = 'tmdb_tv_networks_%s_%s' % (network_id, page_no)
	url = '%s/discover/tv?language=en-US&region=US&page=%s' % (base_url, page_no)
	url += '&sort_by=popularity.desc&include_null_first_air_dates=false&with_networks=%s' % network_id
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_tv_similar(tmdb_id, page_no):
	string = 'tmdb_tv_similar_%s_%s' % (tmdb_id, page_no)
	url = '%s/tv/%s/similar?language=en-US&page=%s' % (base_url, tmdb_id, page_no)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_tv_recommendations(tmdb_id, page_no):
	string = 'tmdb_tv_recommendations_%s_%s' % (tmdb_id, page_no)
	url = '%s/tv/%s/recommendations?language=en-US&page=%s' % (base_url, tmdb_id, page_no)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_tv_more_like_this(tmdb_id, _page_no):
	return _more_like_this(tmdb_id, tmdb_tv_recommendations(tmdb_id, 1), tmdb_tv_similar)

def tmdb_tv_search(query, page_no):
	string = 'tmdb_tv_search_%s_%s' % (query, page_no)
	url = '%s/search/tv?language=en-US&query=%s&page=%s' % (base_url, query, page_no)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_4_HOURS)

def tmdb_moviesanime_popular(page_no):
	string = 'tmdb_moviesanime_popular_%s' % page_no
	url = '%s/discover/movie?page=%s&with_keywords=%s&sort_by=popularity.desc' % (base_url, page_no, '210024')
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_moviesanime_latest_releases(page_no):
	current_date, previous_date = get_dates(181, reverse=True)
	string = 'tmdb_moviesanime_latest_releases_%s' % page_no
	url = '%s/discover/movie?page=%s&with_keywords=%s&with_release_type=4|5' % (base_url, page_no, '210024')
	url += '&sort_by=primary_release_date.desc&release_date.gte=%s&release_date.lte=%s' % (previous_date, current_date)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_moviesanime_genres(genre_id, page_no):
	string = 'tmdb_moviesanime_genres_%s_%s' % (genre_id, page_no)
	url = '%s/discover/movie?page=%s&with_keywords=%s&with_genres=%s&sort_by=popularity.desc' % (base_url, page_no, '210024', genre_id)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_moviesanime_year(year, page_no):
	string = 'tmdb_moviesanime_year_%s_%s' % (year, page_no)
	url = '%s/discover/movie?page=%s&with_keywords=%s' % (base_url, page_no, '210024')
	url += '&sort_by=popularity.desc&certification_country=US&primary_release_year=%s' % year
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_tvanime_popular(page_no):
	string = 'tmdb_tvanime_popular_%s' % page_no
	url = '%s/discover/tv?page=%s&with_keywords=%s&sort_by=popularity.desc' % (base_url, page_no, '210024')
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_tvanime_premieres(page_no):
	current_date, previous_date = get_dates(181, reverse=True)
	string = 'tmdb_tvanime_premieres_%s' % page_no
	url = '%s/discover/tv?page=%s&with_keywords=%s' % (base_url, page_no, '210024')
	url += '&sort_by=first_air_date.desc&first_air_date.gte=%s&first_air_date.lte=%s' % (previous_date, current_date)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_tvanime_genres(genre_id, page_no):
	string = 'tmdb_tvanime_genres_%s_%s' % (genre_id, page_no)
	url = '%s/discover/tv?page=%s&with_keywords=%s' % (base_url, page_no, '210024')
	url += '&sort_by=popularity.desc&include_null_first_air_dates=false&with_genres=%s' % genre_id
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_tvanime_year(year, page_no):
	string = 'tmdb_tvanime_year_%s_%s' % (year, page_no)
	url = '%s/discover/tv?page=%s&with_keywords=%s' % (base_url, page_no, '210024')
	url += '&sort_by=popularity.desc&include_null_first_air_dates=false&first_air_date_year=%s' % year
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_2_DAYS)

def tmdb_popular_people(page_no):
	string = 'tmdb_popular_people_%s' % page_no
	url = '%s/person/popular?language=en-US&page=%s' % (base_url, page_no)
	return cache_object(get_tmdb, string, url)

def tmdb_people_full_info(actor_id, language=None):
	if not language: language = get_language()
	string = 'tmdb_people_full_info_%s_%s' % (actor_id, language)
	url = '%s/person/%s?language=%s' % (base_url, actor_id, language)
	url += '&append_to_response=external_ids,combined_credits,images,tagged_images'
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_1_WEEK)

def tmdb_people_actor_info(actor_id):
	string = 'tmdb_people_actor_info_%s_en' % actor_id
	url = '%s/person/%s?language=en&append_to_response=combined_credits' % (base_url, actor_id)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_1_WEEK)

def tmdb_people_info(query):
	string = 'tmdb_people_info_%s' % query
	url = '%s/search/person?language=en-US&query=%s' % (base_url, query)
	return cache_object(get_tmdb, string, url, expiration=EXPIRES_4_HOURS)['results']

def get_dates(days, reverse=True):
	import datetime
	current_date = datetime.date.today()
	if reverse: new_date = (current_date - datetime.timedelta(days=days)).strftime('%Y-%m-%d')
	else: new_date = (current_date + datetime.timedelta(days=days)).strftime('%Y-%m-%d')
	return str(current_date), new_date

def tmdb_image_params(language):
	return ','.join(dict.fromkeys([language, language.split('-')[0], 'en,en-US,null']))

def movie_details(tmdb_id, language):
	try:
		url = '%s/movie/%s?language=%s&append_to_response=%s' % (base_url, tmdb_id, language, movies_append)
		if language not in 'en,en-US': url += '&include_image_language=%s' % tmdb_image_params(language)
		return get_tmdb(url)
	except: return None

def tvshow_details(tmdb_id, language):
	try:
		url = '%s/tv/%s?language=%s&append_to_response=%s' % (base_url, tmdb_id, language, tvshows_append)
		if language not in 'en,en-US': url += '&include_image_language=%s' % tmdb_image_params(language)
		return get_tmdb(url)
	except: return None

def season_episodes_details(tmdb_id, season_no, language):
	try:
		url = '%s/tv/%s/season/%s?language=%s&append_to_response=credits' % (base_url, tmdb_id, season_no, language)
		return get_tmdb(url)
	except: return None

def movie_external_id(external_source, external_id):
	try:
		string = 'movie_external_id_%s_%s' % (external_source, external_id)
		url = '%s/find/%s?external_source=%s' % (base_url, external_id, external_source)
		result = cache_function(get_tmdb, string, url, EXPIRES_1_MONTH)
		result = result['movie_results']
		if result: return result[0]
		else: return None
	except: return None

def tvshow_external_id(external_source, external_id):
	try:
		string = 'tvshow_external_id_%s_%s' % (external_source, external_id)
		url = '%s/find/%s?external_source=%s' % (base_url, external_id, external_source)
		result = cache_function(get_tmdb, string, url, EXPIRES_1_MONTH)
		result = result['tv_results']
		if result: return result[0]
		else: return None
	except: return None

def movie_keywords(tmdb_id):
	try:
		url = '%s/movie/%s/keywords' % (base_url, tmdb_id)
		result = get_tmdb(url)
		result = result['keywords']
		return result
	except: return None

def english_translation(mediatype, tmdb_id):
	try:
		string = 'english_translation_%s_%s' % (mediatype, tmdb_id)
		url = '%s/%s/%s/translations' % (base_url, mediatype, tmdb_id)
		result = cache_function(get_tmdb, string, url, EXPIRES_1_WEEK * 52)
		try: result = result['translations']
		except: result = None
		return result
	except: return None

def episode_groups(tmdb_id):
	def _process(dummy):
		eps_map = (dummy, 'Original air date', 'Absolute', 'DVD', 'Digital', 'Story arc', 'Production', 'TV')
		result = get_tmdb(url)['results']
		for i in result: i['type'] = eps_map[i['type']]
		return result
	string = 'tmdb_episode_group_%s' % tmdb_id
	url = '%s/tv/%s/episode_groups' % (base_url, tmdb_id)
	return cache_function(_process, string, url, EXPIRES_1_WEEK)

def episode_group_details(group_id):
	def _process(dummy):
		result = get_tmdb(url)
		result['groups'].sort(key=lambda k: k['order'])
		return result['groups']
	string = 'tmdb_episode_group_details_%s' % group_id
	url = '%s/tv/episode_group/%s' % (base_url, group_id)
	return cache_function(_process, string, url, EXPIRES_1_WEEK)

def tmdb_region_ids():
	def _process(dummy):
		region_list = (
			'AF,AL,DZ,AQ,AR,AM,AU,AT,BD,BY,BE,BR,BG,KH,CA,CL,CN,HR,CZ,DK,EG,FI,FR,DE,'
			'GR,HK,HU,IS,IN,ID,IR,IQ,IE,IL,IT,JP,MY,NP,NL,NZ,NO,PK,PY,PE,PH,PL,PT,PR,'
			'RO,RU,SA,RS,SG,SK,SI,ZA,ES,LK,SE,CH,TH,TR,UA,AE,GB,US,UY,VE,VN,YE,ZW'
		)
		return sorted((
			{'code': i['iso_3166_1'], 'name': i['english_name']}
			for i in get_tmdb(url) if i['iso_3166_1'] in region_list
		), key=lambda k: k['name'])
	string = 'tmdb_region_ids'
	url = '%s/configuration/countries' % base_url
	return cache_object(_process, string, url, expiration=EXPIRES_1_MONTH)
