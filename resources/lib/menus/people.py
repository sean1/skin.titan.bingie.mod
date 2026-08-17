import json
import sys
from datetime import datetime
from time import monotonic, monotonic_ns
from urllib.parse import unquote
from caches.window_property_cache import WindowPropertyCache
from indexers.tmdb_api import tmdb_people_info, tmdb_people_actor_info, tmdb_image_base, resized_tmdb_image
from menus.images import Images
from modules import kodi_utils, settings
from modules.utils import calculate_age, valid_tmdb_id
# from modules.kodi_utils import logger

KODI_VERSION = kodi_utils.get_kodi_version()
ls, build_url, make_listitem = kodi_utils.local_string, kodi_utils.build_url, kodi_utils.make_listitem
fanart_empty = kodi_utils.get_addoninfo('fanart')
poster_empty = kodi_utils.media_path('people.png')
gender_dict = {0: '', 1: ls(32844), 2: ls(32843), 3: ls(32466)}
actor_properties = (
	'PovActorId', 'PovActorName', 'PovActorProfile', 'PovActorBiography', 'PovActorLifespan', 'PovActorBirthplace', 'PovActorBackdrop',
	'PovActorHasMovies', 'PovActorHasTVShows', 'PovActorHasDirected', 'PovActorReady'
)
excluded_genres = {99, 10763, 10764, 10767}
actor_window_id = 1122
actor_hydration_property = 'PovActorHydrationRequest'
actor_credit_types = ('movies', 'tvshows', 'directed')
actor_credit_focus = (('movies', 'PovActorHasMovies', 610), ('tvshows', 'PovActorHasTVShows', 620), ('directed', 'PovActorHasDirected', 630))
actor_credit_cache = WindowPropertyCache('pov_lite_actor_credits_v1_registry', 18)
actor_credit_cache_prefix = '%s_%s'
actor_credit_fill_prefix = 'PovActorCreditsFill.%s'
credit_snapshot_keys = (
	'media_type', 'id', 'title', 'name', 'original_title', 'original_name', 'release_date', 'first_air_date', 'backdrop_path', 'poster_path', 'vote_average', 'overview'
)

def popular_people():
	Images().run({'mode': 'popular_people_image_results', 'page_no': 1})

def person_data_dialog(params):
	show_person_info(params)

def _clear_actor_properties():
	for prop in actor_properties: kodi_utils.clear_property(prop)

def _actor_request_current(actor_id, request, require_window=False):
	current = kodi_utils.get_property(actor_hydration_property) == request and kodi_utils.get_property('PovActorId') == str(actor_id)
	return current and (not require_window or kodi_utils.get_visibility('Window.IsActive(%s)' % actor_window_id))

def _wait_for_actor_window(actor_id, request):
	deadline = monotonic() + 2.0
	while _actor_request_current(actor_id, request) and not kodi_utils.get_visibility('Window.IsActive(%s)' % actor_window_id) and monotonic() < deadline:
		if kodi_utils.monitor.abortRequested(): return False
		kodi_utils.sleep(25)
	return _actor_request_current(actor_id, request, require_window=True)

def _set_pending_actor_properties(actor_id, params):
	_clear_actor_properties()
	name = unquote(params.get('actor_name') or params.get('query') or '').strip()
	profile = resized_tmdb_image(params.get('actor_image'), 'w342') or poster_empty
	kodi_utils.set_property('PovActorId', str(actor_id))
	kodi_utils.set_property('PovActorName', name)
	kodi_utils.set_property('PovActorProfile', profile)
	kodi_utils.set_property('PovActorReady', 'false')

def _actor_focus_condition(target):
	condition = 'Window.IsActive(%s) + Control.HasFocus(600)' % actor_window_id
	if target != 699: condition += ' + String.IsEqual(Container(%s).ListItemAbsolute(0).Property(PovActorSourceId),Window(Home).Property(PovActorId))' % target
	return condition

def _focus_actor_page(credits):
	if not kodi_utils.get_visibility('Window.IsActive(%s)' % actor_window_id): return
	if credits.get('movies'): target = 610
	elif credits.get('tvshows'): target = 620
	elif credits.get('directed'): target = 630
	else: target = 699
	kodi_utils.execute_builtin('AlarmClock(PovActorFocus,SetFocus(%s),00:00:01,silent,loop)' % target)
	actor_id = kodi_utils.get_property('PovActorId')
	if not actor_id: return
	condition = _actor_focus_condition(target)
	deadline = monotonic() + 0.25
	eligible = kodi_utils.get_visibility(condition)
	while kodi_utils.get_property('PovActorId') == actor_id and not eligible and monotonic() < deadline:
		if kodi_utils.monitor.abortRequested(): return
		kodi_utils.sleep(25)
		eligible = kodi_utils.get_visibility(condition)
	if kodi_utils.get_property('PovActorId') == actor_id and eligible: kodi_utils.execute_builtin('SetFocus(%s)' % target)

