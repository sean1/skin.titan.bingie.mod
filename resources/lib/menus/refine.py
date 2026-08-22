import json

from indexers import tmdb_api
from modules import kodi_utils, meta_lists


PROPERTY_PREFIX = 'Refine.'
STATE_PROPERTY = 'Bingie.Refine.Draft.%s'
APPLIED_STATE_PROPERTY = 'Bingie.Refine.Applied.%s'
DEFAULT_DRAFT = {
	'sort': 'popularity', 'sort_label': 'Popularity', 'order': 'desc', 'order_label': 'Descending',
	'genres': '', 'genres_label': '', 'year_start': '', 'year_end': '', 'rating': '', 'votes': '', 'language': '', 'language_label': '', 'mpaa': '',
	'network': '', 'network_label': ''
}
SORT_OPTIONS = {
	'movie': (('Popularity', 'popularity'), ('Release date', 'primary_release_date'), ('Revenue', 'revenue'), ('Title', 'original_title'), ('Rating', 'vote_average')),
	'tvshow': (('Popularity', 'popularity'), ('First air date', 'first_air_date'), ('Title', 'original_name'), ('Rating', 'vote_average'))
}
DISPLAY_PROPERTIES = ('Sort', 'Order', 'Genres', 'Year', 'Rating', 'Votes', 'Language', 'MPAA', 'Network', 'Count', 'Eligible', 'Type')


