import json
from datetime import date, timedelta

from indexers import tmdb_api
from modules import kodi_utils, meta_lists


PROPERTY_PREFIX = 'Refine.'
STATE_PROPERTY = 'Bingie.Refine.Draft.%s'
APPLIED_STATE_PROPERTY = 'Bingie.Refine.Applied.%s'
DEFAULT_DRAFT = {
	'preset_origin': 'None', 'sort': 'popularity', 'sort_label': 'Popularity', 'order': 'desc', 'order_label': 'Descending',
	'genres': '', 'genres_label': '', 'year_start': '', 'year_end': '', 'release_window': '', 'rating': '', 'votes': '', 'max_votes': '', 'released_only': '', 'language': '', 'language_label': '', 'mpaa': '',
	'network': '', 'network_label': '', 'theme': '', 'theme_label': ''
}
SORT_OPTIONS = {
	'movie': (('Popularity', 'popularity'), ('Release date', 'primary_release_date'), ('Revenue', 'revenue'), ('Title', 'original_title'), ('Rating', 'vote_average')),
	'tvshow': (('Popularity', 'popularity'), ('First air date', 'first_air_date'), ('Title', 'original_name'), ('Rating', 'vote_average'))
}
DISPLAY_PROPERTIES = ('Preset', 'Sort', 'Order', 'Genres', 'Theme', 'Year', 'ReleaseWindow', 'Rating', 'Votes', 'MaxVotes', 'Released', 'Language', 'MPAA', 'Network', 'Count', 'Changed', 'Eligible', 'Type')
THEME_OPTIONS = (
	('Any', ''), ('Time Travel | Time Loop', '4379|10854'), ('Post-Apocalyptic Future | End of the World | Apocalypse | Climate Apocalypse | Robot Apocalypse', '4458|10150|12332|355070|298669'),
	('Survival', '10349'), ('Zombie | Zombie Apocalypse', '12377|186565'), ('Space Exploration | Space Travel | Spaceship | Spacecraft', '191132|3801|252937|1612'),
	('Artificial Intelligence | Virtual Reality', '310|4563'), ('Ancient Rome | Ancient Egypt', '5049|157894'), ('Pirate | Pirate Ship', '12988|185200'),
	('Treasure Hunt | Quest', '6956|207372'), ('Ghost | Haunted House', '162846|3358'), ('Monster | Giant Monster | Alien Invasion', '1299|11100|14909'),
	('Vampire | Werewolf', '3133|12564'), ('Slasher | Serial Killer', '12339|10714'), ('Psychological Thriller | Mind Game | Mind Games', '12565|184312|226106'),
	('Heist | Bank Robbery', '10051|15363'), ('Spy | Espionage | Secret Agent', '470|5265|4289'), ('Organized Crime | Mafia | Gangster', '10291|10391|3149'),
	('Conspiracy', '10410'), ('Dystopia | Cyberpunk', '4565|12190'), ('Whodunit', '12570'), ('Soldier | Military | Army | Special Forces', '13065|162365|6092|15218'),
	('Dark Comedy | Satire | Parody', '10123|8201|9755'), ('Stand-Up Comedy', '9716'), ('Workplace Comedy | Workplace Romance | Office Romance', '210605|212796|182325')
)
PRESET_FIELDS = ('sort', 'order', 'rating', 'votes', 'max_votes', 'released_only', 'release_window')
PRESET_RECIPES = {
	'None': {'sort': 'popularity', 'order': 'desc', 'rating': '', 'votes': '', 'max_votes': '', 'released_only': '', 'release_window': '', 'genres': '', 'mpaa': ''},
	'Top Rated': {'sort': 'vote_average', 'order': 'desc', 'rating': '7.0', 'votes': '500', 'max_votes': '', 'released_only': '', 'release_window': '', 'genres': '', 'mpaa': ''},
	'Crowd Favorites': {'sort': 'popularity', 'order': 'desc', 'rating': '7.0', 'votes': '1000', 'max_votes': '', 'released_only': '', 'release_window': '', 'genres': '', 'mpaa': ''},
	'New & Noteworthy': {'sort': 'release_date', 'order': 'desc', 'rating': '6.5', 'votes': '50', 'max_votes': '', 'released_only': 'true', 'release_window': '', 'genres': '', 'mpaa': ''},
	'New Releases': {'sort': 'release_date', 'order': 'desc', 'rating': '', 'votes': '1', 'max_votes': '', 'released_only': '', 'release_window': '90', 'genres': '', 'mpaa': ''},
	'Hidden Gems': {'sort': 'vote_average', 'order': 'desc', 'rating': '7.0', 'votes': '50', 'max_votes': '500', 'released_only': '', 'release_window': '', 'genres': '', 'mpaa': ''},
	'Family Night': {'sort': 'popularity', 'order': 'desc', 'rating': '6.5', 'votes': '100', 'max_votes': '', 'released_only': '', 'release_window': '', 'genres': '10751', 'mpaa': 'G|PG'}
}


