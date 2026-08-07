from pathlib import Path
import json
import sys

lib_path = str(Path(__file__).parent)
if lib_path not in sys.path: sys.path.insert(0, lib_path)

from indexers.subtitles import Subtitles, subtitle_context_property, subtitle_file_prefix, subtitle_languages, subtitle_manifest
from modules import kodi_utils

language_names = {'eng': 'English', 'vie': 'Vietnamese'}

def _context():
	try: return json.loads(kodi_utils.get_property(subtitle_context_property))
	except: return {}

def _video_metadata():
	metadata = {'imdb_id': '', 'season': None, 'episode': None, 'is_episode': False}
	try:
		video_info = kodi_utils.player.getVideoInfoTag()
		metadata.update({
			'imdb_id': video_info.getUniqueID('imdb'), 'season': video_info.getSeason(), 'episode': video_info.getEpisode(),
			'is_episode': bool(video_info.getTVShowTitle())
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
	client = Subtitles()
	client.manifest, client.languages = subtitle_manifest, subtitle_languages
	client.imdb_id, client.season, client.episode = imdb_id, season, episode
	client.poster, client.subtitle_path = context.get('poster', ''), 'special://temp/'
	if season not in (None, ''): client.sub_filename = '%s%s_%s_%s' % (subtitle_file_prefix, imdb_id, season, episode)
	else: client.sub_filename = '%s%s' % (subtitle_file_prefix, imdb_id)
	return client, context

def _search(handle):
	configured = _client()
	if not configured: return kodi_utils.notification('Play a BINGIE Lite video before searching SubMaker subtitles.')
	client, context = configured
	kodi_utils.logger('BINGIE Lite Subtitles', 'Searching IMDb %s season %s episode %s' % (client.imdb_id, client.season, client.episode))
	subtitles = context.get('subtitles') or client.subtitles_search()
	if isinstance(subtitles, str): return kodi_utils.notification('Subtitles Error: %s' % subtitles)
	kodi_utils.logger('BINGIE Lite Subtitles', 'SubMaker returned %s results' % len(subtitles))
	client._set_context(subtitles)
	for language in subtitle_languages:
		for result_number, subtitle in enumerate((item for item in subtitles if item.get('lang') == language and item.get('url')), 1):
			listitem = kodi_utils.make_listitem()
			listitem.setLabel(language_names[language])
			listitem.setLabel2('%s subtitle %s' % (language_names[language], result_number))
			url = kodi_utils.build_url({'action': 'download', 'url': subtitle['url'], 'language': language, 'result': result_number})
			kodi_utils.add_item(handle, url, listitem, False)

def _download(handle, params):
	configured = _client()
	if not configured: return kodi_utils.notification('The BINGIE Lite video is no longer playing.')
	client = configured[0]
	response = client.subtitles_download(params['url'])
	if isinstance(response, str): return kodi_utils.notification('Subtitles Error: %s' % response)
	language, result_number = params.get('language', 'eng'), params.get('result', '1')
	final_path = '%s%s_%s_%s.srt' % (client.subtitle_path, client.sub_filename, language, result_number)
	try: content = response.text
	except: content = response.content
	with kodi_utils.open_file(final_path, 'w') as file: file.write(content)
	listitem = kodi_utils.make_listitem()
	listitem.setLabel(final_path)
	kodi_utils.add_item(handle, final_path, listitem, False)

def run(sys_obj):
	handle = int(sys_obj.argv[1])
	params = kodi_utils.parsed_query(sys_obj.argv[2])
	try:
		if params.get('action') in ('search', 'manualsearch'): _search(handle)
		elif params.get('action') == 'download': _download(handle, params)
	finally: kodi_utils.end_directory(handle, False)

if __name__ == '__main__': run(sys)