def _focus_loaded_actor_shelf(actor_id, credit_type, has_items):
	if not has_items or kodi_utils.get_property('PovActorId') != str(actor_id) or kodi_utils.get_property('PovActorReady') != 'true': return
	focus = next(((candidate, target) for candidate, prop, target in actor_credit_focus if kodi_utils.get_property(prop) == 'true'), None)
	if not focus or focus[0] != credit_type: return
	if not kodi_utils.get_visibility(_actor_focus_condition(focus[1])): return
	kodi_utils.execute_builtin('SetFocus(%s)' % focus[1])

def _image_key(value):
	if not value: return ''
	return unquote(str(value)).split('?', 1)[0].rstrip('/').rsplit('/', 1)[-1].lower()

def _resolve_person_id(params):
	actor_id = params.get('actor_id')
	if actor_id:
		try: return str(int(actor_id))
		except: pass
	query = unquote(params.get('query') or params.get('actor_name') or '').strip()
	if not query: return None
	results = tmdb_people_info(query)
	if not results: return None
	image_key = _image_key(params.get('actor_image'))
	if image_key:
		matched = next((item for item in results if _image_key(item.get('profile_path')) == image_key), None)
		if matched: return matched['id']
	query_lower = query.lower()
	exact = next((item for item in results if str(item.get('name', '')).lower() == query_lower), None)
	return (exact or results[0])['id']

def _is_non_acting_role(character):
	role = (character or '').strip().lower()
	if 'archive footage' in role or 'archive sound' in role: return True
	return role in ('self', 'himself', 'herself') or role.startswith(('self -', 'self (', 'himself -', 'himself (', 'herself -', 'herself ('))

def _credit_score(item):
	try: popularity = float(item.get('popularity') or 0)
	except: popularity = 0.0
	try: votes = int(item.get('vote_count') or 0)
	except: votes = 0
	try: rating = float(item.get('vote_average') or 0)
	except: rating = 0.0
	date = item.get('release_date') or item.get('first_air_date') or ''
	return date, popularity, votes, rating

def _filtered_credits(person_info):
	combined = person_info.get('combined_credits') or {}
	acting = combined.get('cast') or []
	crew = combined.get('crew') or []
	movies = [item for item in acting if item.get('media_type') == 'movie' and not excluded_genres.intersection(item.get('genre_ids') or []) and not _is_non_acting_role(item.get('character'))]
	tvshows = [item for item in acting if item.get('media_type') == 'tv' and not excluded_genres.intersection(item.get('genre_ids') or []) and not _is_non_acting_role(item.get('character'))]
	directed = [item for item in crew if item.get('media_type') in ('movie', 'tv') and str(item.get('job', '')).lower() == 'director']
	return {key: _dedupe_and_sort(value) for key, value in (('movies', movies), ('tvshows', tvshows), ('directed', directed))}

def _dedupe_and_sort(data):
	unique = {}
	for item in data:
		key = item.get('media_type'), item.get('id')
		if not key[1] or not item.get('backdrop_path'): continue
		if key not in unique or _credit_score(item) > _credit_score(unique[key]): unique[key] = item
	return sorted(unique.values(), key=_credit_score, reverse=True)

def _credit_snapshot(item):
	return {key: item.get(key) for key in credit_snapshot_keys if item.get(key) not in (None, '')}

def _cache_actor_credits(actor_id, credits):
	snapshots = {credit_type: [_credit_snapshot(item) for item in credits.get(credit_type, ())] for credit_type in actor_credit_types}
	for credit_type, items in snapshots.items(): actor_credit_cache.set(actor_credit_cache_prefix % (actor_id, credit_type), items)
	return snapshots

def _cached_actor_credits(actor_id, credit_type):
	cached = actor_credit_cache.get(actor_credit_cache_prefix % (actor_id, credit_type))
	return cached if isinstance(cached, list) else None

def _wait_for_actor_credits(actor_id, credit_type, fill_property):
	deadline = monotonic() + 12.0
	while kodi_utils.get_property(fill_property) and monotonic() < deadline:
		cached = _cached_actor_credits(actor_id, credit_type)
		if cached is not None: return cached
		if kodi_utils.monitor.abortRequested(): return None
		kodi_utils.sleep(50)
	return _cached_actor_credits(actor_id, credit_type)

