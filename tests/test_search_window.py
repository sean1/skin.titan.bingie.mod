import types
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Event
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


	def test_new_query_starts_while_old_request_runs_and_ignores_old_response(self):
		self.request('fargo')
		first = self.window.future
		first.set_running_or_notify_cancel()
		self.window.set_query('alien')
		self.clock += 1
		self.executor.submit.return_value = Future()
		self.window.tick()
		self.assertEqual(self.executor.submit.call_count, 2)
		self.executor.submit.assert_called_with(self.module.tmdb_media_search, 'alien', 1)
		self.window.tick()
		self.assertEqual(self.window.items, [])
		alien = {'media_type': 'movie', 'id': 348, 'title': 'Alien'}
		self.complete(payload(alien))
		first.set_result(payload(MOVIE))
		self.window.tick()
		self.assertEqual(self.window.items, [alien])

	def test_changing_query_clears_selectable_results_immediately(self):
		self.request('fargo')
		self.complete(payload(MOVIE))
		self.assertEqual(self.window.items, [MOVIE])
		results = self.window.getControl(500)
		results.reset_mock()
		self.window.set_query('alien')
		self.assertEqual(self.window.items, [])
		results.reset.assert_called_once()
		results.addItems.assert_called_once_with([])
		self.assertEqual(self.window.properties['Search.Pending'], 'true')
		self.window.onClick(500)
		self.assertEqual(self.window.focus_id, 200)

	def test_clearing_query_ignores_late_response(self):
		self.request('fargo')
		old_request = self.window.future
		old_request.set_running_or_notify_cancel()
		self.window.set_query('')
		old_request.set_result(payload(MOVIE))
		self.window.tick()
		self.assertEqual(self.window.items, [])
		self.assertEqual(self.window.properties['Search.Pending'], 'false')
		self.assertEqual(self.executor.submit.call_count, 1)

	def test_queued_obsolete_query_is_cancelled_before_workers_free(self):
		self.module.ThreadPoolExecutor = ThreadPoolExecutor
		started = {query: Event() for query in ('a', 'b', 'c', 'd')}
		release = Event()
		def search(query, page):
			started[query].set()
			if query in ('a', 'b') and not release.wait(5): raise RuntimeError('Timed out waiting to release search')
			return payload({'media_type': 'movie', 'id': ord(query), 'title': query})
		self.module.tmdb_media_search = search
		window = self.module.Search('search.xml', 'skin')
		def request(query):
			window.set_query(query)
			self.clock += 1
			window.tick()
		try:
			request('a')
			self.assertTrue(started['a'].wait(2))
			request('b')
			self.assertTrue(started['b'].wait(2))
			request('c')
			queued = window.future
			request('d')
			self.assertTrue(queued.cancelled())
			release.set()
			self.assertTrue(started['d'].wait(2))
			window.future.result(timeout=2)
			window.tick()
			self.assertFalse(started['c'].is_set())
			self.assertEqual([item['title'] for item in window.items], ['d'])
		finally:
			release.set()
			window.executor.shutdown(wait=True, cancel_futures=True)


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


if __name__ == '__main__': unittest.main()
