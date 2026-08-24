from pathlib import Path
import json
import sys

lib_path = str(Path(__file__).parent)
if lib_path not in sys.path: sys.path.insert(0, lib_path)

from indexers.subtitles import SubtitleCancelled, Subtitles, subtitle_context_property, subtitle_languages
try:
	from indexers.subtitles import playing_file_fingerprint, stable_media_identity, subtitle_context_max_bytes, subtitle_context_version
except ImportError:
	# Compatibility for isolated callers that provide the legacy subtitle module surface.
	import hashlib
	playing_file_fingerprint = lambda value: hashlib.sha256(str(value or '').encode('utf-8')).hexdigest() if value else ''
	stable_media_identity = lambda imdb_id='', tmdb_id='', mediatype='', title='', year='', season=None, episode=None: ('imdb:%s' % imdb_id) if imdb_id else (('%s:tmdb:%s' % (mediatype, tmdb_id)) if tmdb_id else '')
	subtitle_context_max_bytes, subtitle_context_version = 64 * 1024, 2
from modules import kodi_utils

language_names = {'eng': 'English', 'vie': 'Vietnamese'}
provider_names = {'opensubtitles': 'OpenSubtitles', 'subdl': 'SubDL', 'subsource': 'SubSource'}
config_issue_names = {
	'missing': 'provider settings are missing', 'unreadable': 'provider settings cannot be read',
	'invalid_json': 'provider settings contain invalid JSON', 'invalid_schema': 'provider settings are invalid'
}

def _diagnostic_message(diagnostics):
	if not isinstance(diagnostics, dict): return ''
	category = diagnostics.get('config')
	if category in config_issue_names: return 'Subtitle search unavailable: %s.' % config_issue_names[category]
	providers = [provider_names[name] for name in diagnostics.get('providers', ()) if name in provider_names]
	return 'Subtitle provider failed: %s.' % ', '.join(providers) if providers else ''

def _release_label(subtitle):
	values = [subtitle.get('release')]
	release_names = subtitle.get('release_names')
	if isinstance(release_names, (list, tuple)): values.extend(release_names)
	else: values.append(release_names)
	for value in values:
		if not isinstance(value, str): continue
		value = value.strip()
		if value: return value[:120]
	return 'Release name unavailable'

def _result_label(subtitle, result_number):
	provider = provider_names.get(subtitle.get('provider'), str(subtitle.get('provider') or 'Subtitle provider'))
	return '%s #%s · %s' % (provider, result_number, _release_label(subtitle))

def _rating_icon(rating):
	try: rating = min(max(float(rating), 0.0), 10.0)
	except (TypeError, ValueError): return ''
	return str(int(rating / 2.0 + 0.5))

def _context():
	try:
		payload = kodi_utils.get_property(subtitle_context_property)
		if not isinstance(payload, str) or len(payload.encode('utf-8')) > subtitle_context_max_bytes: return {}
		context = json.loads(payload)
		return context if isinstance(context, dict) and context.get('version') == subtitle_context_version else {}
	except: return {}

def _video_metadata():
	metadata = {'imdb_id': '', 'tmdb_id': '', 'season': None, 'episode': None, 'is_episode': False, 'mediatype': '', 'title': '', 'year': ''}
	try:
		video_info = kodi_utils.player.getVideoInfoTag()
		metadata.update({
			'imdb_id': video_info.getUniqueID('imdb'), 'tmdb_id': video_info.getUniqueID('tmdb'), 'season': video_info.getSeason(), 'episode': video_info.getEpisode(),
			'is_episode': bool(video_info.getTVShowTitle()), 'title': video_info.getTVShowTitle() or video_info.getTitle(), 'year': video_info.getYear()
		})
	except: pass
	metadata['imdb_id'] = metadata['imdb_id'] or kodi_utils.get_infolabel('VideoPlayer.UniqueID(imdb)') or kodi_utils.get_infolabel('VideoPlayer.IMDBNumber')
	metadata['tmdb_id'] = metadata['tmdb_id'] or kodi_utils.get_infolabel('VideoPlayer.UniqueID(tmdb)')
	metadata['is_episode'] = metadata['is_episode'] or kodi_utils.get_infolabel('VideoPlayer.DBTYPE') == 'episode'
	if metadata['is_episode'] and metadata['season'] in (None, '', -1):
		metadata['season'], metadata['episode'] = kodi_utils.get_infolabel('VideoPlayer.Season'), kodi_utils.get_infolabel('VideoPlayer.Episode')
	elif not metadata['is_episode']: metadata['season'], metadata['episode'] = None, None
	metadata['mediatype'] = 'episode' if metadata['is_episode'] else 'movie'
	metadata['title'] = metadata['title'] or kodi_utils.get_infolabel('VideoPlayer.TVShowTitle') or kodi_utils.get_infolabel('VideoPlayer.Title')
	return metadata

