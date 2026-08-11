import json
import requests
from modules import kodi_utils
# logger = kodi_utils.logger

timeout = 20.0
subtitle_file_prefix = 'POVLiteSubs_'
subtitle_languages = ('eng', 'vie')
subtitle_manifest = 'https://submaker.elfhosted.com/addon/0784f5f345ca13ddd7296af5f0bde65d/manifest.json'
subtitle_context_property = 'pov_lite_subtitle_context'

def _get(url, params=None, stream=False, retry=False):
	response = requests.get(url, params=params, stream=stream, timeout=timeout)
	if retry and response.status_code in (429,):
		kodi_utils.notification(32740)
		kodi_utils.sleep(10000)
		return _get(url, params=params, stream=stream)
	return response

class Subtitles(kodi_utils.xbmc_player):
	def subtitles_download(self, url):
		response = _get(url, stream=True, retry=True)
		return response if response.ok else response.reason

	def subtitles_search(self):
		if self.season: params = 'subtitles/series/%s:%s:%s' % (self.imdb_id, self.season, self.episode)
		else: params = 'subtitles/movie/%s' % self.imdb_id
		try: response = _get(self.manifest.replace('manifest', params), retry=True)
		except requests.RequestException as e: return str(e)
		return response.json()['subtitles'] if response.ok else response.reason

	def _video_file_subs(self):
		try: available_sub_language = self.getSubtitles()
		except: available_sub_language = ''
		if available_sub_language not in self.languages: return False
		self.showSubtitles(True)
		kodi_utils.notification(32852, icon=self.poster)
		return True

	def _downloaded_subs(self):
		files = kodi_utils.list_dirs(self.subtitle_path)[1]
		final_match = next((filename for language in self.languages for filename in files if filename == self._search_filename(language)), None)
		if not final_match: return False
		subtitle = '%s%s' % (self.subtitle_path, final_match)
		self.setSubtitles(subtitle)
		kodi_utils.notification(32792, icon=self.poster)
		return True

	def _searched_subs(self):
		subs = self.subtitles_search()
		if isinstance(subs, str): return kodi_utils.notification('Subtitles Error: %s' % subs)
		self._set_context(subs)
		if not subs: return kodi_utils.notification(32793, icon=self.poster)
		chosen_sub = next((item for language in self.languages for item in subs if item.get('lang') == language), None)
		if not chosen_sub: return kodi_utils.notification(32793, icon=self.poster)
		response = self.subtitles_download(chosen_sub['url'])
		if isinstance(response, str): return kodi_utils.notification('Subtitles Error: %s' % response)
		final_path = '%s%s' % (self.subtitle_path, self._search_filename(chosen_sub['lang']))
		try: content = response.text
		except: content = response.content
		with kodi_utils.open_file(final_path, 'w') as file: file.write(content)
		self.setSubtitles(final_path)
		return True

	def _search_filename(self, language):
		return '%s_%s.srt' % (self.sub_filename, language)

	def _set_context(self, subtitles=None):
		try: playing_file = self.getPlayingFile()
		except: playing_file = ''
		context = {
			'imdb_id': self.imdb_id, 'season': self.season, 'episode': self.episode, 'poster': self.poster, 'playing_file': playing_file,
			'subtitles': [{key: item.get(key, '') for key in ('id', 'lang', 'url')} for item in subtitles or () if item.get('url')]
		}
		kodi_utils.set_property(subtitle_context_property, json.dumps(context))

	def run(self, query, imdb_id, season, episode, poster):
		self.manifest, self.languages = subtitle_manifest, subtitle_languages
		self.imdb_id, self.season, self.episode, self.poster = imdb_id, season, episode, poster
		self.subtitle_path = 'special://temp/'
		if season: self.sub_filename = '%s%s_%s_%s' % (subtitle_file_prefix, self.imdb_id, self.season, self.episode)
		else: self.sub_filename = '%s%s' % (subtitle_file_prefix, self.imdb_id)
		self._set_context()
		kodi_utils.sleep(2500)
		return self._video_file_subs() or self._downloaded_subs() or self._searched_subs()
