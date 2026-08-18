import json
from time import monotonic, monotonic_ns
from modules import kodi_utils, settings
from modules.cache import clear_cache
from modules.utils import get_datetime, safe_string, valid_tmdb_id
# logger = kodi_utils.logger

ls, build_url, media_path, select_dialog = kodi_utils.local_string, kodi_utils.build_url, kodi_utils.media_path, kodi_utils.select_dialog
show_busy_dialog, hide_busy_dialog, notification, ok_dialog = kodi_utils.show_busy_dialog, kodi_utils.hide_busy_dialog, kodi_utils.notification, kodi_utils.ok_dialog
get_property, set_property, clear_property, container_refresh = kodi_utils.get_property, kodi_utils.set_property, kodi_utils.clear_property, kodi_utils.container_refresh
execute_builtin, confirm_dialog, container_content, sleep = kodi_utils.execute_builtin, kodi_utils.confirm_dialog, kodi_utils.container_content, kodi_utils.sleep

POV_INFO_WINDOW_ID = 1123
POV_INFO_PROPERTIES = (
	'PovInfoType', 'PovInfoTmdb', 'PovInfoTitle', 'PovInfoLogo', 'PovInfoFanart', 'PovInfoPoster', 'PovInfoPlot', 'PovInfoYear',
	'PovInfoYearRange', 'PovInfoMpaa', 'PovInfoRuntime', 'PovInfoSeasons', 'PovInfoMatch', 'PovInfoTrailer', 'PovInfoStatus', 'PovInfoHasCast',
	'PovInfoCollectionId', 'PovInfoCastSnapshot'
)
POV_ACTOR_PROPERTIES = (
	'PovActorId', 'PovActorName', 'PovActorProfile', 'PovActorBiography', 'PovActorLifespan', 'PovActorBirthplace', 'PovActorBackdrop',
	'PovActorHasMovies', 'PovActorHasTVShows', 'PovActorHasDirected', 'PovActorReady'
)
POV_PAGE_HISTORY_PROPERTY = 'PovPageHistory'
POV_INFO_HYDRATION_PROPERTY = 'PovInfoHydrationRequest'
POV_INFO_PENDING_TMDB_PROPERTY = 'PovInfoPendingTmdb'
POV_ACTOR_HYDRATION_PROPERTY = 'PovActorHydrationRequest'
TRAILER_PREVIEW_PROPERTY = 'BingieTrailerPreview'
TRAILER_PREVIEW_CANCEL_PROPERTY = 'BingieTrailerPreviewCancel'
TRAILER_PREVIEW_REQUEST_PROPERTY = 'BingieTrailerPreviewRequest'
POV_INFO_FOCUS_CONTROLS = (80, 51, 53, 550, 563, 560)
POV_ACTOR_FOCUS_CONTROLS = (610, 620, 630, 699)
POV_CONTAINER_CONTROLS = (550, 563, 560, 610, 620, 630)

def _subtitle_rpc(method, params=None):
	request = {'jsonrpc': '2.0', 'id': 1, 'method': method}
	if params: request['params'] = params
	return json.loads(kodi_utils.execJSONRPC(json.dumps(request))).get('result')

def _subtitle_stream_label(stream, count):
	if not stream: return kodi_utils.xbmc.getLocalizedString(231)
	label = stream.get('name') or stream.get('language') or kodi_utils.xbmc.getLocalizedString(13205)
	index = stream.get('index')
	if isinstance(index, int) and count: label += ' (%s/%s)' % (index + 1, count)
	return label

def subtitle_settings_menu():
	players = _subtitle_rpc('Player.GetActivePlayers') or []
	player = next((item for item in players if item.get('type') == 'video'), None)
	if not player: return
	player_id = player['playerid']
	state = _subtitle_rpc('Player.GetProperties', {'playerid': player_id, 'properties': ['subtitleenabled', 'subtitles', 'currentsubtitle']}) or {}
	streams = state.get('subtitles') or []
	current = state.get('currentsubtitle') or {}
	localized = kodi_utils.xbmc.getLocalizedString
	offset = kodi_utils.get_infolabel('Player.SubtitleDelay') or '0.000s'
	options = [
		'%s: %s' % (localized(13397), localized(16041) if state.get('subtitleenabled') else localized(351)),
		'%s: %s' % (localized(22006), offset),
		'%s: %s' % (localized(462), _subtitle_stream_label(current, len(streams))),
		localized(24134),
	]
	choice = kodi_utils.dialog.select(localized(24133), options)
	if choice == 0:
		_subtitle_rpc('Player.SetSubtitle', {'playerid': player_id, 'subtitle': 'off' if state.get('subtitleenabled') else 'on'})
	elif choice == 1: execute_builtin('Action(SubtitleDelay)')
	elif choice == 2 and streams:
		labels = [_subtitle_stream_label(stream, len(streams)) for stream in streams]
		selected = kodi_utils.dialog.select(localized(462), labels, preselect=current.get('index', -1))
		if selected not in (-1, None): _subtitle_rpc('Player.SetSubtitle', {'playerid': player_id, 'subtitle': streams[selected]['index'], 'enable': True})
	elif choice == 3: execute_builtin('ActivateWindow(subtitlesearch)')

