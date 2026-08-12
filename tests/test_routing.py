import runpy
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module, temporary_modules


ROOT = Path(__file__).resolve().parents[1]
LIB_PATH = ROOT / 'resources' / 'lib'


def load_routing(params):
	modules = types.ModuleType('modules')
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.parsed_query = Mock(return_value=params)
	modules.kodi_utils = kodi_utils
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils}
	return load_module('test_routing_module', LIB_PATH / 'routing.py', stubs)


class RoutingTests(unittest.TestCase):
	def test_pov_route_imports_handler_only_when_dispatched(self):
		params = {'mode': 'play_trailer', 'tmdb_id': '42'}
		routing = load_routing(params)
		self.assertNotIn('modules.trailers', sys.modules)
		play = Mock(return_value='played')
		trailers = types.ModuleType('modules.trailers')
		trailers.play = play
		with temporary_modules({'modules.trailers': trailers}):
			sys_obj = types.SimpleNamespace(argv=['plugin://skin.titan.bingie.lite', '1', '?mode=play_trailer'])
			self.assertEqual(routing.Router().run(sys_obj), 'played')

		routing.kodi_utils.parsed_query.assert_called_once_with(sys_obj.argv[2])
		play.assert_called_once_with(params)

	def test_subtitle_route_lazily_receives_current_sys_object(self):
		routing = load_routing({'action': 'search'})
		self.assertNotIn('subtitle_service', sys.modules)
		run = Mock(return_value='subtitles')
		subtitle_service = types.ModuleType('subtitle_service')
		subtitle_service.run = run
		with temporary_modules({'subtitle_service': subtitle_service}):
			sys_obj = types.SimpleNamespace(argv=['plugin://skin.titan.bingie.lite', '2', '?action=search'])
			self.assertEqual(routing.routing(sys_obj), 'subtitles')

		run.assert_called_once_with(sys_obj)

	def test_launcher_passes_current_sys_module_to_router(self):
		seen = []
		routing = types.ModuleType('routing')

		class Router:
			def run(self, sys_obj):
				seen.append(sys_obj)

		routing.Router = Router
		path_was_present = str(LIB_PATH) in sys.path
		try:
			with temporary_modules({'routing': routing}): runpy.run_path(str(LIB_PATH / 'router.py'), run_name='__main__')
		finally:
			if not path_was_present: sys.path.remove(str(LIB_PATH))

		self.assertEqual(seen, [sys])


if __name__ == '__main__':
	unittest.main()
