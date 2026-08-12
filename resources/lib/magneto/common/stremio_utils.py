import re

from fenom import source_utils


SIZE_PATTERN = re.compile(r'((?:\d+\,\d+\.\d+|\d+\.\d+|\d+\,\d+|\d+)\s*(?:GB|GiB|Gb|MB|MiB|Mb))')


def request_context(data):
	is_episode = 'tvshowtitle' in data
	title = data['tvshowtitle'] if is_episode else data['title']
	context = {
		'title': title.replace('&', 'and').replace('Special Victims Unit', 'SVU').replace('/', ' '),
		'aliases': source_utils.aliases_to_array(data['aliases']),
		'episode_title': data['title'] if is_episode else None,
		'total_seasons': data['total_seasons'] if is_episode else None,
		'year': data['year'],
		'imdb': data['imdb'],
		'undesirables': source_utils.get_undesirables(),
		'check_foreign_audio': source_utils.check_foreign_audio()
	}
	if is_episode:
		context.update({'season': data['season'], 'episode': data['episode'], 'hdlr': 'S%02dE%02d' % (int(data['season']), int(data['episode']))})
	else:
		context['hdlr'] = data['year']
	return context


def classify_release(context, name):
	package, episode_start, episode_end, last_season = None, 0, None, None
	if not source_utils.check_title(context['title'], context['aliases'], name, context['hdlr'], context['year']):
		if context['total_seasons'] is None: return None
		valid, episode_start, episode_end = source_utils.filter_season_pack(context['title'], context['aliases'], context['year'], context['season'], name)
		if valid:
			package = 'season'
		else:
			valid, last_season = source_utils.filter_show_pack(context['title'], context['aliases'], context['imdb'], context['year'], context['season'], name, context['total_seasons'])
			if not valid: return None
			package = 'show'
	if package:
		name_info = source_utils.info_from_name(name, context['title'], context['year'], season=context['season'], pack=package)
	else:
		name_info = source_utils.info_from_name(name, context['title'], context['year'], context['hdlr'], context['episode_title'])
	if source_utils.remove_lang(name_info, context['check_foreign_audio']): return None
	if context['undesirables'] and source_utils.remove_undesirables(name_info, context['undesirables']): return None
	return {'name_info': name_info, 'package': package, 'last_season': last_season, 'episode_start': episode_start, 'episode_end': episode_end}


def magnet_url(info_hash, name):
	return 'magnet:?xt=urn:btih:%s&dn=%s' % (info_hash, name)


def parse_seeders(text, pattern, minimum, strict=False):
	try:
		seeders = int(text) if strict else int(re.search(pattern, text).group(1))
		return None if minimum > seeders else seeders
	except:
		return 0


def release_details(name_info, url, size_text=None, byte_size=None):
	quality, info = source_utils.get_release_quality(name_info, url)
	try:
		if byte_size is not None:
			dsize, isize = source_utils.convert_size(float(byte_size))
		else:
			dsize, isize = source_utils._size(SIZE_PATTERN.search(size_text).group(0))
		info.insert(0, isize)
	except:
		dsize = 0
	return quality, ' | '.join(info), dsize


def build_result(provider, info_hash, name, release, quality, info, size, seeders=0, pack_true_size=False):
	item = {
		'source': 'torrent', 'language': 'en', 'direct': False, 'debridonly': True,
		'provider': provider, 'hash': info_hash, 'url': magnet_url(info_hash, name), 'name': name, 'name_info': release['name_info'],
		'quality': quality, 'info': info, 'size': size, 'seeders': seeders
	}
	if release['package']:
		item['package'] = release['package']
		if pack_true_size: item['true_size'] = True
	if release['package'] == 'show': item['last_season'] = release['last_season']
	if release['episode_start']: item.update({'episode_start': release['episode_start'], 'episode_end': release['episode_end']})
	return item