def _reset_info_page_focus(media_type):
	execute_builtin('SetFocus(%s)' % (80 if media_type == 'movie' else 51))

def _page_history():
	try:
		history = json.loads(get_property(POV_PAGE_HISTORY_PROPERTY) or '[]')
		return history if isinstance(history, list) else []
	except: return []

def reset_pov_page_history():
	clear_property(POV_PAGE_HISTORY_PROPERTY)

def _push_page_history(state):
	history = _page_history()
	history.append(state)
	set_property(POV_PAGE_HISTORY_PROPERTY, json.dumps(history[-20:], separators=(',', ':')))

def _page_focus_state(page_type):
	controls = POV_INFO_FOCUS_CONTROLS if page_type == 'info' else POV_ACTOR_FOCUS_CONTROLS if page_type == 'actor' else ()
	try:
		window = kodi_utils.current_window_id()
		control = window.getFocusId()
		if control not in controls: return {}
		state = {'control': control}
		if control in POV_CONTAINER_CONTROLS: state['position'] = max(0, int(window.getControl(control).getSelectedPosition()))
		return state
	except: return {}

def _restore_page_focus(focus, controls, window_id, fallback=None):
	try: control = int(focus.get('control'))
	except: control = fallback
	if control not in controls: control = fallback
	if control is None: return
	if control in POV_CONTAINER_CONTROLS:
		try: position = max(0, int(focus.get('position', 0)))
		except: position = 0
		condition = 'Window.IsActive(%s) + Integer.IsGreater(Container(%s).NumItems,%s)' % (window_id, control, position)
		deadline = monotonic() + 2.0
		while not kodi_utils.get_visibility(condition) and monotonic() < deadline: sleep(25)
		if kodi_utils.get_visibility(condition): execute_builtin('Control.SetFocus(%s,%s,absolute)' % (control, position))
		return
	execute_builtin('SetFocus(%s)' % control)

def push_pov_page_state(page_type):
	properties = POV_INFO_PROPERTIES if page_type == 'info' else POV_ACTOR_PROPERTIES if page_type == 'actor' else ()
	if not properties: return
	values = {prop: get_property(prop) for prop in properties}
	if not any(values.values()): return
	_push_page_history({'page': page_type, 'values': values, 'focus': _page_focus_state(page_type)})

def push_native_info_state():
	_push_page_history({'page': 'native_info'})

def pov_page_back(params=None):
	_stop_owned_trailer_preview()
	clear_property(POV_INFO_HYDRATION_PROPERTY)
	clear_property(POV_INFO_PENDING_TMDB_PROPERTY)
	clear_property(POV_ACTOR_HYDRATION_PROPERTY)
	history = _page_history()
	if history:
		state = history.pop()
		page_type = state.get('page')
		focus = state.get('focus') or {}
		properties = POV_INFO_PROPERTIES if page_type == 'info' else POV_ACTOR_PROPERTIES if page_type == 'actor' else ()
		values = state.get('values') or {}
		if page_type == 'info': clear_property('PovInfoTmdb')
		for prop in properties:
			if page_type == 'info' and prop == 'PovInfoTmdb': continue
			value = values.get(prop) or ''
			set_property(prop, str(value)) if value else clear_property(prop)
		if page_type == 'info':
			tmdb_id = values.get('PovInfoTmdb') or ''
			set_property('PovInfoTmdb', str(tmdb_id)) if tmdb_id else clear_property('PovInfoTmdb')
		if history: set_property(POV_PAGE_HISTORY_PROPERTY, json.dumps(history, separators=(',', ':')))
		else: clear_property(POV_PAGE_HISTORY_PROPERTY)
		if page_type == 'info':
			execute_builtin('ReplaceWindow(%s)' % POV_INFO_WINDOW_ID)
			_restore_page_focus(focus, POV_INFO_FOCUS_CONTROLS, POV_INFO_WINDOW_ID, 80 if get_property('PovInfoType') == 'movie' else 51)
			return
		if page_type == 'actor':
			execute_builtin('ReplaceWindow(1122)')
			_restore_page_focus(focus, POV_ACTOR_FOCUS_CONTROLS, 1122)
			return
		if page_type == 'native_info':
			execute_builtin('PreviousMenu')
			execute_builtin('AlarmClock(PovNativeInfoBack,Action(Info),00:00,silent)')
			return
	execute_builtin('PreviousMenu')