def _load_actor_credits(actor_id, credit_type):
	cached = _cached_actor_credits(actor_id, credit_type)
	if cached is not None: return cached
	fill_property = actor_credit_fill_prefix % actor_id
	if kodi_utils.get_property(fill_property):
		cached = _wait_for_actor_credits(actor_id, credit_type, fill_property)
		if cached is not None: return cached
	fill_token = str(monotonic_ns())
	kodi_utils.set_property(fill_property, fill_token)
	kodi_utils.sleep(25)
	if kodi_utils.get_property(fill_property) != fill_token:
		cached = _wait_for_actor_credits(actor_id, credit_type, fill_property)
		if cached is not None: return cached
		kodi_utils.set_property(fill_property, fill_token)
	try:
		person_info = tmdb_people_actor_info(actor_id)
		if not person_info: raise ValueError('Invalid actor credit response')
		return _cache_actor_credits(actor_id, _filtered_credits(person_info)).get(credit_type, [])
	finally:
		if kodi_utils.get_property(fill_property) == fill_token: kodi_utils.clear_property(fill_property)

def _display_date(value):
	if not value: return ''
	try: return datetime.strptime(value, '%Y-%m-%d').strftime('%B %d, %Y').replace(' 0', ' ')
	except: return value

def _lifespan(person_info):
	birthday, deathday = person_info.get('birthday'), person_info.get('deathday')
	if not birthday: return ''
	age = calculate_age(birthday, '%Y-%m-%d', deathday) if deathday else calculate_age(birthday, '%Y-%m-%d')
	if deathday: return '%s – %s · Age %s' % (_display_date(birthday), _display_date(deathday), age)
	return 'Born %s · Age %s' % (_display_date(birthday), age)

def _actor_backdrop(credits, resolution):
	all_credits = credits['movies'] + credits['tvshows'] + credits['directed']
	best = max(all_credits, key=_credit_score, default={})
	path = best.get('backdrop_path')
	return tmdb_image_base % (resolution, path) if path else fanart_empty

def show_person_info(params):
	if kodi_utils.get_property('PovActorTransition'): return
	transition_token = str(monotonic_ns())
	kodi_utils.set_property('PovActorTransition', transition_token)
	try:
		person_id = _resolve_person_id(params)
		if not person_id: return kodi_utils.notification(32760)
		from modules.dialogs import push_native_info_state, push_pov_page_state, reset_pov_page_history
		active_info = kodi_utils.get_visibility('Window.IsActive(1123)')
		active_actor = kodi_utils.get_visibility('Window.IsActive(1122)')
		active_native_info = kodi_utils.get_visibility('Window.IsActive(DialogVideoInfo.xml)') and not active_info
		if active_info: push_pov_page_state('info')
		elif active_actor: push_pov_page_state('actor')
		else:
			reset_pov_page_history()
			if active_native_info: push_native_info_state()
		if kodi_utils.get_property('BingieTrailerPreview') == 'true':
			kodi_utils.execute_builtin('PlayerControl(Stop)')
			kodi_utils.clear_property('BingieTrailerPreview')
		if active_native_info:
			kodi_utils.execute_builtin('Dialog.Close(movieinformation)')
			close_deadline = monotonic() + 2.0
			while kodi_utils.get_visibility('Window.IsActive(DialogVideoInfo.xml)') and monotonic() < close_deadline: kodi_utils.sleep(50)
			if kodi_utils.get_visibility('Window.IsActive(DialogVideoInfo.xml)'): return
		_set_pending_actor_properties(person_id, params)
		hydration_token = '|'.join((transition_token, str(person_id)))
		kodi_utils.set_property(actor_hydration_property, hydration_token)
		if kodi_utils.get_property('PovActorTransition') == transition_token: kodi_utils.clear_property('PovActorTransition')
		kodi_utils.execute_builtin(('%sWindow(%s)' % ('Replace' if active_actor else 'Activate', actor_window_id)))
		hydrate_url = build_url({'mode': 'hydrate_person_info', 'actor_id': person_id, 'request': hydration_token})
		kodi_utils.execute_builtin('RunPlugin(%s)' % hydrate_url)
	except Exception as exc:
		_clear_actor_properties()
		kodi_utils.logger('show_person_info', str(exc))
		kodi_utils.notification(32760)
	finally:
		if kodi_utils.get_property('PovActorTransition') == transition_token: kodi_utils.clear_property('PovActorTransition')

