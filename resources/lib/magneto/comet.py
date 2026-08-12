# created by kodifitzwell for Fenomscrapers
"""
	Fenomscrapers Project
"""

import re, requests
from fenom import source_utils
from magneto.common import stremio_utils


class source:
	timeout = 7
	priority = 1
	pack_capable = False # packs parsed in sources function
	hasMovies = True
	hasEpisodes = True
	def __init__(self):
		self.language = ['en']
		self.base_link = "https://comet.feels.legal"
		self.movieSearch_link = '/stream/movie/%s.json'
		self.tvSearch_link = '/stream/series/%s:%s:%s.json'
		self.min_seeders = 0

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
			results = requests.get(url, timeout=self.timeout)
			files = results.json()['streams']
			_INFO = re.compile(r'💾.*')
		except:
			source_utils.scraper_error('COMET')
			return sources

		for file in files:
			try:
				hash = file['infoHash']
				file_title = file['description'].split('\n')
				file_info = [x for x in file_title if _INFO.search(x)][0]

				name = source_utils.clean_name(file_title[0])

				release = stremio_utils.classify_release(context, name)
				if release is None: continue
				url = stremio_utils.magnet_url(hash, name)
				seeders = stremio_utils.parse_seeders(file_info, r'👤\s*(\d+)', self.min_seeders)
				if seeders is None: continue
				quality, info, dsize = stremio_utils.release_details(release['name_info'], url, size_text=file_info)
				sources_append(stremio_utils.build_result('comet', hash, name, release, quality, info, dsize, seeders, pack_true_size=True))
			except:
				source_utils.scraper_error('COMET')
		return sources
