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
		self.base_link = "https://mediafusionfortheweebs.midnightignite.me"
		self.movieSearch_link = '/%s/stream/movie/%s.json'
		self.tvSearch_link = '/%s/stream/series/%s:%s:%s.json'
		self.min_seeders = 0

	def sources(self, data, hostDict):
		sources = []
		if not data: return sources
		sources_append = sources.append
		try:
			context = stremio_utils.request_context(data)
			if 'tvshowtitle' in data:
				url = '%s%s' % (self.base_link, self.tvSearch_link % (self._token(), context['imdb'], context['season'], context['episode']))
			else:
				url = '%s%s' % (self.base_link, self.movieSearch_link % (self._token(), context['imdb']))
			# log_utils.log('url = %s' % url)
			if 'timeout' in data: self.timeout = int(data['timeout'])
			results = requests.get(url, timeout=self.timeout)
			if hasattr(results, 'raise_for_status'): results.raise_for_status()
			files = results.json()['streams']
			if not isinstance(files, list): raise ValueError('Invalid streams payload')
			_INFO = re.compile(r'💾.*')
		except:
			source_utils.scraper_error('MEDIAFUSION')
			self.scrape_failed = True
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
				sources_append(stremio_utils.build_result('mediafusion', hash, name, release, quality, info, dsize, seeders))
			except:
				source_utils.scraper_error('MEDIAFUSION')
				self.scrape_partial = True
		return sources

	def _token(self):
		return (
			'D-h5mpsX35oygOGFiHutl66dLAPiXzjQTODPXKQuKBaQOLjwNBbVkSPi7TJPr0gdykpCFREq8JOh'
			'DHZcvoS_UNZsWpsbjscCAwzgqc9VvP0S3Wt9lz5blcPT8lU6fcHdAHYctp_yde6nWKtSQ1O9Tjeh'
			'GNwajH9TjGZwn6rOybPFmoMpccXfTkB3Xwe9xRhT9O-bKzoYnGnlG8fCDxlNGdzrnlythePc3C7O'
			'phF8b5GyhuSnvBhxD7dTfkI77Dbay8_k_wqS-me9euZQ-oyOJBNTOIsO8HiWQhLGCC8m9rYsqJT6'
			'QF1Xhn-2bNzlukfbSYh_X1kOFdi6Y-YkBeEYokDlQHzzU45qmrj2b1Nz-GALcJHjNDJEMF3h9Eyx'
			'7UcmGWT1qvTpv_tcXjAX37ceqrWH-e_EqwVkvQDjNnmpjOhBWhuUW2R-0KbvxKUn1s5d2jZjLBxC'
			'bMotHIC-G2SrVCLgC_KV0OUainevUHKOKTe0CQmWz1HKV1ju52CFZFZYAWkOAX5cw55qzNnWl_nQ'
			'RnLyngrW_P6aYqghbYyyrAvQ6hCrIbSnVj4GsMFIelcMETvGW4jIXdwZGZA1L8gCzmyCbI9vAqPv'
			'dZxRWb7roc2EnB7gaSYdFtTP9gGoFKKkQ-9aircUEiPXjkP4QWO7lVI4GZri7KKCKjBM7-hWf4nm'
			'ttY7lJS_4Te_H80BeR_qpqeYQ6V0gpVwihARA6cIsZFbWmQXtoYNO16jt1ZqeVztwR6L1IQQnAsH'
			'ANyR5kF7ovGCOnhWlDDxO3nk8fhm3s0k7XewrMisZHy1zNsivTjvJW6KoVwghLn8-QCTf9PEPoPj'
			's6tW5KjciaRvbMg5-mbhpAhYOmPisB4ZyW63vWY6TeU1OBJV0T_fkHtgbvgiTEX5RFoRVDLnhaof'
			'-xHVw2oCc2AdXmBDVROmFjY8x9KEyZ91QfNjHnrTFmGetelcHE'
		)
