# created by Venom for Fenomscrapers (updated 12-16-2021)
"""
	Fenomscrapers Project
"""

import re
from urllib.parse import quote_plus, unquote_plus
from fenom import client
from fenom import source_utils
from modules.source_utils import ALTERNATE_EP_RANGE_REGEX


class source:
	timeout = 5
	priority = 5
	pack_capable = False
	hasMovies = True
	hasEpisodes = True
	def __init__(self):
		self.language = ['en']
		self.base_link = "https://nyaa.si"
		self.search_link = '/?f=0&c=0_0&q=%s'
		self.min_seeders = 1

	def sources(self, data, hostDict):
		sources = []
		if not data: return sources
		sources_append = sources.append
		try:
			title = data['tvshowtitle'] if 'tvshowtitle' in data else data['title']
			title = title.replace('&', 'and').replace('Special Victims Unit', 'SVU').replace('/', ' ')
			aliases = source_utils.aliases_to_array(data['aliases'])
			episode_title = data['title'] if 'tvshowtitle' in data else None
			year = data['year']
			hdlr = 'S%02dE%02d' % (int(data['season']), int(data['episode'])) if 'tvshowtitle' in data else year
			hdlr2 = 'S%d - %d' % (int(data['season']), int(data['episode'])) if 'tvshowtitle' in data else year

			query = '%s %s' % (title, hdlr)
			query = re.sub(r'(\\\|/| -|:|;|\*|\?|"|\'|<|>|\|)', '', query)
			query2 = '%s %s' % (title, hdlr2)
			query2 = re.sub(r'(\\\|/| -|:|;|\*|\?|"|\'|<|>|\|)', '', query2)

			urls = []
			url = self.search_link % quote_plus(query)
			url = '%s%s' % (self.base_link, url)
			urls.append(url)
			url2 = self.search_link % quote_plus(query2)
			url2 = '%s%s' % (self.base_link, url2)
			urls.append(url2)
			# log_utils.log('urls = %s' % urls)
			if 'timeout' in data: self.timeout = int(data['timeout'])
			undesirables = source_utils.get_undesirables()
			check_foreign_audio = source_utils.check_foreign_audio()
		except:
			source_utils.scraper_error('NYYAA')
			self.scrape_failed = True
			return sources

		for url in urls:
			try:
				results = client.request(url, timeout=self.timeout)
				if not results: raise ValueError('Empty provider response')
				results = re.sub(r'[\n\t]', '', results)
				tbody = client.parseDOM(results, 'tbody')
				if not tbody: raise ValueError('Invalid provider response')
				rows = client.parseDOM(tbody, 'tr')

				for row in rows:
					links = zip(
									re.findall(r'href\s*=\s*["\'](magnet:[^"\']+)["\']', row, re.DOTALL | re.I),
									re.findall(r'((?:\d+\,\d+\.\d+|\d+\.\d+|\d+\,\d+|\d+)\s*(?:GB|GiB|Gb|MB|MiB|Mb))', row, re.DOTALL),
									[re.findall(r'<td class\s*=\s*["\']text-center["\']>([0-9]+)</td>', row, re.DOTALL)])
					for link in links:
						url = unquote_plus(link[0]).replace('&amp;', '&').split('&tr')[0].replace(' ', '.')
						url = source_utils.strip_non_ascii_and_unprintable(url)
						hash = re.search(r'btih:(.*?)&', url, re.I).group(1)
						name = source_utils.clean_name(url.split('&dn=')[1])

						matching_name = name
						if 'tvshowtitle' in data:
							# Nyaa also uses names such as "S1 - 34", whose spaces clean_name turns into dots.
							marker = r'(?<![a-z0-9])(?:%s|S0*%d[.]*-[.]*0*%d)(?!\d)' % (re.escape(hdlr), int(data['season']), int(data['episode']))
							match = re.search(marker, name, re.I)
							if not match: continue
							suffix = name[match.end():]
							if ALTERNATE_EP_RANGE_REGEX.match(suffix): continue
							matching_name = name[:match.start()] + hdlr + name[match.end():]
							if not source_utils.check_title(title, aliases, name[:match.start()] + hdlr, hdlr, year): continue
						elif hdlr not in name: continue
						if source_utils.remove_lang(name, check_foreign_audio): continue
						name_info = source_utils.info_from_name(matching_name, title, year, hdlr, episode_title)
						# if undesirables and source_utils.remove_undesirables(name_info, undesirables): continue
						try:
							seeders = int(link[2][0])
							if self.min_seeders > seeders: continue
						except: seeders = 0

						quality, info = source_utils.get_release_quality(name, url)
						try:
							size = link[1]
							dsize, isize = source_utils._size(size)
							info.insert(0, isize)
						except: dsize = 0
						info = ' | '.join(info)

						sources_append({'provider': 'nyaa', 'source': 'torrent', 'seeders': seeders, 'hash': hash, 'name': name, 'quality': quality,
										'language': 'en', 'url': url, 'info': info, 'direct': False, 'debridonly': True, 'size': dsize, 'name_info': name_info})
			except:
				source_utils.scraper_error('NYAA')
				self.scrape_failed = True
		return sources
