from threading import Thread
from indexers.metadata import movie_meta, art_infodict, movie_show_infodict, tmdb_image_base, main_actors, resized_cast
from caches.watched_cache import get_watched_info_movie, get_watched_status_movie, get_bookmarks, get_resumetime, set_resumetime
from modules import kodi_utils, settings
from modules.meta_lists import movie_genres
from menus.media import build_tmdb_detail_shelf_item, complete_media_directory
#from modules.utils import manual_function_import, get_datetime, make_thread_list_enumerate, chunks
from modules.utils import LIST_WORKERS, manual_function_import, get_datetime, media_percentage_properties, valid_tmdb_id, TaskPool
# logger = kodi_utils.logger

KODI_VERSION, make_cast_list, default_duration = kodi_utils.get_kodi_version(), kodi_utils.make_cast_list, 3600
string, ls, build_url, get_infolabel = str, kodi_utils.local_string, kodi_utils.build_url, kodi_utils.get_infolabel
run_plugin, container_refresh, container_update = 'RunPlugin(%s)', 'Container.Refresh(%s)', 'Container.Update(%s)'
fanart_empty = kodi_utils.get_addoninfo('fanart')
poster_empty = kodi_utils.media_path('box_office.png')
item_jump = kodi_utils.media_path('item_jump.png')
item_next = kodi_utils.media_path('item_next.png')
watched_str, unwatched_str = ls(32642), ls(32643)
extras_str, options_str, recomm_str = ls(32645), ls(32646), '[B]%s...[/B]' % ls(32503)
hide_str, exit_str, clearprog_str, play_str = ls(32648), ls(32649), ls(32651), '[B]%s...[/B]' % ls(32174)
nextpage_str, switchjump_str, jumpto_str = ls(32799), ls(32784), ls(32964)
PREFETCH_THREADS = 5
MOVIE_GENRE_NAMES = {int(value[0]): name for name, value in movie_genres.items()}

