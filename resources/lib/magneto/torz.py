# created by kodifitzwell for Fenomscrapers
"""
	Fenomscrapers Project
"""

from json import loads as jsloads
from fenom import client
from fenom import source_utils
from magneto.common import provider_utils
from modules.source_search import RequestCoalescer


class source:
	timeout = 7
	priority = 1
	pack_capable = True
	hasMovies = True
	hasEpisodes = True
	_requests = RequestCoalescer()
	def __init__(self):
		self.language = ['en']
		self.base_link = "https://stremthru.13377001.xyz"
		self.movieSearch_link = '/v0/torrents?sid=%s'
		self.tvSearch_link = '/v0/torrents?sid=%s:%s:%s'
		self.min_seeders = 0

	def sources(self, data, hostDict):
		sources = []
		if not data: return sources
		sources_append = sources.append
		try:
			context = provider_utils.request_context(data)
			if 'tvshowtitle' in data:
				url = '%s%s' % (self.base_link, self.tvSearch_link % (context['imdb'], context['season'], context['episode']))
			else:
				url = '%s%s' % (self.base_link, self.movieSearch_link % context['imdb'])
			# log_utils.log('url = %s' % url)
			if 'timeout' in data: self.timeout = int(data['timeout'])
			files = self._get_files(url)
			provider_utils.add_filter_settings(context)
		except:
			source_utils.scraper_error('TORZ')
			return sources

		for file in files:
			try:
				hash = file['hash']
				name = file['name']

				name = source_utils.clean_name(name)

				name_info = provider_utils.direct_release(context, name)
				if name_info is None: continue

				url = 'magnet:?xt=urn:btih:%s&dn=%s' % (hash, name)

				try:
					seeders = file['seeders']
					if self.min_seeders > seeders: continue
				except: seeders = 0

				quality, info = source_utils.get_release_quality(name_info, url)
				try:
					size = float(file['size'])
					dsize, isize = source_utils.convert_size(size)
					info.insert(0, isize)
				except: dsize = 0
				info = ' | '.join(info)

				sources_append(provider_utils.build_result('torz', hash, name, name_info, quality, info, dsize, seeders))
			except:
				source_utils.scraper_error('TORZ')
		return sources

	def _get_files(self, url):
		def fetch():
			try:
				results = client.request(url, timeout=self.timeout)
				return jsloads(results)['data']['items']
			except:
				source_utils.scraper_error('TORZ')
				raise
		return self._requests.get(url, fetch, self.timeout + 1)

	def sources_packs(self, data, hostDict, search_series=False, total_seasons=None, bypass_filter=False):
		sources = []
		if not data: return sources
		sources_append = sources.append
		try:
			context = provider_utils.pack_context(data)
			url = '%s%s' % (self.base_link, self.tvSearch_link % (context['imdb'], context['season'], data['episode']))
			if 'timeout' in data: self.timeout = int(data['timeout'])
			files = self._get_files(url)
			provider_utils.add_filter_settings(context)
		except:
			source_utils.scraper_error('TORZ')
			return sources

		for file in files:
			try:
				hash = file['hash']
				name = file['name']

				release = provider_utils.pack_release(context, name, search_series, total_seasons, bypass_filter, name.replace('.(Archie.Bunker', ''))
				if release is None: continue
				name_info = release['name_info']

				url = 'magnet:?xt=urn:btih:%s&dn=%s' % (hash, name)
				try:
					seeders = file['seeders']
					if self.min_seeders > seeders: continue
				except: seeders = 0

				quality, info = source_utils.get_release_quality(name_info, url)
				try:
					size = float(file['size'])
					dsize, isize = source_utils.convert_size(size)
					info.insert(0, isize)
				except: dsize = 0
				info = ' | '.join(info)

				sources_append(provider_utils.build_result('torz', hash, name, name_info, quality, info, dsize, seeders, release))
			except:
				source_utils.scraper_error('TORZ')
		return sources