def hydrate_person_info(params):
	actor_id, request = params.get('actor_id'), params.get('request')
	if not actor_id or not request or not _actor_request_current(actor_id, request): return
	try:
		person_info = tmdb_people_actor_info(actor_id)
		if not person_info: raise ValueError('Invalid actor metadata response')
		if not _wait_for_actor_window(actor_id, request): return
		credits = _filtered_credits(person_info)
		resolution = settings.get_resolution()
		_cache_actor_credits(actor_id, credits)
		values = {
			'PovActorBiography': person_info.get('biography') or '', 'PovActorLifespan': _lifespan(person_info),
			'PovActorBirthplace': person_info.get('place_of_birth') or '', 'PovActorBackdrop': _actor_backdrop(credits, resolution['fanart']),
			'PovActorHasMovies': str(bool(credits['movies'])).lower(), 'PovActorHasTVShows': str(bool(credits['tvshows'])).lower(),
			'PovActorHasDirected': str(bool(credits['directed'])).lower()
		}
		for prop, value in values.items():
			if not _actor_request_current(actor_id, request, require_window=True): return
			kodi_utils.set_property(prop, str(value)) if value else kodi_utils.clear_property(prop)
		if _actor_request_current(actor_id, request, require_window=True):
			kodi_utils.set_property('PovActorReady', 'true')
			_focus_actor_page(credits)
	except Exception as exc:
		if _wait_for_actor_window(actor_id, request):
			for prop in ('PovActorBiography', 'PovActorLifespan', 'PovActorBirthplace', 'PovActorBackdrop'): kodi_utils.clear_property(prop)
			for prop in ('PovActorHasMovies', 'PovActorHasTVShows', 'PovActorHasDirected'): kodi_utils.set_property(prop, 'false')
			kodi_utils.set_property('PovActorReady', 'true')
			_focus_actor_page({})
		kodi_utils.logger('hydrate_person_info', str(exc))
	finally:
		if kodi_utils.get_property(actor_hydration_property) == request: kodi_utils.clear_property(actor_hydration_property)

def _info_cast_snapshot(media_type, tmdb_id):
	try:
		payload = json.loads(kodi_utils.get_property('PovInfoCastSnapshot') or '')
		if not isinstance(payload, dict): return None
		if payload.get('mediatype') != media_type or str(payload.get('tmdb_id')) != str(tmdb_id): return None
		cast = payload.get('cast')
		return cast if isinstance(cast, list) else None
	except: return None

def _signal_info_shelves(media_type, tmdb_id):
	if kodi_utils.get_property('PovInfoType') != media_type or kodi_utils.get_property('PovInfoTmdb') != str(tmdb_id): return
	kodi_utils.set_property('PovInfoMoreLikeThisReady', '1')
	if media_type == 'movie' and kodi_utils.get_property('PovInfoCollectionId'):
		kodi_utils.set_property('PovInfoCollectionReady', '1')

def build_media_cast(params):
	handle = int(kodi_utils.argv1())
	items = []
	media_type, tmdb_id = params.get('mediatype'), params.get('tmdb_id')
	try:
		if media_type not in ('movie', 'tvshow') or not valid_tmdb_id(tmdb_id): raise ValueError('Invalid media cast request')
		cast = _info_cast_snapshot(media_type, tmdb_id)
		if cast is None:
			from modules.dialogs import get_media_metadata
			meta = get_media_metadata(media_type, tmdb_id)
			if not meta or meta.get('blank_entry'): raise ValueError('Invalid media cast request')
			cast = meta.get('cast') or []
		for actor in cast:
			if len(items) == 14: break
			if not isinstance(actor, dict): continue
			name, role = str(actor.get('name') or '').strip(), str(actor.get('role') or '')
			thumbnail = resized_tmdb_image(actor.get('thumbnail'), 'w342') or poster_empty
			if not name: continue
			url_params = {'mode': 'show_person_info', 'query': name, 'actor_image': thumbnail}
			if actor.get('actor_id'): url_params['actor_id'] = str(actor['actor_id'])
			url = build_url(url_params)
			listitem = make_listitem()
			listitem.setLabel(name)
			listitem.setLabel2(role)
			listitem.setArt({'icon': thumbnail, 'thumb': thumbnail, 'poster': thumbnail})
			listitem.setProperties({'PovActorPath': url, 'PovInfoSourceTmdb': str(tmdb_id)})
			items.append((url, listitem, False))
	except Exception as exc: kodi_utils.logger('build_media_cast', str(exc))
	kodi_utils.add_items(handle, items)
	kodi_utils.set_content(handle, 'actors')
	kodi_utils.end_directory(handle, cacheToDisc=False)
	_signal_info_shelves(media_type, tmdb_id)

