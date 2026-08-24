# created by kodifitzwell for Fenomscrapers
"""
	Fenomscrapers Project
"""

import xml.etree.ElementTree as ET
import requests
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
		self.base_link = "https://bitmagnetfortheweebs.midnightignite.me"
		self.movieSearch_link = '/torznab/api'
		self.tvSearch_link = '/torznab/api'
		self.min_seeders = 0

	def sources(self, data, hostDict):
		sources = []
		if not data: return sources
		sources_append = sources.append
		try:
			context = stremio_utils.request_context(data)
			if 'tvshowtitle' in data:
				url = '%s%s' % (self.base_link, self.tvSearch_link)
				params = {'t': 'tvsearch', 'imdbid': context['imdb'], 'season': context['season'], 'ep': context['episode']}
			else:
				url = '%s%s' % (self.base_link, self.movieSearch_link)
				params = {'t': 'movie', 'imdbid': context['imdb']}
			# log_utils.log('url = %s' % url)
			if 'timeout' in data: self.timeout = int(data['timeout'])
			results = requests.get(url, params=params, timeout=self.timeout)
			if hasattr(results, 'raise_for_status'): results.raise_for_status()
			files = ET.fromstring(results.text)
			if files.tag.rsplit('}', 1)[-1] != 'rss': raise ValueError('invalid Torznab root')
			channel = next((child for child in files if child.tag.rsplit('}', 1)[-1] == 'channel'), None)
			if channel is None: raise ValueError('missing Torznab channel')
		except:
			source_utils.scraper_error('BITMAGNET')
			self.scrape_failed = True
			return sources

		for file in channel.iter('item'):
			try:
				attr_dict = {'title': file.find('title').text}
				for attr in file.findall('torznab:attr', {'torznab': 'http://torznab.com/schemas/2015/feed'}):
					key, val = attr.get('name'), attr.get('value')
					if key: attr_dict[key] = val
				hash = attr_dict['infohash']

				name = source_utils.clean_name(attr_dict['title'])

				release = stremio_utils.classify_release(context, name)
				if release is None: continue
				url = stremio_utils.magnet_url(hash, name)

				seeders = stremio_utils.parse_seeders(attr_dict.get('seeders', ''), None, self.min_seeders, strict=True)
				if seeders is None: continue

				quality, info, dsize = stremio_utils.release_details(release['name_info'], url, byte_size=attr_dict.get('size'))
				sources_append(stremio_utils.build_result('bitmagnet', hash, name, release, quality, info, dsize, seeders))
			except:
				source_utils.scraper_error('BITMAGNET')
				self.scrape_partial = True
		return sources
