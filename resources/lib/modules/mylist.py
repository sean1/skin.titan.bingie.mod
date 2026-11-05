from time import monotonic, monotonic_ns
from caches.mylist_cache import MyList, normalize_identity
from modules import kodi_utils


def _add_status(handle, status):
	item = kodi_utils.make_listitem()
	item.setLabel('My List is empty' if status == 'empty' else 'Could not load My List')
	item.setProperty('BingieMyListStatus', status)
	kodi_utils.add_item(handle, kodi_utils.build_url({'mode': 'navigator.my_list'}), item, False)


def build_list(params):
	from threading import Thread
	from menus.movies import Movies
	from menus.tvshows import TVShows
	from modules.utils import LIST_WORKERS, TaskPool

	handle = int(kodi_utils.argv1())
	try:
		store = MyList()
		movies = store.items('movie')[0]
		tvshows = store.items('tvshow')[0]
		entries = [(item, 'movie') for item in movies] + [(item, 'tvshow') for item in tvshows]
		entries.sort(key=lambda entry: (-entry[0]['saved_at'], entry[1], int(entry[0]['media_id'])))
		builders = {}
		if movies:
			builders['movie'] = Movies({**params, 'action': 'my_list_movies'})
			builders['movie'].saved_titles = {item['media_id']: item['title'] for item in movies}
		if tvshows:
			builders['tvshow'] = TVShows({**params, 'action': 'my_list_tvshows'})
			builders['tvshow'].saved_titles = {item['media_id']: item['title'] for item in tvshows}
		def build(position, item, mediatype):
			builder = builders[mediatype]
			if mediatype == 'movie': builder.build_movie_content(position, item['media_id'])
			else: builder.build_tvshow_content(position, item['media_id'])
		for thread in TaskPool(LIST_WORKERS).tasks(build, [(position, item, mediatype) for position, (item, mediatype) in enumerate(entries)], Thread): thread.join()
		items = [item for builder in builders.values() for item in builder.items]
		items.sort(key=lambda item: int(item[1].getProperty('pov_lite_sort_order')))
		if items: kodi_utils.add_items(handle, items)
		elif entries: _add_status(handle, 'error')
		else: _add_status(handle, 'empty')
	except Exception as error:
		kodi_utils.logger('My List', 'Could not load saved list: %s' % type(error).__name__)
		_add_status(handle, 'error')
	kodi_utils.set_content(handle, 'videos')
	kodi_utils.end_directory(handle, False)


def saved_status(mediatype, tmdb_id):
	try: return 'true' if MyList().contains(mediatype, tmdb_id) else 'false'
	except Exception as error:
		kodi_utils.logger('My List', 'Could not read saved state: %s' % type(error).__name__)
		return ''


def refresh_info_state(mediatype, tmdb_id, title=''):
	saved = saved_status(mediatype, tmdb_id)
	command = ''
	if saved:
		params = {'mode': 'my_list_from_info', 'action': 'remove' if saved == 'true' else 'add', 'mediatype': mediatype, 'tmdb_id': tmdb_id, 'title': title}
		command = 'RunPlugin(%s)' % kodi_utils.build_url(params)
	kodi_utils.set_property('PovInfoMyListSaved', saved)
	kodi_utils.set_property('PovInfoMyListCommand', command)


def context_item(mediatype, tmdb_id, title='', tvshow_id=None):
	try:
		mediatype, tmdb_id = normalize_identity(mediatype, tmdb_id, tvshow_id)
		saved = MyList().contains(mediatype, tmdb_id)
		params = {'mode': 'my_list_action', 'action': 'remove' if saved else 'add', 'mediatype': mediatype, 'tmdb_id': tmdb_id, 'title': title}
		return ('Remove from My List' if saved else 'Add to My List', 'RunPlugin(%s)' % kodi_utils.build_url(params))
	except Exception as error:
		kodi_utils.logger('My List', 'Could not prepare saved action: %s' % type(error).__name__)
		return None


def action(params):
	try:
		mediatype, tmdb_id = normalize_identity(params.get('mediatype'), params.get('tmdb_id'), params.get('tvshow_id'))
		choice = params.get('action')
		store = MyList()
		if choice == 'add': store.add(mediatype, tmdb_id, params.get('title', ''))
		elif choice == 'remove': store.remove(mediatype, tmdb_id)
		else: raise ValueError('My List requires an add or remove action')
	except Exception as error:
		kodi_utils.logger('My List', 'Could not update saved list: %s' % type(error).__name__)
		kodi_utils.notification('Could not update My List')
		return False
	info_id = kodi_utils.get_property('PovInfoTmdb') or kodi_utils.get_property('PovInfoPendingTmdb')
	if kodi_utils.get_property('PovInfoType') == mediatype and str(info_id) == str(tmdb_id):
		refresh_info_state(mediatype, tmdb_id, kodi_utils.get_property('PovInfoTitle'))
	kodi_utils.set_property('BingieMyListRefresh', str(monotonic_ns()))
	if kodi_utils.get_visibility('Window.IsActive(Videos)') and not kodi_utils.external_browse(): kodi_utils.container_refresh()
	elif kodi_utils.get_visibility('Window.IsActive(1123) | Window.IsActive(1122)'): kodi_utils.set_property('BingieMyListPendingRefresh', 'true')
	kodi_utils.notification('Added to My List' if choice == 'add' else 'Removed from My List')
	return True


def from_info(params):
	return action({
		**params, 'mediatype': params.get('mediatype', kodi_utils.get_property('PovInfoType')),
		'tmdb_id': params.get('tmdb_id', kodi_utils.get_property('PovInfoTmdb') or kodi_utils.get_property('PovInfoPendingTmdb')),
		'title': params.get('title', kodi_utils.get_property('PovInfoTitle'))
	})


def refresh_after_info():
	if not kodi_utils.get_property('BingieMyListPendingRefresh'): return
	kodi_utils.clear_property('BingieMyListPendingRefresh')
	deadline = monotonic() + 0.3
	while not kodi_utils.get_visibility('Window.IsActive(Videos) | Window.IsActive(Home)') and monotonic() < deadline: kodi_utils.sleep(10)
	if kodi_utils.get_visibility('Window.IsActive(Videos)') and not kodi_utils.external_browse(): kodi_utils.container_refresh()