def _credit_listitem(item, resolution, actor_id):
	media_type = 'tvshow' if item.get('media_type') == 'tv' else 'movie'
	name_key = 'name' if media_type == 'tvshow' else 'title'
	date = item.get('first_air_date') if media_type == 'tvshow' else item.get('release_date')
	title = item.get(name_key) or item.get('original_%s' % name_key) or ''
	year = date.split('-', 1)[0] if date else ''
	tmdb_id = item['id']
	backdrop = tmdb_image_base % (resolution['fanart'], item['backdrop_path'])
	landscape = tmdb_image_base % ('w780', item['backdrop_path'])
	poster_path = item.get('poster_path')
	poster = tmdb_image_base % (resolution['poster'], poster_path) if poster_path else poster_empty
	rating = float(item.get('vote_average') or 0)
	listitem = make_listitem()
	listitem.setLabel(title)
	listitem.setArt({'thumb': landscape, 'landscape': landscape, 'fanart': backdrop, 'poster': poster, 'icon': poster})
	listitem.setProperties({
		'PovCreditType': media_type, 'PovActorSourceId': str(actor_id), 'landscape': landscape,
		'rating': '%.1f' % rating if rating else '', 'year_range': year
	})
	if KODI_VERSION < 20:
		listitem.setUniqueIDs({'tmdb': str(tmdb_id)})
		listitem.setInfo('video', {'title': title, 'year': year, 'premiered': date or '', 'rating': item.get('vote_average') or 0, 'plot': item.get('overview') or '', 'mediatype': media_type})
	else:
		video_info = listitem.getVideoInfoTag(offscreen=True)
		video_info.setTitle(title)
		video_info.setUniqueIDs({'tmdb': str(tmdb_id)})
		video_info.setMediaType(media_type)
		video_info.setPlot(item.get('overview') or '')
		if date: video_info.setPremiered(date)
		if year: video_info.setYear(int(year))
		video_info.setRating(rating)
		if media_type == 'tvshow': video_info.setTvShowTitle(title)
	url = build_url({'mode': 'show_media_info', 'mediatype': media_type, 'tmdb_id': tmdb_id})
	listitem.setPath(url)
	return url, listitem, False

def build_person_credits(params):
	handle = int(kodi_utils.argv1())
	actor_id, credit_type = params.get('actor_id'), params.get('credit_type')
	try:
		if not actor_id or credit_type not in actor_credit_types: raise ValueError('Invalid actor credit request')
		credits = _load_actor_credits(actor_id, credit_type)
		resolution = settings.get_resolution()
		items = [_credit_listitem(item, resolution, actor_id) for item in credits]
	except Exception as exc:
		kodi_utils.logger('build_person_credits', str(exc))
		items = []
	kodi_utils.add_items(handle, items)
	kodi_utils.set_content(handle, 'videos')
	kodi_utils.end_directory(handle, cacheToDisc=False)
	_focus_loaded_actor_shelf(actor_id, credit_type, bool(items))

def person_search(query):
	def _builder():
		for item in actors:
			try:
				name, gender = '%s' % item['name'], gender_dict[item['gender']]
				if item['name'] != item['original_name']: name += ' - %s' % item['original_name']
				if gender: name += ' (%s)' % gender
				known_for_list = item['known_for']
				plot = '[CR]'.join(i['title'] for i in known_for_list if 'title' in i and i['title'])
				fanart = (tmdb_image_base % (image_resolution['fanart'], i['backdrop_path']) for i in known_for_list if i['backdrop_path'])
				fanart = next(fanart, fanart_empty)
				poster = (tmdb_image_base % (image_resolution['poster'], i['poster_path']) for i in known_for_list if i['poster_path'])
				poster = next(poster, poster_empty)
				icon = tmdb_image_base % (image_resolution['poster'], item['profile_path']) if item['profile_path'] else poster
				url_params = build_url({'mode': 'show_person_info', 'actor_id': item['id'], 'actor_name': item['name'], 'actor_image': icon})
				listitem = make_listitem()
				listitem.setLabel(name)
				listitem.setArt({'icon': icon, 'poster': icon, 'thumb': icon, 'fanart': fanart, 'banner': icon})
				listitem.setInfo('video', {'plot': plot}) if KODI_VERSION < 20 else listitem.getVideoInfoTag().setPlot(plot)
				yield (url_params, listitem, False)
			except: pass
	__handle__ = int(sys.argv[1])
	image_resolution = settings.get_resolution()
	try: actors = tmdb_people_info(query)
	except: actors = []
	kodi_utils.add_items(__handle__, list(_builder()))
	kodi_utils.set_category(__handle__, query)
	kodi_utils.set_content(__handle__, 'artists')
	kodi_utils.end_directory(__handle__)
