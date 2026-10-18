from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from threading import RLock, Thread

import xbmcgui
from indexers.tmdb_api import tmdb_media_search, tmdb_image_base
from modules import kodi_utils


KEYBOARD, RESULTS, MORE = 200, 500, 501
SEARCH_DELAY = 0.35
MAX_QUERY_LENGTH = 150


def media_results(payload):
	"""Keep TMDB's cross-media relevance order, excluding people and duplicate cards."""
	if not isinstance(payload, dict) or not isinstance(payload.get('results'), list): raise ValueError('Search response unavailable')
	seen, results = set(), []
	for item in payload['results']:
		if not isinstance(item, dict) or item.get('media_type') not in ('movie', 'tv') or not item.get('id'): continue
		identity = (item['media_type'], item['id'])
		if identity in seen: continue
		seen.add(identity)
		results.append(item)
	return results


class Search(xbmcgui.WindowXML):
	def __init__(self, *args, **kwargs):
		super().__init__(*args)
		self.query = str(kwargs.get('query') or '')[:MAX_QUERY_LENGTH]
		self.generation = 0
		self.ready_at = monotonic() + SEARCH_DELAY
		self.items, self.page, self.total_pages = [], 0, 0
		self.future = None
		self.request = None
		self.load_more = False
		self.failed = False
		self.closed = False
		self.lock = RLock()
		self.worker = None
		self.window_id = None
		self.parent_window_id = xbmcgui.getCurrentWindowId()
		self.initialized = False
		self.focus_id, self.position = KEYBOARD, 0
		self.key_position = 0
		self.status = 'Type a title to find movies and TV shows'
		self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='bingie-search')

	def onInit(self):
		with self.lock: self._initialize()
		if self.worker is None:
			self.worker = Thread(target=self._work, name='bingie-search-updates')
			self.worker.start()

	def _initialize(self):
		self.window_id = xbmcgui.getCurrentWindowId()
		self.initialized = True
		self.setProperty('Search.Query', self.query)
		self.setProperty('Search.Status', self.status)
		self.setProperty('Search.Pending', 'true' if self.query.strip() and not self.page and not self.failed else 'false')
		self.setProperty('Search.HasMore', 'true' if self.failed or self.page < self.total_pages else 'false')
		self.setProperty('Search.MoreLabel', 'Retry search' if self.failed else 'More results')
		keyboard = self.getControl(KEYBOARD)
		keys = []
		for character in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789':
			key = kodi_utils.make_listitem()
			key.setLabel(character)
			keys.append(key)
		keyboard.reset()
		keyboard.addItems(keys)
		keyboard.selectItem(self.key_position)
		self._render_results()
		self.setFocusId(self.focus_id)
		if self.focus_id == RESULTS: self.getControl(RESULTS).selectItem(self.position)

	def run(self):
		try:
			self.doModal()
		finally:
			self.closed = True
			if self.worker is not None: self.worker.join()
			self.executor.shutdown(wait=True, cancel_futures=True)

	def _work(self):
		while not self.closed and not kodi_utils.monitor.abortRequested():
			with self.lock:
				if self.closed: break
				current_window = xbmcgui.getCurrentWindowId()
				if current_window == self.window_id: self.tick()
				elif current_window in (10000, self.parent_window_id):
					self.closed = True
					self.close()
					if current_window != self.parent_window_id: kodi_utils.execute_builtin('ActivateWindow(%s)' % current_window)
					break
			kodi_utils.sleep(50)

	def onClick(self, control_id):
		with self.lock: self._click(control_id)

	def _click(self, control_id):
		if control_id == KEYBOARD: self.set_query(self.query + self.getControl(KEYBOARD).getSelectedItem().getLabel().lower())
		elif control_id == 201: self.set_query(self.query + ' ')
		elif control_id == 202: self.set_query(self.query[:-1])
		elif control_id == 203: self.set_query('')
		elif control_id == 204:
			self.closed = True
			self.close()
		elif control_id == MORE:
			self.load_more = True
			self.ready_at = 0
		elif control_id == RESULTS and self.items:
			from modules.dialogs import show_media_info
			self.position = self.getControl(RESULTS).getSelectedPosition()
			self.key_position = self.getControl(KEYBOARD).getSelectedPosition()
			item = self.items[self.position]
			self.focus_id = RESULTS
			show_media_info({'mediatype': 'movie' if item['media_type'] == 'movie' else 'tvshow', 'tmdb_id': str(item['id'])})

	def onAction(self, action):
		if action.getId() in kodi_utils.window_xml_closing_actions:
			with self.lock:
				self.closed = True
				self.close()
		elif action.getId() == xbmcgui.ACTION_SHOW_INFO and self.getFocusId() == RESULTS: self.onClick(RESULTS)

	def set_query(self, query):
		query = query[:MAX_QUERY_LENGTH]
		if query == self.query: return
		self.query = query
		self.generation += 1
		self.ready_at = monotonic() + SEARCH_DELAY
		self.page, self.total_pages, self.load_more = 0, 0, False
		self.failed = False
		self.setProperty('Search.Query', query)
		self.setProperty('Search.HasMore', 'false')
		self.setProperty('Search.MoreLabel', 'More results')
		self.setProperty('Search.Pending', 'true' if query.strip() else 'false')
		self._status('Searching…' if query.strip() else 'Type a title to find movies and TV shows')
		if not query.strip():
			self.items = []
			self._render_results()

	def tick(self):
		if self.future is not None:
			if not self.future.done(): return
			future, request = self.future, self.request
			self.future = None
			if request[0] == self.generation:
				try: self._accept(future.result(), request[1])
				except Exception as exc:
					kodi_utils.logger('BINGIE search', str(exc))
					self.failed = True
					if request[1] == 1:
						self.items = []
						self._render_results()
					self.setProperty('Search.Pending', 'false')
					self.setProperty('Search.HasMore', 'true')
					self.setProperty('Search.MoreLabel', 'Retry search')
					self._status('Search unavailable. Please try again.')
		if not self.query.strip() or monotonic() < self.ready_at or ((self.page or self.failed) and not self.load_more): return
		page = self.page + 1
		self.load_more = False
		self.request = (self.generation, page)
		self.setProperty('Search.Pending', 'true')
		self._status('Searching…' if page == 1 else 'Loading more results…')
		self.future = self.executor.submit(tmdb_media_search, self.query.strip(), page)

	def _accept(self, payload, page):
		results = media_results(payload)
		total_pages = int(payload.get('total_pages') or 0)
		seen = {(item['media_type'], item['id']) for item in self.items} if page > 1 else set()
		new_items = [item for item in results if (item['media_type'], item['id']) not in seen]
		if page == 1: self.items = new_items
		else: self.items.extend(new_items)
		self.page, self.total_pages, self.failed = page, total_pages, False
		self.setProperty('Search.Pending', 'false')
		self.setProperty('Search.HasMore', 'true' if page < self.total_pages else 'false')
		self.setProperty('Search.MoreLabel', 'More results')
		self._status('%s results · Movies & TV shows' % len(self.items) if self.items else 'No matching movies or TV shows. Try another title.')
		self._render_results()
		if self.getFocusId() == MORE and self.items:
			self.setFocusId(RESULTS)
			self.getControl(RESULTS).selectItem(len(self.items) - len(new_items) if new_items else 0)

	def _status(self, text):
		self.status = text
		self.setProperty('Search.Status', text)

	def _render_results(self):
		control = self.getControl(RESULTS)
		position = max(0, control.getSelectedPosition())
		cards = []
		for item in self.items:
			movie = item['media_type'] == 'movie'
			title = item.get('title' if movie else 'name') or 'Untitled'
			year = (item.get('release_date' if movie else 'first_air_date') or '')[:4]
			card = kodi_utils.make_listitem()
			card.setLabel(title)
			card.setLabel2(' · '.join(value for value in ('Movie' if movie else 'TV show', year) if value))
			poster = tmdb_image_base % ('w342', item['poster_path']) if item.get('poster_path') else kodi_utils.media_path('box_office.png')
			backdrop = tmdb_image_base % ('w780', item['backdrop_path']) if item.get('backdrop_path') else ''
			card.setArt({'poster': poster, 'thumb': poster, 'fanart': backdrop})
			card.setProperty('tmdb_id', str(item['id']))
			tag = card.getVideoInfoTag()
			tag.setTitle(title)
			tag.setMediaType('movie' if movie else 'tvshow')
			tag.setUniqueIDs({'tmdb': str(item['id'])}, 'tmdb')
			tag.setPlot(item.get('overview') or '')
			if year.isdigit(): tag.setYear(int(year))
			cards.append(card)
		control.reset()
		control.addItems(cards)
		if cards: control.selectItem(min(position, len(cards) - 1))


def show_search(params):
	window = Search('search.xml', kodi_utils.skin_location(), 'Default', '1080i', query=params.get('query', ''))
	try: window.run()
	finally: del window
