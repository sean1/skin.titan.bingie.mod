import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, call


ROOT = Path(__file__).resolve().parents[1]


def load_prefetch_module():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	previous = {name: sys.modules.get(name) for name in ('modules', 'modules.kodi_utils')}
	sys.modules.update({'modules': modules, 'modules.kodi_utils': kodi_utils})
	try:
		path = ROOT / 'resources' / 'lib' / 'modules' / 'prefetch.py'
		spec = importlib.util.spec_from_file_location('test_next_page_prefetch_module', path)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
	finally:
		for name, old_module in previous.items():
			if old_module is None: sys.modules.pop(name, None)
			else: sys.modules[name] = old_module
	return module


class NextPagePrefetchTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.prefetch = load_prefetch_module()

	def setUp(self):
		self.labels = {'Container.CurrentItem': '1', 'Container.NumItems': '21'}
		self.prefetch.kodi_utils.get_infolabel = Mock(side_effect=self.labels.get)
		self.worker = self.prefetch.NextPagePrefetch()

	def test_near_end_uses_absolute_item_in_grid_views(self):
		self.labels.update({'Container.CurrentItem': '21', 'Container.Position': '5'})

		self.assertTrue(self.worker._near_end())
		self.assertEqual(self.prefetch.kodi_utils.get_infolabel.call_args_list, [call('Container.CurrentItem'), call('Container.NumItems')])

	def test_near_end_preserves_five_item_boundary(self):
		for current_item, num_items, expected in (('16', '21', False), ('17', '21', True), ('21', '21', True), ('1', '5', True), ('1', '6', False), ('2', '6', True)):
			with self.subTest(current_item=current_item, num_items=num_items):
				self.labels.update({'Container.CurrentItem': current_item, 'Container.NumItems': num_items})
				self.assertEqual(self.worker._near_end(), expected)

	def test_near_end_rejects_missing_or_malformed_labels(self):
		for current_item, num_items in (('', '21'), (None, '21'), ('invalid', '21'), ('0', '21'), ('-1', '21'), ('22', '21'), ('17', ''), ('17', None), ('17', 'invalid'), ('1', '0')):
			with self.subTest(current_item=current_item, num_items=num_items):
				self.labels.update({'Container.CurrentItem': current_item, 'Container.NumItems': num_items})
				self.assertFalse(self.worker._near_end())

	def test_tick_launches_prefetch_from_grid_near_end(self):
		origin = {'mode': 'build_movie_list', 'action': 'tmdb_movies_popular', 'new_page': '1'}
		request = {'url': 'plugin://next', 'origin': origin, 'not_before': 10.0, 'idle_after': 20.0}
		raw_request = json.dumps(request)
		self.labels.update({'Container.FolderPath': 'plugin://origin', 'Container.CurrentItem': '17', 'Container.Position': '1'})
		self.prefetch.kodi_utils.get_property = Mock(return_value=raw_request)
		self.prefetch.kodi_utils.clear_property = Mock()
		self.prefetch.kodi_utils.parsed_query = Mock(return_value=origin)
		self.prefetch.kodi_utils.get_visibility = Mock(side_effect=lambda condition: condition == 'Window.IsActive(Videos)')
		self.prefetch.kodi_utils.execute_builtin = Mock()
		worker = self.prefetch.NextPagePrefetch(clock=Mock(return_value=12.0))

		self.assertFalse(worker.tick())
		self.prefetch.kodi_utils.execute_builtin.assert_called_once_with('RunPlugin(plugin://next)')
		self.prefetch.kodi_utils.clear_property.assert_called_once_with(self.prefetch.NEXT_PAGE_PREFETCH_PROPERTY)


if __name__ == '__main__':
	unittest.main()