def _client():
	if not kodi_utils.player.isPlayingVideo(): return None
	try: playing_file = kodi_utils.player.getPlayingFile()
	except: playing_file = ''
	context, metadata = _context(), _video_metadata()
	current_identity = stable_media_identity(metadata.get('imdb_id'), metadata.get('tmdb_id'), metadata.get('mediatype'), metadata.get('title'), metadata.get('year'), metadata.get('season'), metadata.get('episode'))
	if context.get('version') == subtitle_context_version and (context.get('playing_fingerprint') != playing_file_fingerprint(playing_file) or not current_identity or context.get('media_identity') != current_identity): context = {}
	imdb_id = context.get('imdb_id') or metadata['imdb_id']
	tmdb_id = context.get('tmdb_id') or metadata.get('tmdb_id', '')
	mediatype = context.get('mediatype') or metadata.get('mediatype', '')
	title = context.get('title') or metadata.get('title', '')
	if not stable_media_identity(imdb_id, tmdb_id, mediatype, title, context.get('year') or metadata['year'], context.get('season', metadata['season']), context.get('episode', metadata['episode'])): return None
	season = context.get('season')
	episode = context.get('episode')
	if season in (None, '') and metadata['is_episode']: season, episode = metadata['season'], metadata['episode']
	arguments = [
		imdb_id, season, episode, context.get('poster', ''), playing_file, context.get('release_name', ''), context.get('quality', ''), context.get('extra_info', ''), context.get('year') or metadata.get('year', ''),
		tmdb_id, mediatype, title, context.get('generation', '')
	]
	client = Subtitles().configure(*arguments)
	return client, context

def _search(handle):
	configured = _client()
	if not configured: return kodi_utils.notification('Play a BINGIE Lite video before searching subtitles.')
	client, context = configured
	kodi_utils.logger('BINGIE Lite Subtitles', 'mode=manual operation=search outcome=started')
	subtitles = context.get('subtitles') or client.subtitles_search()
	client._ensure_current_playback()
	diagnostics = client.subtitle_diagnostics() if hasattr(client, 'subtitle_diagnostics') else {}
	message = _diagnostic_message(diagnostics)
	if message:
		if diagnostics.get('config') in config_issue_names: kodi_utils.ok_dialog('Subtitle search', message)
		else: kodi_utils.notification(message)
	kodi_utils.logger('BINGIE Lite Subtitles', 'mode=manual operation=search outcome=complete count=%s' % len(subtitles))
	updated_context = client._set_context(subtitles)
	if isinstance(updated_context, dict): context, subtitles = updated_context, updated_context.get('subtitles', ())
	for language in subtitle_languages:
		for result_number, subtitle in enumerate((item for item in subtitles if item.get('lang') == language and item.get('provider') and item.get('id')), 1):
			listitem = kodi_utils.make_listitem()
			listitem.setLabel(language_names[language])
			art = {'thumb': language}
			rating_icon = _rating_icon(subtitle.get('rating'))
			if rating_icon: art['icon'] = rating_icon
			listitem.setArt(art)
			if subtitle.get('sync') is True: listitem.setProperty('sync', 'true')
			listitem.setLabel2(_result_label(subtitle, result_number))
			if context.get('version') == subtitle_context_version and subtitle.get('token'):
				params = {'action': 'download', 'generation': context.get('generation', ''), 'candidate': subtitle['token'], 'language': language, 'result': result_number}
			else: params = {'action': 'download', 'provider': subtitle['provider'], 'candidate': subtitle['id'], 'language': language, 'result': result_number}
			url = kodi_utils.build_url(params)
			kodi_utils.add_item(handle, url, listitem, False)

def _download(handle, params):
	configured = _client()
	if not configured:
		kodi_utils.logger('BINGIE Lite Subtitles', 'mode=manual operation=download outcome=cancelled category=playback_stopped')
		return
	client, context = configured
	if context.get('version') == subtitle_context_version:
		generation, candidate_token = params.get('generation', ''), params.get('candidate', '')
		if not generation or generation != context.get('generation') or not candidate_token: return
		candidate = next((item for item in context.get('subtitles', ()) if isinstance(item, dict) and item.get('token') == candidate_token), None)
		if not candidate: return
		provider = candidate.get('provider', '')
		language = candidate.get('lang', '')
		payload = client.download_candidate(candidate)
	else:
		provider, candidate_id = params.get('provider', ''), params.get('candidate', '')
		if provider not in ('opensubtitles', 'subdl', 'subsource') or not candidate_id: return
		language = params.get('language', 'eng')
		payload = client.download_by_id(provider, candidate_id)
	if not payload: return
	if client._cancelled(): return
	result_number = params.get('result', '1')
	extension = client._safe_extension(payload.get('extension'))
	if language not in subtitle_languages or not result_number.isdigit() or not extension: return
	final_path = '%s%s_%s_%s_%s.%s' % (client.subtitle_path, client.sub_filename, language, provider, result_number, extension)
	if not client.save_subtitle(payload, final_path): return
	if client._cancelled(): return
	listitem = kodi_utils.make_listitem()
	listitem.setLabel(final_path)
	kodi_utils.add_item(handle, final_path, listitem, False)

def run(sys_obj):
	handle = int(sys_obj.argv[1])
	params = kodi_utils.parsed_query(sys_obj.argv[2])
	try:
		if params.get('action') in ('search', 'manualsearch'): _search(handle)
		elif params.get('action') == 'download': _download(handle, params)
	except SubtitleCancelled:
		kodi_utils.logger('BINGIE Lite Subtitles', 'mode=manual operation=%s outcome=cancelled category=playback_changed' % params.get('action', 'unknown'))
	except Exception as error:
		kodi_utils.logger('BINGIE Lite Subtitles', 'mode=manual operation=%s outcome=failed category=unexpected detail=%s' % (params.get('action', 'unknown'), type(error).__name__))
		if params.get('action') in ('search', 'manualsearch'): kodi_utils.notification(32856)
	finally: kodi_utils.end_directory(handle, False)

if __name__ == '__main__': run(sys)