class Movies:
	def __init__(self, params):
		self.params = params
		self.id_type = self.params.get('id_type', 'tmdb_id')
		self.list = self.params.get('list', [])
		self.action = self.params.get('action')
		self.exit_list_params = self.params.get('exit_list_params')
		self.items, self.new_page, self.total_pages = [], {}, None
		self.append = self.items.append
		self.is_detail_shelf = self.action in ('tmdb_movies_more_like_this', 'tmdb_movies_in_collection')
		self.is_summary_listing = self.is_detail_shelf or bool(self.action and self.action.startswith('tmdb_movies_'))
		self.is_widget = kodi_utils.external_browse()
		if not self.exit_list_params: self.exit_list_params = get_infolabel('Container.FolderPath')
		self._full_context_ready = False
		if self.is_summary_listing: return
		self._initialize_full_context()

	def _initialize_full_context(self):
		if self._full_context_ready: return
		self.current_date = get_datetime()
		self.meta_user_info = settings.metadata_user_info()
		self.watched_indicators = settings.watched_indicators()
		self.watched_title = settings.watched_title(self.watched_indicators)
		self.watched_info = get_watched_info_movie(self.watched_indicators)
		self.bookmarks = get_bookmarks(self.watched_indicators, 'movie')
		self.include_year_in_title = settings.include_year_in_title('movie')
		self.open_extras = settings.extras_open_action('movie')
		self.cm_sort = settings.context_menu_sort()
		self.widget_hide_watched = self.is_widget and self.meta_user_info['widget_hide_watched']
		self.art_provider = (*settings.get_art_provider(), poster_empty, fanart_empty)
		self._full_context_ready = True

	def build_movie_shelf_content(self, position, item):
		try:
			result = build_tmdb_detail_shelf_item(position, item, self.params.get('tmdb_id'), 'movie', MOVIE_GENRE_NAMES, poster_empty, fanart_empty, KODI_VERSION)
			if result: self.append(result)
		except: pass

	def build_movie_content(self, position, tag):
		try:
			meta = movie_meta(self.id_type, tag, self.meta_user_info, self.current_date)
			meta_get = meta.get
			if not meta or meta_get('blank_entry', False): return
			playcount, overlay = get_watched_status_movie(self.watched_info, string(meta['tmdb_id']))
			if self.widget_hide_watched and playcount: return
			meta.update({'playcount': playcount, 'overlay': overlay})
			resumetime, progress = get_resumetime(self.bookmarks, string(meta['tmdb_id']))
			cm = []
			cm_append = cm.append
			rootname, title, year = meta_get('rootname'), meta_get('title'), meta_get('year')
			display = rootname if self.include_year_in_title else title
			tmdb_id, imdb_id = meta_get('tmdb_id'), meta_get('imdb_id')
			try: tags = [i for i in (imdb_id, string(tmdb_id)) if i]
			except: tags = []
			play_params = build_url({
				'mode': 'play_media', 'mediatype': 'movie', 'tmdb_id': tmdb_id
			})
			extras_params = build_url({
				'mode': 'extras_menu_choice', 'mediatype': 'movie',
				'tmdb_id': tmdb_id, 'is_widget': self.is_widget
			})
			options_params = build_url({
				'mode': 'options_menu_choice', 'mediatype': 'movie',
				'tmdb_id': tmdb_id, 'is_widget': self.is_widget
			})
			recommended_params = build_url({
				'mode': 'build_movie_list', 'action': 'tmdb_movies_recommendations', 'tmdb_id': tmdb_id
			})
			cm_append((self.cm_sort['options'], options_str, run_plugin % options_params))
			if self.open_extras:
				url_params = extras_params
				cm_append((self.cm_sort['extras'], play_str, run_plugin % play_params))
			else:
				url_params = play_params
				cm_append((self.cm_sort['extras'], extras_str, run_plugin % extras_params))
			if progress != '0' or resumetime != '0': cm_append((
				self.cm_sort['mark'], clearprog_str, run_plugin % build_url({
					'mode': 'watched_unwatched_erase_bookmark', 'mediatype': 'movie',
					'tmdb_id': tmdb_id, 'refresh': 'true'
			})))
			if playcount: cm_append((
				self.cm_sort['mark'], unwatched_str % self.watched_title, run_plugin % build_url({
					'mode': 'mark_as_watched_unwatched_movie', 'action': 'mark_as_unwatched',
					'tmdb_id': tmdb_id, 'title': title, 'year': year
			})))
			else: cm_append((
				self.cm_sort['mark'], watched_str % self.watched_title, run_plugin % build_url({
					'mode': 'mark_as_watched_unwatched_movie', 'action': 'mark_as_watched',
					'tmdb_id': tmdb_id, 'title': title, 'year': year
			})))
			cm_append((self.cm_sort['exit'], exit_str, container_refresh % self.exit_list_params))
			cm.sort(key=lambda k: k[0])
			cm = [v for k, *v in cm if k]
			props = {
				'PovLiteItem': 'true', 'pov_lite_sort_order': string(position), 'watchedprogress': progress,
				'main_actors': main_actors(meta_get('cast', [])),
				'PovInfoCollectionId': string((meta_get('extra_info') or {}).get('collection_id') or ''),
				'PovInfoSourceTmdb': string(self.params.get('tmdb_id') or '')
			}
			props.update(media_percentage_properties(meta_get('rating'), progress))
			listitem = kodi_utils.make_listitem()
			listitem.addContextMenuItems(cm)
			listitem.setProperties(props)
			listitem.setLabel(display)
			listitem.setArt(art_infodict(meta, self.art_provider, self.meta_user_info))
			if self.action in ('tmdb_movies_more_like_this', 'tmdb_movies_in_collection'):
				url_params = build_url({'mode': 'show_media_info', 'mediatype': 'movie', 'tmdb_id': tmdb_id})
			if KODI_VERSION < 20:
				listitem.setUniqueIDs({'imdb': imdb_id, 'tmdb': string(tmdb_id)})
				listitem.setInfo('video', movie_show_infodict(meta))
				listitem.setCast(resized_cast(meta_get('cast', [])))
				listitem.setProperty('resumetime', resumetime)
			else:
				videoinfo = listitem.getVideoInfoTag(offscreen=True)
				videoinfo.setTitle(display)
				videoinfo.setUniqueIDs({'imdb': imdb_id, 'tmdb': string(tmdb_id)})
				videoinfo.setCast(make_cast_list(resized_cast(meta_get('cast', []))))
				videoinfo.setCountries(meta_get('country'))
				videoinfo.setDirectors(meta_get('director').split(', '))
				videoinfo.setDuration(int(meta_get('duration') or default_duration))
				videoinfo.setGenres(meta_get('genre').split(', '))
				videoinfo.setIMDBNumber(imdb_id)
				videoinfo.setMediaType('movie')
				videoinfo.setMpaa(meta_get('mpaa'))
				videoinfo.setPlaycount(playcount)
				videoinfo.setPlot(meta_get('plot'))
				videoinfo.setPremiered(meta_get('premiered'))
				videoinfo.setRating(meta_get('rating'))
				videoinfo.setResumePoint(*set_resumetime(resumetime, progress, videoinfo.getDuration()))
				videoinfo.setStudios((meta_get('studio'),))
				videoinfo.setTagLine(meta_get('tagline'))
				videoinfo.setTags(tags)
				videoinfo.setTrailer(meta_get('trailer'))
				videoinfo.setVotes(meta_get('votes'))
				videoinfo.setWriters(meta_get('writer').split(', '))
			self.append((url_params, listitem, False))
		except: pass