class Refine:
	def __init__(self, params):
		self.params = params
		self.mediatype = self._mediatype(params.get('mediatype'))
		self.draft = self._load_draft()

	def initialize(self):
		self.draft = self._load_state(APPLIED_STATE_PROPERTY)
		return self._save()

	def preset(self):
		choice = self._select('Preset', tuple((name, name) for name in PRESET_RECIPES))
		if choice is None: return
		name = choice[1]
		previous = self.draft['preset_origin']
		recipe = dict(PRESET_RECIPES[choice[1]])
		if self.mediatype == 'tvshow':
			if recipe['sort'] == 'release_date': recipe['sort'] = 'first_air_date'
			recipe['mpaa'] = ''
		elif recipe['sort'] == 'release_date': recipe['sort'] = 'primary_release_date'
		self.draft.update({key: recipe[key] for key in PRESET_FIELDS})
		if recipe['release_window']:
			self.draft['year_start'], self.draft['year_end'] = '', ''
		if name in ('None', 'Family Night') or previous == 'Family Night':
			self.draft['genres'], self.draft['genres_label'] = recipe['genres'], 'Family' if recipe['genres'] else ''
			self.draft['mpaa'] = recipe['mpaa']
		self.draft['preset_origin'] = name
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
		choice = self._multiselect('Genres', options, preselect, allow_empty=True)
		if choice is None: return
		self.draft['genres_label'] = ', '.join(item[0] for item in choice)
		self.draft['genres'] = ','.join(item[1] for item in choice)
		return self._save()

	def theme(self):
		choice = self._select('Theme', THEME_OPTIONS)
		if choice is None: return
		self.draft['theme_label'], self.draft['theme'] = choice if choice[1] else ('', '')
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
		if start or end: self.draft['release_window'] = ''
		return self._save()

	def release_window(self):
		choice = self._select('Release window', (('Any', ''), ('Last 30 days', '30'), ('Last 90 days', '90'), ('Last 180 days', '180')))
		if choice is None: return
		self.draft['release_window'] = choice[1]
		if choice[1]: self.draft['year_start'], self.draft['year_end'] = '', ''
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

	def max_votes(self):
		values = ('', '50', '100', '250', '500', '1000')
		choice = self._select('Maximum votes', [('Any' if not value else value, value) for value in values])
		if choice is None: return
		self.draft['max_votes'] = choice[1]
		return self._save()

	def released(self):
		choice = self._select('Released titles only', (('No', ''), ('Yes', 'true')))
		if choice is None: return
		self.draft['released_only'] = choice[1]
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
		options = [(value, value) for value in meta_lists.movie_certifications]
		selected = self.draft['mpaa'].split('|') if self.draft['mpaa'] else []
		preselect = [index for index, item in enumerate(options) if item[1] in selected]
		choice = self._multiselect('MPAA ratings', options, preselect, allow_empty=True)
		if choice is None: return
		self.draft['mpaa'] = '|'.join(item[1] for item in choice)
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
		self._publish()
		url_mediatype = 'movie' if self.mediatype == 'movie' else 'tv'
		query = '%s/discover/%s?language=en-US&page=%%s&include_adult=false' % (tmdb_api.base_url, url_mediatype)
		query += '&sort_by=%s.%s' % (self.draft['sort'], self.draft['order'])
		if self.draft['genres']: query += '&with_genres=%s' % self.draft['genres']
		if self.draft['theme']: query += '&with_keywords=%s' % self.draft['theme']
		date_key = 'primary_release_date' if self.mediatype == 'movie' else 'first_air_date'
		if self.draft['year_start']: query += '&%s.gte=%s-01-01' % (date_key, self.draft['year_start'])
		if self.draft['year_end']: query += '&%s.lte=%s-12-31' % (date_key, self.draft['year_end'])
		if self.draft['release_window']:
			today = date.today()
			query += '&%s.gte=%s&%s.lte=%s' % (date_key, (today - timedelta(days=int(self.draft['release_window']))).isoformat(), date_key, today.isoformat())
		if self.draft['rating']: query += '&vote_average.gte=%s' % self.draft['rating']
		if self.draft['votes']: query += '&vote_count.gte=%s' % self.draft['votes']
		if self.draft['max_votes']: query += '&vote_count.lte=%s' % self.draft['max_votes']
		if self.draft['released_only'] and not self.draft['release_window']: query += '&%s.lte=%s' % (date_key, date.today().isoformat())
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
		if value: self.draft['release_window'] = ''
		return self._save()

	def _select(self, heading, options):
		items = [{'line1': item[0], 'icon': kodi_utils.media_path('discover.png')} for item in options]
		value = kodi_utils.select_dialog([item[1] for item in options], items=json.dumps(items), heading=heading, multi_line='false')
		if value is None: return None
		return next(item for item in options if item[1] == value)

	def _multiselect(self, heading, options, preselect, allow_empty=False):
		items = [{'line1': item[0], 'icon': kodi_utils.media_path('discover.png')} for item in options]
		values = kodi_utils.select_dialog(options, items=json.dumps(items), heading=heading, multi_line='false', multi_choice='true', preselect=preselect, allow_empty='true' if allow_empty else 'false')
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
		self._sync_owned_labels()
		kodi_utils.set_property(STATE_PROPERTY % self.mediatype, json.dumps(self.draft, sort_keys=True))
		return self._publish()

	def _publish(self):
		values = {
			'Preset': self._preset_label(), 'Sort': self.draft['sort_label'], 'Order': self.draft['order_label'], 'Genres': self.draft['genres_label'] or 'Any', 'Theme': self.draft['theme_label'] or 'Any',
			'Year': self._year_label(), 'ReleaseWindow': 'Last %s days' % self.draft['release_window'] if self.draft['release_window'] else 'Any', 'Rating': self.draft['rating'] or 'Any', 'Votes': self.draft['votes'] or 'Any', 'MaxVotes': self.draft['max_votes'] or 'Any', 'Released': 'Yes' if self.draft['released_only'] else 'No',
			'Language': self.draft['language_label'] or 'Any', 'MPAA': self.draft['mpaa'].replace('|', ', ') if self.mediatype == 'movie' and self.draft['mpaa'] else 'Any',
			'Network': self.draft['network_label'] if self.mediatype == 'tvshow' and self.draft['network_label'] else 'Any',
			'Count': str(self._active_count()), 'Changed': 'true' if self._is_changed() else 'false', 'Eligible': 'true', 'Type': self.mediatype
		}
		for key in DISPLAY_PROPERTIES: kodi_utils.set_property(PROPERTY_PREFIX + key, values[key])
		return values

	def _preset_label(self):
		values = {key: self.draft[key] for key in PRESET_FIELDS}
		for name, source_recipe in PRESET_RECIPES.items():
			recipe = dict(source_recipe)
			if self.mediatype == 'tvshow':
				if recipe['sort'] == 'release_date': recipe['sort'] = 'first_air_date'
				recipe['mpaa'] = ''
			elif recipe['sort'] == 'release_date': recipe['sort'] = 'primary_release_date'
			if values != {key: recipe[key] for key in PRESET_FIELDS}: continue
			if name == 'Family Night' and (self.draft['genres'] != recipe['genres'] or self.draft['mpaa'] != recipe['mpaa']): continue
			return name
		return 'Custom'

	def _sync_owned_labels(self):
		self.draft['sort_label'] = next((label for label, value in SORT_OPTIONS[self.mediatype] if value == self.draft['sort']), self.draft['sort_label'])
		self.draft['order_label'] = {'asc': 'Ascending', 'desc': 'Descending'}.get(self.draft['order'], self.draft['order_label'])

	def _year_label(self):
		start, end = self.draft['year_start'], self.draft['year_end']
		if start and end: return start if start == end else '%s–%s' % (start, end)
		if start: return '%s+' % start
		if end: return 'Through %s' % end
		return 'Any'

	def _active_count(self):
		count = sum(bool(self.draft[key]) for key in ('genres', 'theme', 'release_window', 'rating', 'votes', 'max_votes', 'released_only', 'language'))
		if self.mediatype == 'movie' and self.draft['mpaa']: count += 1
		if self.mediatype == 'tvshow' and self.draft['network']: count += 1
		if self.draft['year_start'] or self.draft['year_end']: count += 1
		if self.draft['sort'] != DEFAULT_DRAFT['sort'] or self.draft['order'] != DEFAULT_DRAFT['order']: count += 1
		return count

	def _is_changed(self):
		return self.draft != self._load_state(APPLIED_STATE_PROPERTY)
