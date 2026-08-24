# created by kodifitzwell for Fenomscrapers
"""
	Fenomscrapers Project
"""

import requests
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
		self.base_link = "https://zilean.stremio.ru"
		self.movieSearch_link = '/dmm/filtered?ImdbId=%s'
		self.tvSearch_link = '/dmm/filtered?ImdbId=%s&Season=%s&Episode=%s'
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
			if hasattr(results, 'raise_for_status'): results.raise_for_status()
			files = results.json()
			if not isinstance(files, list): raise ValueError('Invalid result payload')
		except:
			source_utils.scraper_error('ZILEAN')
			self.scrape_failed = True
			return sources

		for file in files:
			try:
				hash = file['info_hash']
				name = source_utils.clean_name(file['raw_title'])

				release = stremio_utils.classify_release(context, name)
				if release is None: continue
				url = stremio_utils.magnet_url(hash, name)
				quality, info, dsize = stremio_utils.release_details(release['name_info'], url, byte_size=file.get('size'))
				sources_append(stremio_utils.build_result('zilean', hash, name, release, quality, info, dsize))
			except:
				source_utils.scraper_error('ZILEAN')
				self.scrape_partial = True
		return sources
