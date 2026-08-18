from pathlib import Path
import json
import sys

lib_path = str(Path(__file__).parent)
if lib_path not in sys.path: sys.path.insert(0, lib_path)

from indexers.subtitles import SubtitleCancelled, Subtitles, subtitle_context_property, subtitle_languages
from modules import kodi_utils

language_names = {'eng': 'English', 'vie': 'Vietnamese'}
provider_names = {'opensubtitles': 'OpenSubtitles', 'subdl': 'SubDL', 'subsource': 'SubSource'}

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
	try: return json.loads(kodi_utils.get_property(subtitle_context_property))
	except: return {}

def _video_metadata():
	metadata = {'imdb_id': '', 'season': None, 'episode': None, 'is_episode': False, 'year': ''}
	try:
		video_info = kodi_utils.player.getVideoInfoTag()
		metadata.update({
			'imdb_id': video_info.getUniqueID('imdb'), 'season': video_info.getSeason(), 'episode': video_info.getEpisode(),
			'is_episode': bool(video_info.getTVShowTitle()), 'year': video_info.getYear()
		})
	except: pass
	metadata['imdb_id'] = metadata['imdb_id'] or kodi_utils.get_infolabel('VideoPlayer.UniqueID(imdb)') or kodi_utils.get_infolabel('VideoPlayer.IMDBNumber')
	metadata['is_episode'] = metadata['is_episode'] or kodi_utils.get_infolabel('VideoPlayer.DBTYPE') == 'episode'
	if metadata['is_episode'] and metadata['season'] in (None, '', -1):
		metadata['season'], metadata['episode'] = kodi_utils.get_infolabel('VideoPlayer.Season'), kodi_utils.get_infolabel('VideoPlayer.Episode')
	return metadata

def _client():
	if not kodi_utils.player.isPlayingVideo(): return None
	try: playing_file = kodi_utils.player.getPlayingFile()
	except: playing_file = ''
	context, metadata = _context(), _video_metadata()
	if context.get('playing_file') and context['playing_file'] != playing_file and context.get('imdb_id') != metadata['imdb_id']: context = {}
	imdb_id = context.get('imdb_id') or metadata['imdb_id']
	if not imdb_id or imdb_id == 'None': return None
	season = context.get('season')
	episode = context.get('episode')
	if season in (None, '') and metadata['is_episode']: season, episode = metadata['season'], metadata['episode']
	client = Subtitles().configure(
		imdb_id, season, episode, context.get('poster', ''), playing_file, context.get('release_name', ''),
		context.get('quality', ''), context.get('extra_info', ''), context.get('year') or metadata.get('year', '')
	)
	return client, context

def _search(handle):
	configured = _client()
	if not configured: return kodi_utils.notification('Play a BINGIE Lite video before searching subtitles.')
	client, context = configured
	kodi_utils.logger('BINGIE Lite Subtitles', 'mode=manual operation=search outcome=started')
	subtitles = context.get('subtitles') or client.subtitles_search()
	client._ensure_current_playback()
	kodi_utils.logger('BINGIE Lite Subtitles', 'mode=manual operation=search outcome=complete count=%s' % len(subtitles))
	client._set_context(subtitles)
	for language in subtitle_languages:
		for result_number, subtitle in enumerate((item for item in subtitles if item.get('lang') == language and item.get('provider') and item.get('id')), 1):
			listitem = kodi_utils.make_listitem()
			listitem.setLabel(language_names[language])
			art = {'thumb': language}
			rating_icon = _rating_icon(subtitle.get('rating'))
			if rating_icon: art['icon'] = rating_icon
			listitem.setArt(art)
			if subtitle.get('sync') is True: listitem.setProperty('sync', 'true')
			provider = subtitle['provider']
			listitem.setLabel2(_result_label(subtitle, result_number))
			url = kodi_utils.build_url({'action': 'download', 'provider': provider, 'candidate': subtitle['id'], 'language': language, 'result': result_number})
			kodi_utils.add_item(handle, url, listitem, False)

def _download(handle, params):
	configured = _client()
	if not configured:
		kodi_utils.logger('BINGIE Lite Subtitles', 'mode=manual operation=download outcome=cancelled category=playback_stopped')
		return
	client = configured[0]
	provider, candidate_id = params.get('provider', ''), params.get('candidate', '')
	if provider not in ('opensubtitles', 'subdl', 'subsource') or not candidate_id: return
	payload = client.download_by_id(provider, candidate_id)
	if not payload: return
	if client._cancelled(): return
	language, result_number = params.get('language', 'eng'), params.get('result', '1')
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
