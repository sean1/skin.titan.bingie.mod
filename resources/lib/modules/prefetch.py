import json
from time import monotonic
from modules import kodi_utils

NEXT_PAGE_PREFETCH_PROPERTY = 'pov_lite_next_page_prefetch'
NEXT_PAGE_PREFETCH_DELAY = 2.0
NEXT_PAGE_PREFETCH_IDLE_DELAY = 10.0
NEXT_PAGE_PREFETCH_IDLE_SECONDS = 10
NEXT_PAGE_PREFETCH_NEAR_END_ITEMS = 5
NEXT_PAGE_PREFETCH_SUMMARY_NEAR_END_ITEMS = 8
NEXT_PAGE_PREFETCH_SUMMARY_ACTION_PREFIXES = ('tmdb_movies_', 'tmdb_tv_')


def schedule_next_page_prefetch(url, origin_params):
	request = {
		'url': url,
		'origin': dict(origin_params),
		'not_before': monotonic() + NEXT_PAGE_PREFETCH_DELAY,
		'idle_after': monotonic() + NEXT_PAGE_PREFETCH_IDLE_DELAY
	}
	kodi_utils.set_property(NEXT_PAGE_PREFETCH_PROPERTY, json.dumps(request, separators=(',', ':')))


class NextPagePrefetch:
	def __init__(self, clock=monotonic):
		self.clock = clock
		self.raw_request = ''
		self.request = None

	def tick(self):
		raw_request = kodi_utils.get_property(NEXT_PAGE_PREFETCH_PROPERTY)
		if raw_request != self.raw_request: self._consume(raw_request)
		if not self.request: return False
		if not self._origin_is_active() or kodi_utils.get_visibility('Player.HasMedia'):
			self._finish()
			return False
		if self.clock() < self.request['not_before']: return True
		if not self._system_is_available(): return True
		if not self._near_end() and (self.clock() < self.request['idle_after'] or not self._system_is_idle()): return True
		url = self.request['url']
		self._finish()
		kodi_utils.execute_builtin('RunPlugin(%s)' % url)
		return False

	def cancel(self):
		self.request, self.raw_request = None, ''
		kodi_utils.clear_property(NEXT_PAGE_PREFETCH_PROPERTY)

	def _consume(self, raw_request):
		self.raw_request, self.request = raw_request, None
		if not raw_request: return
		try:
			request = json.loads(raw_request)
			if not request.get('url') or not isinstance(request.get('origin'), dict): return
			request['not_before'] = float(request['not_before'])
			request['idle_after'] = float(request.get('idle_after', request['not_before']))
			self.request = request
		except: pass

	def _origin_is_active(self):
		if not kodi_utils.get_visibility('Window.IsActive(Videos)'): return False
		current_path = kodi_utils.get_infolabel('Container.FolderPath')
		return kodi_utils.parsed_query(current_path) == self.request['origin']

	def _system_is_available(self):
		if kodi_utils.get_visibility('Container.IsUpdating'): return False
		if kodi_utils.get_visibility('Window.IsVisible(busydialog)'): return False
		if kodi_utils.get_visibility('Window.IsVisible(busydialognocancel)'): return False
		return True

	def _system_is_idle(self):
		return kodi_utils.get_visibility('System.IdleTime(%d)' % NEXT_PAGE_PREFETCH_IDLE_SECONDS)

	def _near_end(self):
		try:
			current_item = int(kodi_utils.get_infolabel('Container.CurrentItem'))
			num_items = int(kodi_utils.get_infolabel('Container.NumItems'))
		except (TypeError, ValueError): return False
		action = self.request.get('origin', {}).get('action', '') if self.request else ''
		is_summary = isinstance(action, str) and action.startswith(NEXT_PAGE_PREFETCH_SUMMARY_ACTION_PREFIXES)
		near_end_items = NEXT_PAGE_PREFETCH_SUMMARY_NEAR_END_ITEMS if is_summary else NEXT_PAGE_PREFETCH_NEAR_END_ITEMS
		return 0 < current_item <= num_items and current_item > num_items - near_end_items

	def _finish(self):
		self.request = None
		if kodi_utils.get_property(NEXT_PAGE_PREFETCH_PROPERTY) == self.raw_request:
			kodi_utils.clear_property(NEXT_PAGE_PREFETCH_PROPERTY)
		self.raw_request = ''
