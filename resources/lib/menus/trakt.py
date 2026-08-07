from indexers import trakt_api, list_helper
from menus.episodes import Episodes
from menus.movies import Movies
from menus.seasons import Seasons
from menus.tvshows import TVShows
from modules import kodi_utils

KODI_VERSION, ls = kodi_utils.get_kodi_version(), kodi_utils.local_string
build_url, make_listitem = kodi_utils.build_url, kodi_utils.make_listitem
fanart = kodi_utils.get_addoninfo('fanart')
default_icon = kodi_utils.media_path('trakt.png')
add2menu_str, add2folder_str = ls(32730), ls(32731)
nextpage_str = ls(32799)

def search_trakt_lists(params):
	return SearchTraktLists(params).build()

def get_trakt_trending_popular_lists(params):
	return GetTrendingPopularLists(params).build()

def build_trakt_list(params):
	return TraktListBuilder(params).build()

class BaseTraktList(list_helper.BaseList):
	def process_results(self):
		for item in self.lists:
			try:
				item = self.parse_item(item)
				if not item: continue
				name, user, slug, list_id = item['name'], item['user']['ids']['slug'], item['ids']['slug'], item['ids']['trakt']
				item_count = item.get('item_count')
				url = build_url({'mode': 'build_trakt_list', 'user': user, 'slug': slug, 'list_id': list_id, 'list_type': 'user_lists', 'name': name})
				display, plot = self.get_display_and_plot(item, name, item_count, user)
				cm = [
					(add2menu_str, 'RunPlugin(%s)' % build_url({'mode': 'menu_editor.add_external', 'name': display, 'iconImage': 'trakt.png'})),
					(add2folder_str, 'RunPlugin(%s)' % build_url({'mode': 'menu_editor.shortcut_folder_add_item', 'name': display, 'iconImage': 'trakt.png'}))
				]
				listitem = make_listitem()
				listitem.setLabel(display)
				listitem.setArt({'icon': default_icon, 'poster': default_icon, 'thumb': default_icon, 'fanart': fanart, 'banner': default_icon})
				if plot: listitem.setInfo('video', {'plot': plot}) if KODI_VERSION < 20 else listitem.getVideoInfoTag().setPlot(plot)
				listitem.addContextMenuItems(cm)
				yield (url, listitem, True)
			except: pass

class SearchTraktLists(BaseTraktList):
	def __init__(self, params):
		super().__init__(params)
		self.page = params.get('new_page', '1')
		self.pages = self.page
		self.search_title = params.get('search_title') or kodi_utils.dialog.input('BINGIE Lite')
		self.category_name = self.search_title

	def fetch_results(self):
		if self.search_title: self.lists, self.pages = trakt_api.trakt_search_lists(self.search_title, self.page)
		else: self.lists, self.pages = [], self.page

	def parse_item(self, item):
		list_info = item[item['type']]
		if list_info['privacy'] == 'private' or list_info['item_count'] == 0: return None
		return list_info

	def add_next_page(self):
		if int(self.pages) <= int(self.page): return
		url = {'mode': 'build_trakt_list.search_trakt_lists', 'search_title': self.search_title, 'new_page': int(self.page) + 1}
		kodi_utils.add_dir(self.handle, url, nextpage_str)

class GetTrendingPopularLists(BaseTraktList):
	def __init__(self, params):
		super().__init__(params)
		self.list_type = params['list_type']

	def fetch_results(self):
		self.lists = trakt_api.trakt_trending_popular_lists(self.list_type)

	def parse_item(self, item):
		return item['list']

class TraktListBuilder(list_helper.BaseMediaListBuilder):
	mode = 'build_trakt_list'

	def __init__(self, params):
		super().__init__(params)
		self.slug = params.get('slug')
		self.list_type = 'user_lists'

	def fetch_results(self):
		return trakt_api.get_trakt_list_contents(self.list_type, self.list_id, self.user, self.slug)

	def process_media_types(self, queue, process_list):
		movies, tvshows = Movies({'id_type': 'trakt_dict'}), TVShows({'id_type': 'trakt_dict'})
		episodes, seasons = Episodes({'id_type': 'trakt_dict'}), Seasons({'id_type': 'trakt_dict'})
		for idx, tag in enumerate(process_list, 1):
			mtype = tag['type']
			if mtype == 'movie':
				queue.put((movies.build_movie_content, idx, tag[mtype]['ids']))
			elif mtype == 'show':
				queue.put((tvshows.build_tvshow_content, idx, tag[mtype]['ids']))
			elif mtype == 'episode':
				ids = {'media_ids': {'tmdb': tag['show']['ids']['tmdb']}, 'season': tag['episode']['season'], 'episode': tag['episode']['number']}
				queue.put((episodes.build_episode_content, idx, ids))
			elif mtype == 'season':
				ids = {'tmdb_id': tag['show']['ids']['tmdb'], 'season': tag['season']['number'], 'sort': idx}
				queue.put((seasons.build_season_list, ids))
		return {'movies': movies, 'tvshows': tvshows, 'episodes': episodes, 'seasons': seasons}

	def get_url_params(self):
		params = super().get_url_params()
		params.update({'slug': self.slug, 'list_type': self.list_type})
		return params
