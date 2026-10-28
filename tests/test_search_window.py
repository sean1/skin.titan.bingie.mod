import types
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


class Window:
	def __init__(self, *args):
		self.properties = {}
		self.controls = {key: Mock() for key in (200, 500)}
		for control in self.controls.values(): control.getSelectedPosition.return_value = 0
		self.focus = 200

	def setProperty(self, key, value): self.properties[key] = value
	def getControl(self, control): return self.controls[control]
	def getFocusId(self): return self.focus
	def setFocusId(self, control): self.focus = control


def load_search():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.make_listitem = Mock(side_effect=Mock)
	kodi_utils.media_path = lambda value: value
	kodi_utils.logger = Mock()
	kodi_utils.window_xml_closing_actions = (9, 10, 92)
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	tmdb_api = types.ModuleType('indexers.tmdb_api')
	tmdb_api.tmdb_media_search = Mock()
	tmdb_api.tmdb_image_base = 'https://image.tmdb.org/t/p/%s%s'
	xbmcgui = types.ModuleType('xbmcgui')
	xbmcgui.WindowXML = Window
	xbmcgui.getCurrentWindowId = lambda: 10000
	xbmcgui.ACTION_SHOW_INFO = 11
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils, 'indexers': types.ModuleType('indexers'), 'indexers.tmdb_api': tmdb_api, 'xbmcgui': xbmcgui}
	return load_module('test_search_window_module', ROOT / 'resources/lib/windows/search.py', stubs)


def payload(*items, pages=1): return {'results': list(items), 'total_pages': pages}


MOVIE = {'media_type': 'movie', 'id': 42, 'title': 'Fargo', 'release_date': '1996-03-08'}
SHOW = {'media_type': 'tv', 'id': 42, 'name': 'Fargo', 'first_air_date': '2014-04-15'}


class SearchWindowTests(unittest.TestCase):
	def setUp(self):
		self.module = load_search()
		self.clock = 100.0
		self.module.monotonic = lambda: self.clock
		self.module.ThreadPoolExecutor = Mock()
		self.module.Thread = Mock()
		self.window = self.module.Search('search.xml', 'skin')
		self.executor = self.window.executor

	def complete(self, value):
		self.window.future.set_result(value)
		self.window.tick()

	def request(self, query):
		self.window.set_query(query)
		self.clock += 1
		self.executor.submit.return_value = Future()
		self.window.tick()

	def test_mixed_results_keep_relevance_order_and_distinct_media_with_same_id(self):
		data = payload(SHOW, {'media_type': 'person', 'id': 10}, MOVIE, SHOW, None, {'media_type': 'movie'})
		self.assertEqual(self.module.media_results(data), [SHOW, MOVIE])

	def test_typing_coalesces_queries_after_pause(self):
		self.window.set_query('f')
		self.window.tick()
		self.clock += 0.2
		self.window.set_query('fa')
		self.clock += 0.2
		self.window.tick()
		self.executor.submit.assert_not_called()
		self.clock += 0.2
		self.window.tick()
		self.executor.submit.assert_called_once_with(self.module.tmdb_media_search, 'fa', 1)

	def test_old_response_cannot_replace_new_query_and_only_one_request_runs_at_once(self):
		self.request('fargo')
		first = self.window.future
		self.window.set_query('alien')
		self.clock += 1
		self.window.tick()
		self.assertEqual(self.executor.submit.call_count, 1)
		first.set_result(payload(MOVIE))
		self.executor.submit.return_value = Future()
		self.window.tick()
		self.assertEqual(self.window.items, [])
		self.executor.submit.assert_called_with(self.module.tmdb_media_search, 'alien', 1)
		alien = {'media_type': 'movie', 'id': 348, 'title': 'Alien'}
		self.complete(payload(alien))
		self.assertEqual(self.window.items, [alien])

	def test_clear_discards_inflight_results_without_an_empty_request(self):
		self.request('fargo')
		self.window.set_query('')
		self.complete(payload(MOVIE))
		self.assertEqual(self.window.items, [])
		self.assertEqual(self.executor.submit.call_count, 1)
		self.assertEqual(self.window.properties['Search.Pending'], 'false')

	def test_page_two_appends_without_duplicates_and_keeps_movie_show_identity(self):
		self.request('fargo')
		self.complete(payload(MOVIE, pages=2))
		self.executor.submit.return_value = Future()
		self.window.onClick(501)
		self.window.tick()
		self.executor.submit.assert_called_with(self.module.tmdb_media_search, 'fargo', 2)
		self.complete(payload(MOVIE, SHOW, pages=2))
		self.assertEqual(self.window.items, [MOVIE, SHOW])
		self.assertEqual(self.window.properties['Search.HasMore'], 'false')

	def test_failure_does_not_claim_no_matches_or_retry_forever(self):
		self.request('fargo')
		self.complete(None)
		self.assertIn('Search unavailable', self.window.status)
		self.window.tick()
		self.assertEqual(self.executor.submit.call_count, 1)
		self.executor.submit.return_value = Future()
		self.window.onClick(501)
		self.window.tick()
		self.executor.submit.assert_called_with(self.module.tmdb_media_search, 'fargo', 1)
		self.complete(payload(SHOW))
		self.assertEqual(self.window.items, [SHOW])


	def test_failed_next_page_retries_that_page_and_preserves_existing_results(self):
		self.request('fargo')
		self.complete(payload(MOVIE, pages=3))
		self.executor.submit.return_value = Future()
		self.window.onClick(501)
		self.window.tick()
		self.complete(None)
		self.assertEqual(self.window.items, [MOVIE])
		self.executor.submit.return_value = Future()
		self.window.onClick(501)
		self.window.tick()
		self.executor.submit.assert_called_with(self.module.tmdb_media_search, 'fargo', 2)


if __name__ == '__main__': unittest.main()
