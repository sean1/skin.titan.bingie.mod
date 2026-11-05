# created by Venom for Fenomscrapers (updated 3-02-2022)
"""
	Fenomscrapers Project
"""

from json import loads as jsloads
import re
from fenom import client
from fenom import source_utils
from magneto.common import stremio_utils


class source:
	timeout = 5
	priority = 1
	pack_capable = False # packs parsed in sources function
	hasMovies = True
	hasEpisodes = True
	def __init__(self):
		self.language = ['en']
		self.base_link = "https://torrentio.strem.fun"
		self.movieSearch_link = '/stream/movie/%s.json'
		self.tvSearch_link = '/stream/series/%s:%s:%s.json'
		self.min_seeders = 0
# Currently supports YTS(+), EZTV(+), RARBG(+), 1337x(+), ThePirateBay(+), KickassTorrents(+), TorrentGalaxy(+), HorribleSubs(+), NyaaSi(+), NyaaPantsu(+), Rutor(+), Comando(+), ComoEuBaixo(+), Lapumia(+), OndeBaixa(+), Torrent9(+).

	def sources(self, data, hostDict):
		sources = []
		if not data: return sources
		sources_append = sources.append
		try:
			context = stremio_utils.request_context(data)
			if 'tvshowtitle' in data:
				url = '%s%s' % (self.base_link, self.tvSearch_link % (context['imdb'], context['season'], context['episode']))
			else:
				url = '%s%s' % (self.base_link, self.movieSearch_link % context['imdb'])
			# log_utils.log('url = %s' % url)
			if 'timeout' in data: self.timeout = int(data['timeout'])
			results = client.request(url, timeout=self.timeout)
			files = jsloads(results)['streams']
			if not isinstance(files, list): raise ValueError('Invalid streams payload')
			_INFO = re.compile(r'👤.*')
		except:
			source_utils.scraper_error('TORRENTIO')
			self.scrape_failed = True
			return sources

		for file in files:
			try:
				hash = file['infoHash']
				file_title = file['title'].split('\n')
				file_info = [x for x in file_title if _INFO.match(x)][0]
				# try:
					# index = file_title.index(file_info)
					# if index == 1: combo = file_title[0].replace(' ', '.')
					# else: combo = ''.join(file_title[0:2]).replace(' ', '.')
					# if '🇷🇺' in file_title[index+1] and not any(value in combo for value in ('.en.', '.eng.', 'english')): continue
				# except: pass

				name = source_utils.clean_name(file_title[0])

				release = stremio_utils.classify_release(context, name)
				if release is None and 'season' in context:
					# Some Torrentio titles separate the season and episode with a hyphen or dot.
					marker = r'(?<![a-z0-9])S%02d[-.]E%02d(?!\d)' % (int(context['season']), int(context['episode']))
					normalized, replacements = re.subn(marker, context['hdlr'], name, flags=re.I)
					if replacements:
						title, separator, details = normalized.partition(context['hdlr'])
						normalized = re.sub(r'\(Chinese:[^a-z0-9)]*\)', '', title, flags=re.I) + separator + details
					if replacements and source_utils.check_title(context['title'], context['aliases'], normalized, context['hdlr'], context['year']):
						release = stremio_utils.classify_release(context, normalized)
						if release is not None: name = normalized
				if release is None and 'season' in context and len(file_title) > 1:
					# Torrentio may return a pack title followed by the selected episode filename.
					# Classify that filename independently so both the show and episode must match.
					filename = file_title[1]
					if re.search(r'\.(?:mkv|mp4|avi|m4v|mov|wmv|ts|webm)$', filename, re.I):
						name = source_utils.clean_name(filename)
						if re.search(re.escape(context['hdlr']) + r'(?!\d)', name, re.I) and source_utils.check_title(context['title'], context['aliases'], name, context['hdlr'], context['year']):
							release = stremio_utils.classify_release(context, name)
				if release is None: continue
				url = stremio_utils.magnet_url(hash, name)
				# if not episode_title: #filter for eps returned in movie query (rare but movie and show exists for Run in 2020)
					# ep_strings = [r'(?:\.|\-)s\d{2}e\d{2}(?:\.|\-|$)', r'(?:\.|\-)s\d{2}(?:\.|\-|$)', r'(?:\.|\-)season(?:\.|\-)\d{1,2}(?:\.|\-|$)']
					# name_lower = name.lower()
					# if any(re.search(item, name_lower) for item in ep_strings): continue

				seeders = stremio_utils.parse_seeders(file_info, r'(\d+)', self.min_seeders)
				if seeders is None: continue
				quality, info, dsize = stremio_utils.release_details(release['name_info'], url, size_text=file_info)
				sources_append(stremio_utils.build_result('torrentio', hash, name, release, quality, info, dsize, seeders, pack_true_size=True))
			except:
				source_utils.scraper_error('TORRENTIO')
				self.scrape_partial = True
		return sources