def get_media_metadata(media_type, tmdb_id):
	if media_type not in ('movie', 'tvshow') or not valid_tmdb_id(tmdb_id): return None
	from indexers import metadata
	user_info = settings.metadata_user_info().copy()
	user_info['language'] = 'en'
	current_date = get_datetime()
	if media_type == 'movie': return metadata.movie_meta('tmdb_id', tmdb_id, user_info, current_date)
	return metadata.tvshow_meta('tmdb_id', tmdb_id, user_info, current_date)

def _runtime_label(duration):
	try: minutes = max(0, int(duration or 0) // 60)
	except: return ''
	if not minutes: return ''
	hours, minutes = divmod(minutes, 60)
	if hours and minutes: return '%dh %dm' % (hours, minutes)
	if hours: return '%dh' % hours
	return '%dm' % minutes

def _show_year_range(meta):
	year = str(meta.get('year') or '')
	if not year: return ''
	status = str(meta.get('status') or '')
	if status and status.lower() not in ('ended', 'canceled', 'cancelled'): return '%s–Present' % year
	return str(meta.get('year_range') or year)

def _seasons_label(total):
	try: total = int(total or 0)
	except: return ''
	if not total: return ''
	return '%d Season%s' % (total, '' if total == 1 else 's')

def get_focused_media_info(media_type, tmdb_id):
	meta = get_media_metadata(media_type, tmdb_id)
	if meta is None: return None
	if not meta or meta.get('blank_entry'): return {}
	from indexers.metadata import main_actors, resized_tmdb_image
	return {
		'main_actors': main_actors(meta.get('cast') or ()), 'genre': meta.get('genre') or '',
		'clearlogo': resized_tmdb_image(meta.get('clearlogo'), 'w300') or '', 'mpaa': meta.get('mpaa') or '',
		'duration': _runtime_label(meta.get('duration')), 'seasons': _seasons_label(meta.get('total_seasons')) if media_type == 'tvshow' else '',
		'year_range': _show_year_range(meta) if media_type == 'tvshow' else str(meta.get('year') or ''), 'trailer': meta.get('trailer') or ''
	}

def _match_label(rating):
	try: rating = float(rating or 0)
	except: return ''
	return '%d%% Match' % round(rating * 10) if rating > 0 else ''

def _stop_owned_trailer_preview(suppress_identity=''):
	clear_property(TRAILER_PREVIEW_REQUEST_PROPERTY)
	set_property(TRAILER_PREVIEW_CANCEL_PROPERTY, suppress_identity or 'true')
	if get_property(TRAILER_PREVIEW_PROPERTY) != 'true': return
	execute_builtin('PlayerControl(Stop)')
	clear_property(TRAILER_PREVIEW_PROPERTY)

def play_from_info(params):
	media_type, tmdb_id = params.get('mediatype'), params.get('tmdb_id')
	if not tmdb_id: tmdb_id = get_property(POV_INFO_PENDING_TMDB_PROPERTY)
	if tmdb_id: params['tmdb_id'] = tmdb_id
	_stop_owned_trailer_preview('|'.join(('info', media_type, tmdb_id)) if media_type and tmdb_id else '')
	if media_type == 'movie':
		from modules.sources import Sources
		return Sources.factory(params)
	if media_type == 'tvshow':
		from modules.episode_tools import SmartPlay
		return SmartPlay(params)

def _media_cast_snapshot(media_type, tmdb_id, cast):
	items = []
	for actor in cast or ():
		if not isinstance(actor, dict): continue
		name = str(actor.get('name') or '').strip()
		if not name: continue
		item = {'name': name, 'role': str(actor.get('role') or ''), 'thumbnail': str(actor.get('thumbnail') or '')}
		if actor.get('actor_id'): item['actor_id'] = str(actor['actor_id'])
		items.append(item)
		if len(items) == 14: break
	return items, json.dumps({'mediatype': media_type, 'tmdb_id': str(tmdb_id), 'cast': items}, separators=(',', ':'))

def _set_media_info_properties(media_type, tmdb_id, meta):
	title = meta.get('english_title') or meta.get('title') or ''
	extra_info = meta.get('extra_info') or {}
	status = meta.get('status') or extra_info.get('status') or ''
	cast, cast_snapshot = _media_cast_snapshot(media_type, tmdb_id, meta.get('cast'))
	values = {
		'PovInfoType': media_type, 'PovInfoTmdb': str(tmdb_id), 'PovInfoTitle': title, 'PovInfoLogo': meta.get('clearlogo') or '',
		'PovInfoFanart': meta.get('fanart') or '', 'PovInfoPoster': meta.get('poster') or '', 'PovInfoPlot': meta.get('plot') or '',
		'PovInfoYear': str(meta.get('year') or ''), 'PovInfoYearRange': _show_year_range(meta) if media_type == 'tvshow' else str(meta.get('year') or ''),
		'PovInfoMpaa': meta.get('mpaa') or '', 'PovInfoRuntime': _runtime_label(meta.get('duration')),
		'PovInfoSeasons': _seasons_label(meta.get('total_seasons')) if media_type == 'tvshow' else '', 'PovInfoMatch': _match_label(meta.get('rating')),
		'PovInfoTrailer': meta.get('trailer') or '', 'PovInfoStatus': status, 'PovInfoHasCast': str(bool(cast)).lower(),
		'PovInfoCollectionId': extra_info.get('collection_id') if media_type == 'movie' else '', 'PovInfoCastSnapshot': cast_snapshot
	}
	clear_property('PovInfoTmdb')
	for prop in POV_INFO_PROPERTIES:
		if prop != 'PovInfoTmdb': set_property(prop, str(values.get(prop) or ''))
	set_property('PovInfoTmdb', str(tmdb_id))

def _selected_media_snapshot(media_type, tmdb_id):
	get_label = kodi_utils.get_infolabel
	requested_id = str(tmdb_id)
	def label(name): return get_label('Container.ListItem.%s' % name).strip() or get_label('ListItem.%s' % name).strip()
	selected_id = label('UniqueID(tmdb)') or label('Property(tmdb_id)')
	if selected_id != requested_id: return {}
	year = label('Year') or label('Property(year)')
	rating = label('Rating') or label('Property(rating)')
	return {
		'PovInfoType': media_type, 'PovInfoTitle': label('Title') or label('Label'), 'PovInfoLogo': label('Art(clearlogo)'),
		'PovInfoFanart': label('Art(fanart)') or label('Property(landscape)'), 'PovInfoPoster': label('Art(poster)') or label('Icon'), 'PovInfoPlot': label('Plot'), 'PovInfoYear': year,
		'PovInfoYearRange': label('Property(year_range)') if media_type == 'tvshow' else year, 'PovInfoMpaa': label('MPAA'),
		'PovInfoRuntime': label('Duration'), 'PovInfoSeasons': _seasons_label(label('Property(totalseasons)')) if media_type == 'tvshow' else '',
		'PovInfoMatch': _match_label(rating), 'PovInfoTrailer': label('Trailer'), 'PovInfoStatus': '', 'PovInfoHasCast': 'true',
		'PovInfoCollectionId': label('Property(PovInfoCollectionId)') if media_type == 'movie' else '', 'PovInfoCastSnapshot': ''
	}

def _set_pending_media_info_properties(media_type, tmdb_id):
	values = _selected_media_snapshot(media_type, tmdb_id)
	clear_property('PovInfoTmdb')
	for prop in POV_INFO_PROPERTIES:
		if prop != 'PovInfoTmdb': set_property(prop, str(values.get(prop) or ''))
	# Keep provider shelves gated until the complete metadata snapshot is installed.
	set_property('PovInfoType', media_type)
	set_property('PovInfoHasCast', 'true')
	clear_property('PovInfoMoreLikeThisReady')
	clear_property('PovInfoCollectionReady')

def show_media_info(params):
	media_type, tmdb_id = params.get('mediatype'), params.get('tmdb_id')
	if media_type not in ('movie', 'tvshow') or not valid_tmdb_id(tmdb_id): return
	if get_property('PovInfoTransition'): return
	transition_token = str(monotonic_ns())
	set_property('PovInfoTransition', transition_token)
	try:
		active_info = kodi_utils.get_visibility('Window.IsActive(%s)' % POV_INFO_WINDOW_ID)
		active_actor = kodi_utils.get_visibility('Window.IsActive(1122)')
		active_native_info = kodi_utils.get_visibility('Window.IsActive(DialogVideoInfo.xml)')
		if active_actor:
			clear_property(POV_ACTOR_HYDRATION_PROPERTY)
			push_pov_page_state('actor')
		elif active_info: push_pov_page_state('info')
		else:
			reset_pov_page_history()
			if active_native_info: push_native_info_state()
		_stop_owned_trailer_preview()
		if active_native_info:
			execute_builtin('Dialog.Close(movieinformation)')
			close_deadline = monotonic() + 2.0
			while kodi_utils.get_visibility('Window.IsActive(DialogVideoInfo.xml)') and monotonic() < close_deadline: sleep(50)
			if kodi_utils.get_visibility('Window.IsActive(DialogVideoInfo.xml)'): return
		_set_pending_media_info_properties(media_type, tmdb_id)
		hydration_token = '|'.join((transition_token, media_type, str(tmdb_id)))
		set_property(POV_INFO_HYDRATION_PROPERTY, hydration_token)
		set_property(POV_INFO_PENDING_TMDB_PROPERTY, str(tmdb_id))
		if get_property('PovInfoTransition') == transition_token: clear_property('PovInfoTransition')
		if active_info:
			execute_builtin('ReplaceWindow(%s)' % POV_INFO_WINDOW_ID)
			_reset_info_page_focus(media_type)
		else: execute_builtin('ActivateWindow(%s)' % POV_INFO_WINDOW_ID)
		hydrate_url = build_url({'mode': 'hydrate_media_info', 'mediatype': media_type, 'tmdb_id': tmdb_id, 'request': hydration_token})
		execute_builtin('RunPlugin(%s)' % hydrate_url)
	finally:
		if get_property('PovInfoTransition') == transition_token: clear_property('PovInfoTransition')

def hydrate_media_info(params):
	media_type, tmdb_id, hydration_token = params.get('mediatype'), params.get('tmdb_id'), params.get('request')
	if media_type not in ('movie', 'tvshow') or not valid_tmdb_id(tmdb_id) or get_property(POV_INFO_HYDRATION_PROPERTY) != hydration_token: return
	try:
		meta = get_media_metadata(media_type, tmdb_id)
		if get_property(POV_INFO_HYDRATION_PROPERTY) != hydration_token: return
		if get_property('PovInfoTmdb'): return
		if meta and not meta.get('blank_entry'): _set_media_info_properties(media_type, tmdb_id, meta)
		else:
			set_property('PovInfoHasCast', 'false')
			set_property('PovInfoTmdb', str(tmdb_id))
		set_property('PovInfoMoreLikeThisReady', '1')
		if media_type == 'movie' and get_property('PovInfoCollectionId'): set_property('PovInfoCollectionReady', '1')
	except Exception as exc:
		if get_property(POV_INFO_HYDRATION_PROPERTY) == hydration_token and not get_property('PovInfoTmdb'):
			set_property('PovInfoHasCast', 'false')
			set_property('PovInfoTmdb', str(tmdb_id))
			set_property('PovInfoMoreLikeThisReady', '1')
			if media_type == 'movie' and get_property('PovInfoCollectionId'): set_property('PovInfoCollectionReady', '1')
		kodi_utils.logger('hydrate_media_info', str(exc))
	finally:
		if get_property(POV_INFO_HYDRATION_PROPERTY) == hydration_token:
			clear_property(POV_INFO_HYDRATION_PROPERTY)
			clear_property(POV_INFO_PENDING_TMDB_PROPERTY)

def imdb_videos_choice(videos, poster):
	try: videos = json.loads(videos)
	except: pass
	videos.sort(key=lambda x: x['quality_rank'])
	list_items = [{'line1': i['quality'], 'icon': poster} for i in videos]
	kwargs = {'items': json.dumps(list_items), 'heading': ls(32241)}
	return select_dialog([i['url'] for i in videos], **kwargs)

def trailer_choice(mediatype, poster, tmdb_id, trailer_url, all_trailers=None):
	if settings.get_language() != 'en' and not trailer_url and not all_trailers:
		from indexers.tmdb_api import tmdb_media_videos
		try: all_trailers = tmdb_media_videos(mediatype, tmdb_id)['results']
		except: pass
	if not all_trailers: return trailer_url
	if len(all_trailers) > 1:
		all_trailers.sort(key=lambda k: k.get('published_at'))
		list_items = [
			{'line1': safe_string(i['name']),
			 'line2': '%s (%s)' % (i['type'], i.get('site') or 'NA'),
			 'icon': poster}
			for i in all_trailers
		]
		kwargs = {'items': json.dumps(list_items), 'heading': ls(32606)}
		video_id = select_dialog([i['key'] for i in all_trailers], **kwargs)
	else: video_id = next(iter(all_trailers), {}).get('key')
	if video_id is None: trailer_url = 'canceled'
	else:
		from modules.trailers import plugin_url
		trailer_url = plugin_url(video_id)
	return trailer_url

def genres_choice(mediatype, genres, poster, return_genres=False):
	from modules.meta_lists import movie_genres, tvshow_genres
	if mediatype in ('movie', 'movies'):
		genre_action, meta_type, action = movie_genres, 'movie', 'tmdb_movies_genres'
	else: genre_action, meta_type, action = tvshow_genres, 'tvshow', 'tmdb_tv_genres'
	genre_list = [{'genre': k, 'value': v} for k, v in genre_action.items() if k in genres]
	if return_genres: return genre_list
	if len(genre_list) == 0: return notification(32760, 1500)
	mode = 'build_%s_list' % meta_type
	choices = [{'mode': mode, 'action': action, 'genre_id': i['value'][0]} for i in genre_list]
	list_items = [{'line1': i['genre'], 'icon': poster} for i in genre_list]
	kwargs = {'items': json.dumps(list_items), 'heading': ls(32470)}
	return select_dialog(choices, **kwargs)

def browse_choice(meta, is_widget=False):
	tmdb_id = meta.get('tmdb_id')
	if not tmdb_id: return
	container_update = ('Container.Update(%s)', 'ActivateWindow(Videos,%s,return)')[is_widget]
	url_params = {'mode': 'build_season_list', 'tmdb_id': tmdb_id}
	execute_builtin(container_update % build_url(url_params))

def random_choice(choice, meta):
	tmdb_id = meta.get('tmdb_id')
	if not tmdb_id: return
	from modules.episode_tools import get_random_episode
	from modules.sources import Sources
	continual = True if choice == 'play_random_continual' else False
	meta, play_params = get_random_episode(tmdb_id, continual)
	if not play_params: return notification(32760)
	Sources.factory(play_params)

def playback_choice(content, poster, meta):
	items = [
		('clear_and_rescrape', ls(32014)), ('scrape_with_filters_ignored', ls(32807)), ('scrape_with_custom_values', ls(32135))
	]
	list_items = [{'line1': i[1], 'icon': poster} for i in items]
	kwargs = {'items': json.dumps(list_items), 'heading': ls(32174)}
	choice = select_dialog([i[0] for i in items], **kwargs)
	if choice is None: return
	if choice == 'clear_and_rescrape': clear_and_rescrape(content, meta)
	elif choice == 'scrape_with_filters_ignored': scrape_with_filters_ignored(content, meta)
	else: scrape_with_custom_values(content, meta)

def dropped_choice(params):
	from caches.dropped_cache import Dropped
	dropped = Dropped()
	mediatype, tmdb_id, title = params['mediatype'], params['tmdb_id'], params['title']
	current_dropped = dropped.get(mediatype)
	if tmdb_id in {int(i['tmdb_id']) for i in current_dropped}:
		action, text = dropped.remove, '%s BINGIE Lite %s?' % (ls(32603), 'Dropped')
	else: action, text = dropped.add, '%s BINGIE Lite %s?' % (ls(32602), 'Dropped')
	if not confirm_dialog(text='%s[CR][CR]%s' % (title, text)): return
	notification(32576) if action(mediatype, tmdb_id, title) else notification(32574)
	container_refresh()

def options_menu(params, meta=None):
	is_widget = params.get('is_widget', False) in ('true', 'True', True)
	content = params.get('content') or params.get('mediatype') or container_content()[:-1]
	season, episode = params.get('season'), params.get('episode')
	if not meta:
		from indexers import metadata
		func = metadata.movie_meta if content == 'movie' else metadata.tvshow_meta
		meta = func('tmdb_id', params['tmdb_id'], settings.metadata_user_info(), get_datetime())
	scrapable = content in ('movie', 'episode')
	poster = meta.get('poster', '')
	title = meta.get('title', '')
	scraper_options_str = '%s %s' % (ls(32533), ls(32841))
	browse_str = ls(32652).replace('[B]', '').replace('[/B]', '')
	smart_play = settings.smart_play_enabled()
	listing = []
	append = listing.append
	if content == 'episode':
		append(('scrape_from_episode_group', 'Scrape From Episode Group', scraper_options_str, poster))
	if scrapable:
		append(('clear_and_rescrape', ls(32014), scraper_options_str, poster))
		append(('scrape_with_filters_ignored', ls(32807), scraper_options_str, poster))
		append(('scrape_with_custom_values', ls(32135), scraper_options_str, poster))
	if content == 'tvshow' and meta:
		if smart_play == 2 or (smart_play == 1 and is_widget):
			append(('browse_choice', browse_str, title, poster))
		append(('play_random', ls(32541), title, poster))
		append(('play_random_continual', ls(32542), title, poster))
	append(('clear_scrapers_cache', ls(32637), ''))
	if content == 'tvshow':
		append(('dropped_choice', 'Toggle Dropped', title, poster))
	if content in ('movie', 'tvshow') and meta:
		append(('clear_media_cache', ls(32604) % (ls(32028) if content == 'movie' else ls(32029)), title, poster))
	if is_widget: listing.append(('reload_widgets', 'BINGIE Lite: Refresh Widgets', ''))
	list_items = [
		{'line1': item[1], 'line2': item[2] or item[1], **({'icon': item[3]} if len(item) == 4 else {})}
		for item in listing
	]
	heading = ls(32646).replace('[B]', '').replace('[/B]', '')
	choice = select_dialog([i[0] for i in listing], items=json.dumps(list_items), heading=heading)
	if choice in (None, 'save_and_exit'): return
	if choice == 'clear_and_rescrape': return clear_and_rescrape(content, meta, season, episode)
	if choice == 'scrape_with_filters_ignored': return scrape_with_filters_ignored(content, meta, season, episode)
	if choice == 'scrape_with_custom_values': return scrape_with_custom_values(content, meta, season, episode)
	if choice == 'scrape_from_episode_group': return scrape_from_episode_group(meta, season, episode)
	if choice == 'browse_choice': return browse_choice(meta, is_widget)
	if choice in ('play_random', 'play_random_continual'): return random_choice(choice, meta)
	if choice == 'clear_scrapers_cache': return clear_scrapers_cache()
	if choice == 'dropped_choice': return dropped_choice(meta)
	if choice == 'clear_media_cache': return refresh_cached_meta(meta)
	if choice == 'reload_widgets': return kodi_utils.widget_refresh()
	options_menu(params, meta=meta)

def extras_menu(params):
	from indexers import metadata
	from windows import open_window
	function = metadata.movie_meta if params['mediatype'] == 'movie' else metadata.tvshow_meta
	meta = function('tmdb_id', params['tmdb_id'], settings.metadata_user_info(), get_datetime())
	kwargs = {'meta': meta, 'is_widget': params.get('is_widget', 'false'), 'is_home': params.get('is_home', 'false')}
	open_window(('windows.extras', 'Extras'), 'extras.xml', **kwargs)

def refresh_cached_meta(meta):
	from caches.meta_cache import MetaCache
	try:
		metacache = MetaCache()
		mediatype, tmdb_id = meta['mediatype'], meta['tmdb_id']
		if mediatype == 'tvshow': metacache.delete_all_seasons_memory_cache(tmdb_id, meta.get('total_seasons'))
		metacache.delete(mediatype, 'tmdb_id', tmdb_id, meta)
		notification(32576, 1500)
		container_refresh()
	except: notification(32574)

def build_navigate_to_page(params):
	use_alphabet = settings.nav_jump_use_alphabet() == 2
	icon = media_path('item_jump.png')
	mediatype = params.get('mediatype', '')
	if use_alphabet:
		start_list = [chr(i) for i in range(97, 123)]
	else:
		total_pages = int(params.get('total_pages', 0))
		start_list = [str(i) for i in range(1, total_pages + 1)]
		current_page = params.get('current_page')
		if current_page in start_list: start_list.remove(current_page)
	list_items = []
	for item in start_list:
		if use_alphabet: line1, line2 = item.upper(), ls(32821) % (mediatype, item.upper())
		else: line1, line2 = '%s %s' % ('Page', item), ls(32822) % item
		list_items.append({'line1': line1, 'line2': line2, 'icon': icon})
	kwargs = {'items': json.dumps(list_items), 'heading': 'BINGIE Lite'}
	new_start = select_dialog(start_list, **kwargs)
	sleep(100)
	if new_start is None: return
	passthrough_keys = ['mediatype', 'query', 'actor_id', 'user', 'slug', 'list_id', 'name']
	url_params = {key: params.get(key, '') for key in passthrough_keys}
	url_params.update({
		'mode': params.get('transfer_mode', ''),
		'action': params.get('transfer_action', ''),
		'new_page': '' if use_alphabet else new_start,
		'new_letter': new_start if use_alphabet else ''
	})
	execute_builtin('Container.Update(%s)' % build_url(url_params))

def _get_base_play_params(mediatype, meta, season=None, episode=None):
	play_params = {'mode': 'play_media', 'tmdb_id': meta['tmdb_id'], 'autoplay': 'false'}
	if mediatype in ('movie', 'movies'): play_params.update({'mediatype': 'movie'})
	else: play_params.update({'mediatype': 'episode', 'season': season, 'episode': episode})
	return play_params

def scrape_from_episode_group(meta, season, episode):
	from indexers.tmdb_api import episode_groups, episode_group_details
	from modules.sources import Sources
	tmdb_id, heading, poster = meta['tmdb_id'], meta['tvshowtitle'], meta['poster']
	groups = episode_groups(tmdb_id)
	choices = [
		(item['id'],
		 '%s (%s)' % (item['name'], item['type']),
		 '%s Groups, %s Episodes' % (item['group_count'], item['episode_count']))
		for item in groups
	]
	if not choices: return notification(32760)
	list_items = [{'line1': item[1], 'line2': item[2], 'icon': poster} for item in choices]
	kwargs = {'items': json.dumps(list_items), 'heading': heading, 'enumerate': 'true'}
	choice = select_dialog([i[0] for i in choices], multi_line='false', **kwargs)
	if choice is None: return
	episodes = episode_group_details(choice)
	if not episodes: return notification(32760)
	episodes = [
		{**episode, 'custom_episode': episode['order'] + 1, 'custom_season': group['order'],
		'custom_name': f"S{group['order']}xE{episode['order'] + 1:02d} - {episode['name']}",
		'custom_title': f"S{episode['season_number']}xE{episode['episode_number']:02d} - {episode['name']}"}
		for group in episodes for episode in group['episodes']
	]
	index = next((
		episodes.index(i) for i in episodes
		if i['season_number'] == int(season) and i['episode_number'] == int(episode)
	), None)
	if index is not None:
		heading = episodes[index]['name']
		episodes, preselect = episodes[index:] + episodes[:index], 0
	else: heading, preselect = meta['title'], -1
	choices = [(item['custom_season'], item['custom_episode'], item['custom_name'], item['custom_title']) for item in episodes]
	if not choices: return
	list_items = [{'line1': item[2], 'line2': item[3], 'icon': poster} for item in choices]
	kwargs = {'items': json.dumps(list_items), 'heading': heading, 'preselect': preselect}
	choice = select_dialog([(i[0], i[1]) for i in choices], multi_line='false', **kwargs)
	if choice is None: return
	play_params = {'mode': 'play_media', 'tmdb_id': tmdb_id, 'mediatype': 'episode', 'season': season, 'episode': episode}
	play_params.update({'custom_season': choice[0], 'custom_episode': choice[1]})
	Sources().source_select(play_params)

def clear_and_rescrape(mediatype, meta, season=None, episode=None):
	from caches.providers_cache import ExternalProvidersCache
	from modules.sources import Sources
	show_busy_dialog()
	deleted = ExternalProvidersCache().delete_cache_single(mediatype, str(meta['tmdb_id']))
	hide_busy_dialog()
	if not deleted: return notification(32574)
	play_params = _get_base_play_params(mediatype, meta, season, episode)
	Sources().source_select(play_params)

def scrape_with_filters_ignored(mediatype, meta, season=None, episode=None):
	from modules.sources import Sources
	play_params = _get_base_play_params(mediatype, meta, season, episode)
	play_params.update({'ignore_scrape_filters': 'true'})
	set_property('pov_lite_fs_filterless_search', 'true')
	Sources().source_select(play_params)

def scrape_with_custom_values(mediatype, meta, season=None, episode=None):
	from windows import open_window
	from modules.sources import Sources
	play_params = _get_base_play_params(mediatype, meta, season, episode)
	custom_title = kodi_utils.dialog.input(ls(32228), defaultt=meta['title'])
	if not custom_title: return
	play_params['custom_title'] = custom_title
	if mediatype in ('movie', 'movies'):
		custom_year = kodi_utils.dialog.numeric(0, '%s (%s)' % (ls(32543), ls(32669)), defaultt=str(meta['year']))
		if custom_year: play_params.update({'custom_year': custom_year})
	else:
		custom_season = kodi_utils.dialog.numeric(0, '%s (%s)' % (ls(32537).title(), ls(32669)), defaultt=str(season))
		custom_episode = kodi_utils.dialog.numeric(0, '%s (%s)' % (ls(32203).title(), ls(32669)), defaultt=str(episode))
		if custom_season and custom_episode: play_params.update({'custom_season': custom_season, 'custom_episode': custom_episode})
	kwargs = {'meta': meta, 'enable_buttons': True, 'true_button': ls(32824), 'false_button': ls(32828), 'focus_button': 11}
	choice = open_window(('windows.progress', 'ProgressMedia'), 'progress_media.xml', text=ls(32808), **kwargs)
	if choice is None: return
	if choice:
		play_params['ignore_scrape_filters'] = 'true'
		set_property('pov_lite_fs_filterless_search', 'true')
	Sources().source_select(play_params)

def clear_scrapers_cache(silent=False):
	for item in ('internal_scrapers', 'external_scrapers'): clear_cache(item, silent=True)
	if not silent: notification(32576)
