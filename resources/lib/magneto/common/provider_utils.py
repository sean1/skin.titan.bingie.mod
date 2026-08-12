from fenom import source_utils


def request_context(data):
	is_episode = 'tvshowtitle' in data
	title = data['tvshowtitle'] if is_episode else data['title']
	context = {
		'title': title.replace('&', 'and').replace('Special Victims Unit', 'SVU').replace('/', ' '),
		'aliases': source_utils.aliases_to_array(data['aliases']),
		'episode_title': data['title'] if is_episode else None,
		'year': data['year'],
		'imdb': data['imdb']
	}
	if is_episode:
		context.update({'season': data['season'], 'episode': data['episode'], 'hdlr': 'S%02dE%02d' % (int(data['season']), int(data['episode']))})
	else:
		context['hdlr'] = data['year']
	return context


def pack_context(data):
	return {
		'title': data['tvshowtitle'].replace('&', 'and').replace('Special Victims Unit', 'SVU').replace('/', ' '),
		'aliases': source_utils.aliases_to_array(data['aliases']),
		'imdb': data['imdb'], 'year': data['year'], 'season': data['season']
	}


def add_filter_settings(context):
	context['undesirables'] = source_utils.get_undesirables()
	context['check_foreign_audio'] = source_utils.check_foreign_audio()
	return context


def direct_release(context, name):
	if not source_utils.check_title(context['title'], context['aliases'], name, context['hdlr'], context['year']): return None
	name_info = source_utils.info_from_name(name, context['title'], context['year'], context['hdlr'], context['episode_title'])
	if source_utils.remove_lang(name_info, context['check_foreign_audio']): return None
	if context['undesirables'] and source_utils.remove_undesirables(name_info, context['undesirables']): return None
	return name_info


def pack_release(context, name, search_series=False, total_seasons=None, bypass_filter=False, filter_name=None):
	filter_name = name if filter_name is None else filter_name
	episode_start, episode_end, last_season = 0, 0, None
	if search_series:
		if bypass_filter:
			last_season = total_seasons
		else:
			valid, last_season = source_utils.filter_show_pack(context['title'], context['aliases'], context['imdb'], context['year'], context['season'], filter_name, total_seasons)
			if not valid: return None
		package = 'show'
	else:
		if not bypass_filter:
			valid, episode_start, episode_end = source_utils.filter_season_pack(context['title'], context['aliases'], context['year'], context['season'], filter_name)
			if not valid: return None
		package = 'season'
	name_info = source_utils.info_from_name(name, context['title'], context['year'], season=context['season'], pack=package)
	if source_utils.remove_lang(name_info, context['check_foreign_audio']): return None
	if context['undesirables'] and source_utils.remove_undesirables(name_info, context['undesirables']): return None
	return {'name_info': name_info, 'package': package, 'last_season': last_season, 'episode_start': episode_start, 'episode_end': episode_end}


def build_result(provider, info_hash, name, name_info, quality, info, size, seeders=0, release=None):
	item = {
		'source': 'torrent', 'language': 'en', 'direct': False, 'debridonly': True,
		'provider': provider, 'hash': info_hash, 'url': 'magnet:?xt=urn:btih:%s&dn=%s' % (info_hash, name), 'name': name, 'name_info': name_info,
		'quality': quality, 'info': info, 'size': size, 'seeders': seeders
	}
	if release:
		item['package'] = release['package']
		if release['package'] == 'show': item['last_season'] = release['last_season']
		elif release['episode_start']: item.update({'episode_start': release['episode_start'], 'episode_end': release['episode_end']})
	return item
