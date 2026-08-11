import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_people():
	class WindowPropertyCache:
		def __init__(self, *args): pass
		def get(self, *args): return None
		def set(self, *args): pass

	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.get_kodi_version = lambda: 21
	kodi_utils.local_string = str
	kodi_utils.build_url = lambda params: 'plugin://test'
	kodi_utils.make_listitem = lambda: None
	kodi_utils.get_addoninfo = lambda key: ''
	kodi_utils.media_path = lambda name: name
	settings = types.ModuleType('modules.settings')
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	modules.settings = settings
	window_property_cache = types.ModuleType('caches.window_property_cache')
	window_property_cache.WindowPropertyCache = WindowPropertyCache
	caches = types.ModuleType('caches')
	tmdb_api = types.ModuleType('indexers.tmdb_api')
	tmdb_api.tmdb_people_info = lambda *args: []
	tmdb_api.tmdb_people_actor_info = lambda *args: {}
	tmdb_api.tmdb_image_base = '%s/%s'
	tmdb_api.resized_tmdb_image = lambda *args: ''
	indexers = types.ModuleType('indexers')
	images = types.ModuleType('menus.images')
	images.Images = object
	menus = types.ModuleType('menus')
	utils = types.ModuleType('modules.utils')
	utils.calculate_age = lambda *args: 0
	utils.valid_tmdb_id = lambda value: bool(value)
	stubs = {
		'caches': caches, 'caches.window_property_cache': window_property_cache, 'indexers': indexers, 'indexers.tmdb_api': tmdb_api,
		'menus': menus, 'menus.images': images, 'modules': modules, 'modules.kodi_utils': kodi_utils, 'modules.settings': settings, 'modules.utils': utils
	}
	previous = {name: sys.modules.get(name) for name in stubs}
	sys.modules.update(stubs)
	try:
		path = ROOT / 'resources' / 'lib' / 'menus' / 'people.py'
		spec = importlib.util.spec_from_file_location('test_actor_focus_people', path)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
	finally:
		for name, old_module in previous.items():
			if old_module is None: sys.modules.pop(name, None)
			else: sys.modules[name] = old_module
	return module


class ActorFocusTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.people = load_people()

	def setUp(self):
		self.properties = {'PovActorId': '1245'}
		self.commands = []
		self.people.kodi_utils.get_property = lambda key: self.properties.get(key, '')
		self.people.kodi_utils.get_visibility = lambda condition: True
		self.people.kodi_utils.execute_builtin = self.commands.append
		self.people.kodi_utils.monitor = types.SimpleNamespace(abortRequested=lambda: False)
		self.people.kodi_utils.sleep = lambda milliseconds: None
		self.properties['PovActorReady'] = 'true'
		original_monotonic = self.people.monotonic
		self.addCleanup(setattr, self.people, 'monotonic', original_monotonic)

	def test_build_person_credits_focuses_movies_after_directory_commit(self):
		events = []
		self.properties['PovActorHasMovies'] = 'true'
		self.people.kodi_utils.argv1 = lambda: '7'
		self.people.kodi_utils.add_items = lambda handle, items: events.append(('items', handle, items))
		self.people.kodi_utils.set_content = lambda handle, content: events.append(('content', handle, content))
		self.people.kodi_utils.end_directory = lambda handle, cacheToDisc=False: events.append(('end', handle, cacheToDisc))
		self.people.kodi_utils.execute_builtin = lambda command: events.append(('builtin', command))
		self.people.settings.get_resolution = lambda: {'poster': 'w342', 'fanart': 'w1280'}
		self.people._load_actor_credits = lambda actor_id, credit_type: [{'id': 1}]
		self.people._credit_listitem = lambda item, resolution, actor_id: 'movie-item'

		self.people.build_person_credits({'actor_id': '1245', 'credit_type': 'movies'})

		self.assertEqual(events, [
			('items', 7, ['movie-item']), ('content', 7, 'videos'), ('end', 7, False), ('builtin', 'SetFocus(610)')
		])

	def test_focus_actor_page_immediately_focuses_cached_shelf(self):
		conditions = []
		self.people.kodi_utils.get_visibility = lambda condition: conditions.append(condition) or True

		self.people._focus_actor_page({'movies': [{}]})

		self.assertEqual(self.commands, [
			'AlarmClock(PovActorFocus,SetFocus(610),00:00:01,silent,loop)', 'SetFocus(610)'
		])
		self.assertEqual(conditions, [
			'Window.IsActive(1122)',
			'Window.IsActive(1122) + Control.HasFocus(600) + String.IsEqual(Container(610).ListItemAbsolute(0).Property(PovActorSourceId),Window(Home).Property(PovActorId))'
		])

	def test_focus_actor_page_keeps_alarm_when_shelf_is_not_populated(self):
		visibility = iter((True, False))
		self.people.kodi_utils.get_visibility = lambda condition: next(visibility)
		times = iter((0.0, 0.25))
		self.people.monotonic = lambda: next(times)

		self.people._focus_actor_page({'movies': [{}]})

		self.assertEqual(self.commands, ['AlarmClock(PovActorFocus,SetFocus(610),00:00:01,silent,loop)'])

	def test_focus_actor_page_waits_for_cold_shelf_publication(self):
		visibility = iter((True, False, True))
		self.people.kodi_utils.get_visibility = lambda condition: next(visibility)
		self.people.monotonic = lambda: 0.0
		waits = []
		self.people.kodi_utils.sleep = waits.append

		self.people._focus_actor_page({'movies': [{}]})

		self.assertEqual(waits, [25])
		self.assertEqual(self.commands, [
			'AlarmClock(PovActorFocus,SetFocus(610),00:00:01,silent,loop)', 'SetFocus(610)'
		])

	def test_focus_loaded_actor_shelf_uses_first_available_shelf(self):
		cases = (
			({'PovActorHasMovies': 'true', 'PovActorHasTVShows': 'true', 'PovActorHasDirected': 'true'}, 'movies', 'SetFocus(610)'),
			({'PovActorHasMovies': 'false', 'PovActorHasTVShows': 'true', 'PovActorHasDirected': 'true'}, 'tvshows', 'SetFocus(620)'),
			({'PovActorHasMovies': 'false', 'PovActorHasTVShows': 'false', 'PovActorHasDirected': 'true'}, 'directed', 'SetFocus(630)')
		)
		for properties, credit_type, expected in cases:
			with self.subTest(credit_type=credit_type):
				self.properties.update(properties)
				self.commands.clear()
				self.people._focus_loaded_actor_shelf('1245', credit_type, True)
				self.assertEqual(self.commands, [expected])

	def test_focus_loaded_actor_shelf_returns_before_actor_is_ready(self):
		self.properties.update({'PovActorReady': 'false', 'PovActorHasMovies': 'true'})

		self.people._focus_loaded_actor_shelf('1245', 'movies', True)

		self.assertEqual(self.commands, [])

	def test_focus_loaded_actor_shelf_requires_current_container_item(self):
		self.properties['PovActorHasMovies'] = 'true'
		conditions = []
		self.people.kodi_utils.get_visibility = lambda condition: conditions.append(condition) or False

		self.people._focus_loaded_actor_shelf('1245', 'movies', True)

		self.assertEqual(self.commands, [])
		self.assertEqual(conditions, [
			'Window.IsActive(1122) + Control.HasFocus(600) + String.IsEqual(Container(610).ListItemAbsolute(0).Property(PovActorSourceId),Window(Home).Property(PovActorId))'
		])

	def test_focus_loaded_actor_shelf_does_not_steal_focus(self):
		self.properties.update({'PovActorHasMovies': 'true', 'PovActorHasTVShows': 'true'})
		cases = (
			('empty shelf', '1245', 'movies', False, True),
			('stale actor', '9999', 'movies', True, True),
			('lower priority shelf', '1245', 'tvshows', True, True),
			('user moved focus', '1245', 'movies', True, False)
		)
		for name, actor_id, credit_type, has_items, visibility in cases:
			with self.subTest(name=name):
				self.commands.clear()
				self.people.kodi_utils.get_visibility = lambda condition, result=visibility: result
				self.people._focus_loaded_actor_shelf(actor_id, credit_type, has_items)
				self.assertEqual(self.commands, [])


if __name__ == '__main__':
	unittest.main()
