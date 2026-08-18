from indexers.metadata import tmdb_image_base
from modules import kodi_utils
from modules.utils import media_percentage_properties


def first_country_code(data):
	for key in ('country_codes', 'origin_country', 'production_countries'):
		values = data.get(key) or ()
		if isinstance(values, (str, dict)): values = (values,)
		for value in values:
			if isinstance(value, dict): value = value.get('iso_3166_1')
			code = str(value or '').strip().lower()
			if len(code) == 2 and code.isalpha(): return code
	return ''


def card_flag(data):
	country_code = first_country_code(data)
	if country_code: return 'flags/country/%s.png' % country_code
	return ''


def card_language(data):
	if first_country_code(data): return ''
	language_code = str(data.get('original_language') or '').strip().lower()
	if language_code != 'xx' and len(language_code) == 2 and language_code.isascii() and language_code.isalpha(): return language_code.upper()
	return ''


def _schedule_next_page_prefetch(url, origin_params):
	from modules.prefetch import schedule_next_page_prefetch
	return schedule_next_page_prefetch(url, origin_params)


def complete_media_directory(handle, mode, action, exit_list_params, category, content_type, view_type, is_widget, new_page, limited_tmdb, origin_params, nextpage_label, nextpage_icon):
	try:
		if new_page and not is_widget:
			if limited_tmdb:
				page_params = {'mode': mode, 'action': action, 'exit_list_params': exit_list_params, 'name': category}
			else:
				new_page.update({'mode': mode, 'action': action, 'exit_list_params': exit_list_params, 'name': category})
				page_params = new_page
			kodi_utils.add_dir(handle, page_params, nextpage_label, nextpage_icon)
	except: pass
	kodi_utils.set_category(handle, category)
	kodi_utils.set_sort_method(handle, content_type)
	kodi_utils.set_content(handle, content_type)
	kodi_utils.end_directory(handle, False if is_widget else None)
	if new_page and not is_widget and not limited_tmdb: _schedule_next_page_prefetch(kodi_utils.build_url({**new_page, 'prefetch': 'true'}), origin_params)
	kodi_utils.set_view_mode(view_type, content_type, is_widget)


def build_tmdb_detail_shelf_item(position, item, source_tmdb_id, mediatype, genre_names, poster_empty, fanart_empty, kodi_version):
	item_get = item.get
	is_tvshow = mediatype == 'tvshow'
	title = item_get('name') or item_get('original_name') if is_tvshow else item_get('title') or item_get('original_title')
	tmdb_id = item_get('id')
	if not tmdb_id or not title: return None
	premiered = item_get('first_air_date' if is_tvshow else 'release_date') or ''
	year = premiered[:4] if len(premiered) >= 4 else ''
	rating = item_get('vote_average') or 0
	genres = [genre_names[genre_id] for genre_id in item_get('genre_ids') or () if genre_id in genre_names]
	poster_path, backdrop_path = item_get('poster_path'), item_get('backdrop_path')
	poster = tmdb_image_base % ('w342', poster_path) if poster_path else poster_empty
	fanart = tmdb_image_base % ('w1280', backdrop_path) if backdrop_path else fanart_empty
	landscape = tmdb_image_base % ('w780', backdrop_path) if backdrop_path else fanart
	url_params = kodi_utils.build_url({'mode': 'show_media_info', 'mediatype': mediatype, 'tmdb_id': tmdb_id})
	props = {
		'PovLiteItem': 'true', 'PovLiteSummary': 'true', 'PovFocusIdentity': 'listing|%s|%s' % (mediatype, tmdb_id),
		'pov_lite_sort_order': str(position), 'tmdb_id': str(tmdb_id), 'PovInfoSourceTmdb': str(source_tmdb_id or '')
	}
	flag = card_flag(item)
	if flag: props['card_flag'] = flag
	elif not is_tvshow:
		language = card_language(item)
		if language: props['card_language'] = language
	props.update(media_percentage_properties(rating))
	art = {'poster': poster, 'icon': poster, 'fanart': fanart, 'thumb': landscape, 'landscape': landscape}
	if is_tvshow: art.update({'tvshow.poster': poster, 'tvshow.landscape': landscape})
	listitem = kodi_utils.make_listitem()
	listitem.setLabel(title)
	listitem.setProperties(props)
	listitem.setArt(art)
	if kodi_version < 20:
		listitem.setUniqueIDs({'tmdb': str(tmdb_id)})
		info = {'title': title, 'plot': item_get('overview') or '', 'premiered': premiered, 'year': year, 'rating': rating, 'genre': genres, 'mediatype': mediatype}
		if is_tvshow: info['tvshowtitle'] = title
		listitem.setInfo('video', info)
	else:
		videoinfo = listitem.getVideoInfoTag(offscreen=True)
		videoinfo.setTitle(title)
		if is_tvshow: videoinfo.setTvShowTitle(title)
		videoinfo.setUniqueIDs({'tmdb': str(tmdb_id)})
		videoinfo.setMediaType(mediatype)
		videoinfo.setPlot(item_get('overview') or '')
		if genres: videoinfo.setGenres(genres)
		if premiered: videoinfo.setPremiered(premiered)
		if year: videoinfo.setYear(int(year))
		if rating: videoinfo.setRating(float(rating))
	return url_params, listitem, False