class Refine:
	def __init__(self, params):
		self.params = params
		self.mediatype = self._mediatype(params.get('mediatype'))
		self.draft = self._load_draft()

	def initialize(self):
		self.draft = self._load_state(APPLIED_STATE_PROPERTY)
		return self._save()

	def sort(self):
		options = SORT_OPTIONS[self.mediatype]
		choice = self._select('Sort by', options)
		if choice is None: return
		self.draft['sort_label'], self.draft['sort'] = choice
		return self._save()

	def order(self):
		choice = self._select('Order', (('Ascending', 'asc'), ('Descending', 'desc')))
		if choice is None: return
		self.draft['order_label'], self.draft['order'] = choice
		return self._save()

	def genres(self):
		genres = meta_lists.movie_genres if self.mediatype == 'movie' else meta_lists.tvshow_genres
		options = [(key, str(value[0])) for key, value in sorted(genres.items())]
		selected_ids = self.draft['genres'].split(',') if self.draft['genres'] else []
		preselect = [index for index, item in enumerate(options) if item[1] in selected_ids]
		choice = self._multiselect('Genres', options, preselect)
		if choice is None: return
		self.draft['genres_label'] = ', '.join(item[0] for item in choice)
		self.draft['genres'] = ','.join(item[1] for item in choice)
		return self._save()

	def year_start(self):
		return self._year('year_start', 'From year')

	def year_end(self):
		return self._year('year_end', 'To year')

	def year(self):
		start = kodi_utils.dialog.numeric(0, 'From year', self.draft['year_start'])
		if start is None: return
		start = str(start).strip()
		if start and (not start.isdigit() or len(start) != 4): return kodi_utils.notification('Enter a four-digit year')
		end = kodi_utils.dialog.numeric(0, 'To year', self.draft['year_end'])
		if end is None: return
		end = str(end).strip()
		if end and (not end.isdigit() or len(end) != 4): return kodi_utils.notification('Enter a four-digit year')
		if start and end and int(start) > int(end): return kodi_utils.notification('From year must not be later than to year')
		self.draft['year_start'], self.draft['year_end'] = start, end
		return self._save()

	def rating(self):
		options = [('Any', '')] + [('%.1f' % (value / 2.0), '%.1f' % (value / 2.0)) for value in range(1, 21)]
		choice = self._select('Minimum rating', options)
		if choice is None: return
		self.draft['rating'] = choice[1]
		return self._save()

	def votes(self):
		values = ('', '1', '50', '100', '250', '500', '1000')
		choice = self._select('Minimum votes', [('Any' if not value else value, value) for value in values])
		if choice is None: return
		self.draft['votes'] = choice[1]
		return self._save()

	def language(self):
		options = [('Any', '')] + sorted(((name, value['iso']) for name, value in meta_lists.meta_languages.items()), key=lambda item: item[0])
		choice = self._select('Original language', options)
		if choice is None: return
		self.draft['language_label'], self.draft['language'] = choice
		return self._save()

	def mpaa(self):
		if self.mediatype != 'movie':
			self.draft['mpaa'] = ''
			return self._save()
		options = [('Any', '')] + [(value, value) for value in meta_lists.movie_certifications]
		choice = self._select('MPAA rating', options)
		if choice is None: return
		self.draft['mpaa'] = choice[1]
		return self._save()

	def network(self):
		if self.mediatype != 'tvshow':
			self.draft['network'], self.draft['network_label'] = '', ''
			return self._save()
		options = [('Any', '')] + [(item['name'], str(item['id'])) for item in sorted(meta_lists.networks, key=lambda item: item['name'])]
		choice = self._select('Original network', options)
		if choice is None: return
		self.draft['network_label'], self.draft['network'] = choice if choice[1] else ('', '')
		return self._save()

	def clear(self):
		self.draft = dict(DEFAULT_DRAFT)
		return self._save()

	def apply(self):
		self._save()
		kodi_utils.set_property(APPLIED_STATE_PROPERTY % self.mediatype, json.dumps(self.draft, sort_keys=True))
		url_mediatype = 'movie' if self.mediatype == 'movie' else 'tv'
		query = '%s/discover/%s?language=en-US&page=%%s&include_adult=false' % (tmdb_api.base_url, url_mediatype)
		query += '&sort_by=%s.%s' % (self.draft['sort'], self.draft['order'])
		if self.draft['genres']: query += '&with_genres=%s' % self.draft['genres']
		date_key = 'primary_release_date' if self.mediatype == 'movie' else 'first_air_date'
		if self.draft['year_start']: query += '&%s.gte=%s-01-01' % (date_key, self.draft['year_start'])
		if self.draft['year_end']: query += '&%s.lte=%s-12-31' % (date_key, self.draft['year_end'])
		if self.draft['rating']: query += '&vote_average.gte=%s' % self.draft['rating']
		if self.draft['votes']: query += '&vote_count.gte=%s' % self.draft['votes']
		if self.draft['language']: query += '&with_original_language=%s' % self.draft['language']
		if self.mediatype == 'movie' and self.draft['mpaa']: query += '&certification_country=US&certification=%s' % self.draft['mpaa']
		if self.mediatype == 'tvshow' and self.draft['network']: query += '&with_networks=%s' % self.draft['network']
		mode, action, name = ('build_movie_list', 'tmdb_movies_discover', 'Refined Movies') if self.mediatype == 'movie' else ('build_tvshow_list', 'tmdb_tv_discover', 'Refined TV Shows')
		url = kodi_utils.build_url({'mode': mode, 'action': action, 'query': query, 'name': name, 'iconImage': 'discover.png'})
		return kodi_utils.execute_builtin('ActivateWindow(Videos,%s,return)' % url)

	def show_results(self):
		return self.apply()

	def _year(self, key, heading):
		value = kodi_utils.dialog.numeric(0, heading, self.draft[key])
		if value is None: return
		value = str(value).strip()
		if value and (not value.isdigit() or len(value) != 4): return kodi_utils.notification('Enter a four-digit year')
		self.draft[key] = value
		return self._save()

	def _select(self, heading, options):
		items = [{'line1': item[0], 'icon': kodi_utils.media_path('discover.png')} for item in options]
		value = kodi_utils.select_dialog([item[1] for item in options], items=json.dumps(items), heading=heading, multi_line='false')
		if value is None: return None
		return next(item for item in options if item[1] == value)

	def _multiselect(self, heading, options, preselect):
		items = [{'line1': item[0], 'icon': kodi_utils.media_path('discover.png')} for item in options]
		values = kodi_utils.select_dialog(options, items=json.dumps(items), heading=heading, multi_line='false', multi_choice='true', preselect=preselect)
		return values

	def _mediatype(self, value):
		if value in ('movie', 'tvshow'): return value
		content = kodi_utils.get_infolabel('Container.Content').lower()
		return 'tvshow' if content in ('tvshows', 'seasons', 'episodes') else 'movie'

	def _load_draft(self):
		return self._load_state(STATE_PROPERTY)

	def _load_state(self, property_name):
		try: draft = json.loads(kodi_utils.get_property(property_name % self.mediatype))
		except (TypeError, ValueError): draft = {}
		result = dict(DEFAULT_DRAFT)
		result.update({key: value for key, value in draft.items() if key in result})
		return result

	def _save(self):
		kodi_utils.set_property(STATE_PROPERTY % self.mediatype, json.dumps(self.draft, sort_keys=True))
		return self._publish()

	def _publish(self):
		values = {
			'Sort': self.draft['sort_label'], 'Order': self.draft['order_label'], 'Genres': self.draft['genres_label'] or 'Any',
			'Year': self._year_label(), 'Rating': self.draft['rating'] or 'Any', 'Votes': self.draft['votes'] or 'Any',
			'Language': self.draft['language_label'] or 'Any', 'MPAA': self.draft['mpaa'] if self.mediatype == 'movie' and self.draft['mpaa'] else 'Any',
			'Network': self.draft['network_label'] if self.mediatype == 'tvshow' and self.draft['network_label'] else 'Any',
			'Count': str(self._active_count()), 'Eligible': 'true', 'Type': self.mediatype
		}
		for key in DISPLAY_PROPERTIES: kodi_utils.set_property(PROPERTY_PREFIX + key, values[key])
		return values

	def _year_label(self):
		start, end = self.draft['year_start'], self.draft['year_end']
		if start and end: return start if start == end else '%s–%s' % (start, end)
		if start: return '%s+' % start
		if end: return 'Through %s' % end
		return 'Any'

	def _active_count(self):
		count = sum(bool(self.draft[key]) for key in ('genres', 'rating', 'votes', 'language'))
		if self.mediatype == 'movie' and self.draft['mpaa']: count += 1
		if self.mediatype == 'tvshow' and self.draft['network']: count += 1
		if self.draft['year_start'] or self.draft['year_end']: count += 1
		if self.draft['sort'] != DEFAULT_DRAFT['sort'] or self.draft['order'] != DEFAULT_DRAFT['order']: count += 1
		return count