class Menu(Movies):
	personal_dict = {'watched_movies': ('caches.watched_cache', 'get_watched_movie_tvshow'), 'in_progress_movies': ('caches.watched_cache', 'get_in_progress_items')}
	tmdb_special_key_dict = {'tmdb_movies_networks': 'company', 'tmdb_movies_year': 'year'}
	tmdb_main = ('tmdb_movies_trending_day', 'tmdb_movies_trending', 'tmdb_movies_popular', 'tmdb_movies_now_playing', 'tmdb_movies_upcoming', 'tmdb_movies_top_rated')
	similar = ('tmdb_movies_similar', 'tmdb_movies_recommendations', 'tmdb_movies_more_like_this', 'tmdb_movies_in_collection')
	personalized = ('tmdb_movies_because_you_watched',)

	def build_movies_results(self):
		full_items = []
		for position, item in enumerate(self.list):
			if isinstance(item, dict) and item.get('id') and (item.get('title') or item.get('original_title')): self.build_movie_shelf_content(position, item)
			else: full_items.append((position, item.get('id') if isinstance(item, dict) and 'id' in item else item))
		if full_items:
			self._initialize_full_context()
			for thread in TaskPool(LIST_WORKERS).tasks(self.build_movie_content, full_items, Thread): thread.join()
		self.items.sort(key=lambda k: int(k[1].getProperty('pov_lite_sort_order')))
		return self.items

	def prefetch_metadata(self):
		self._initialize_full_context()
		def fetch(tag): movie_meta(self.id_type, tag.get('id') if self.id_type == 'tmdb_id' and isinstance(tag, dict) and 'id' in tag else tag, self.meta_user_info, self.current_date)
		for thread in TaskPool(PREFETCH_THREADS).tasks(fetch, [(tag,) for tag in self.list], Thread): thread.join()

	def build_collections_results(self):
		for item in self.list:
			try:
				url_params = build_url({'mode': 'build_movie_list', 'action': 'tmdb_movies_collection', 'collection_id': item['id']})
				poster_path, backdrop_path = item['poster_path'], item['backdrop_path']
				if poster_path: poster = tmdb_image_base % ('w342', poster_path)
				else: poster = poster_empty
				if backdrop_path: fanart = tmdb_image_base % ('w1280', backdrop_path)
				else: fanart = fanart_empty
				listitem = kodi_utils.make_listitem()
				listitem.setLabel(item['name'])
				listitem.setInfo('Video', {'plot': item['overview']})
				listitem.setArt({'icon': poster, 'fanart': fanart})
				self.append((url_params, listitem, True))
			except: pass
		return self.items

	def run(self):
		__handle__ = int(kodi_utils.argv1())
		prefetch = self.params.get('prefetch') == 'true'
		limited_tmdb = False
		params_get = self.params.get
		worker, view_type, content_type = self.build_movies_results, 'view.movies', 'movies'
		mode, category = params_get('mode'), ''
		try:
			category = ls(params_get('name'))
			try: item_limit = int(params_get('limit', '0'))
			except (TypeError, ValueError): item_limit = 0
			limited_tmdb = item_limit > 0 and self.action in Menu.tmdb_main
			try: page_no = int(params_get('new_page', '1'))
			except ValueError: page_no = params_get('new_page')
			if self.action in Menu.personal_dict: var_module, import_function = Menu.personal_dict[self.action]
			else: var_module, import_function = 'indexers.%s_api' % self.action.split('_')[0], self.action
			try: function = manual_function_import(var_module, import_function)
			except: pass
			if self.action in Menu.tmdb_main:
				data = function(page_no)
				results = data['results'][:item_limit] if limited_tmdb else data['results']
				self.list = results
				total_pages = data['total_pages']
				if total_pages > page_no: self.new_page = {'new_page': string(data['page'] + 1)}
			elif self.action in Menu.personal_dict:
				watched_info = self.bookmarks if self.action == 'in_progress_movies' else self.watched_info
				data, total_pages = function(watched_info, 'movie', page_no)
				if self.action == 'in_progress_movies' and item_limit > 0: data = data[:item_limit]
				self.list = [i['media_id'] for i in data]
				if total_pages > 2: self.total_pages = total_pages
				if total_pages > page_no: self.new_page = {'new_page': string(page_no + 1)}
			elif self.action in Menu.similar:
				tmdb_id = params_get('tmdb_id')
				if not valid_tmdb_id(tmdb_id): data = {'results': [], 'page': 1, 'total_pages': 1}
				elif self.action == 'tmdb_movies_in_collection': data = function(tmdb_id, page_no, params_get('collection_id'))
				else: data = function(tmdb_id, page_no)
				self.list = data['results']
				if data['page'] < data['total_pages']:
					self.new_page = {'new_page': string(data['page'] + 1), 'tmdb_id': tmdb_id}
			elif self.action in Menu.personalized:
				watched_info = get_watched_info_movie(settings.watched_indicators())
				seed_item = max((i for i in watched_info.values() if valid_tmdb_id(i[0])), key=lambda k: k[2], default=None)
				seed = seed_item[0] if seed_item else None
				data = function(seed, watched_info.keys())
				self.list = data['results'][:item_limit] if item_limit > 0 else data['results']
			elif self.action in Menu.tmdb_special_key_dict:
				key = Menu.tmdb_special_key_dict[self.action]
				function_var = params_get(key)
				if not function_var: return
				data = function(function_var, page_no)
				self.list = data['results']
				if data['page'] < data['total_pages']:
					self.new_page = {'new_page': string(data['page'] + 1), key: function_var}
			elif self.action == 'tmdb_movies_discover':
				name, query = params_get('name'), params_get('query')
				data = function(query, page_no)
				try: target_results = int(params_get('target_results', '0'))
				except (TypeError, ValueError): target_results = 0
				fallback_query = params_get('fallback_query')
				if page_no == 1 and fallback_query and fallback_query != query and target_results > 0 and data.get('total_results', 0) < target_results:
					query, data = fallback_query, function(fallback_query, page_no)
				self.list = data['results'][:item_limit] if item_limit > 0 else data['results']
				try: max_pages = int(params_get('max_pages', '0'))
				except (TypeError, ValueError): max_pages = 0
				if data['page'] < data['total_pages'] and (max_pages < 1 or data['page'] < max_pages):
					self.new_page = {'query': query, 'name': name, 'new_page': string(data['page'] + 1)}
					if max_pages > 0: self.new_page['max_pages'] = string(max_pages)
			elif self.action == 'tmdb_movies_genres':
				genre_id = params_get('genre_id')
				if not genre_id: return
				data = function(genre_id, page_no)
				self.list = data['results']
				if data['page'] < data['total_pages']:
					self.new_page = {'new_page': string(data['page'] + 1), 'genre_id': genre_id}
			elif self.action == 'tmdb_movies_search':
				query = params_get('query')
				data = function(query, page_no)
				self.list = data['results']
				total_pages = data['total_pages']
				if total_pages > page_no: self.new_page = {'new_page': string(page_no + 1), 'query': query}
			elif self.action == 'tmdb_movies_search_collections':
				worker, view_type, content_type = self.build_collections_results, 'view.main', ''
				query = params_get('query')
				data = function(query, page_no)
				self.list = data['results']
				total_pages = data['total_pages']
				if total_pages > page_no: self.new_page = {'new_page': string(page_no + 1), 'query': query}
			elif self.action == 'tmdb_movies_collection':
				data = function(params_get('collection_id'))['parts']
				data.sort(key=lambda k: k['release_date'] or '2050')
				self.list = data
			if prefetch:
				if worker == self.build_movies_results and not self.is_summary_listing: self.prefetch_metadata()
				return
			if self.total_pages and not self.is_widget and settings.nav_jump_use_alphabet():
				url_params = {
					'mode': 'build_navigate_to_page', 'current_page': page_no, 'total_pages': self.total_pages,
					'query': params_get('search_name', ''), 'actor_id': params_get('actor_id', ''),
					'transfer_mode': mode, 'transfer_action': self.action, 'mediatype': 'Movies'
				}
				kodi_utils.add_dir(__handle__, url_params, jumpto_str, item_jump, isFolder=False)
			kodi_utils.add_items(__handle__, worker())
		except: pass
		if prefetch: return
		complete_media_directory(
			__handle__, mode, self.action, self.exit_list_params, category, content_type, view_type, self.is_widget, self.new_page, limited_tmdb,
			self.params, nextpage_str, item_next
		)
