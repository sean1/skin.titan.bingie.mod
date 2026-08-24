import types
import unittest
from pathlib import Path

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_utils_module():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.local_string = str
	kodi_utils.get_setting = lambda key, fallback=None: fallback
	kodi_utils.logger = lambda *args: None
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	return load_module(
		'test_pagination_bounds_module', ROOT / 'resources' / 'lib' / 'modules' / 'utils.py',
		{'modules': modules, 'modules.kodi_utils': kodi_utils}
	)


class PaginationBoundsTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.utils = load_utils_module()

	def test_empty_input_has_one_empty_page(self):
		for page in (-1, 0, 1, 9):
			with self.subTest(page=page):
				self.assertEqual(self.utils.paginate_list([], page, 2), ([], 1))

	def test_zero_negative_and_stale_pages_are_empty_with_real_total(self):
		items = list(range(5))
		for page in (-2, 0, 4):
			with self.subTest(page=page):
				self.assertEqual(self.utils.paginate_list(items, page, 2), ([], 3))

	def test_valid_page_returns_requested_slice_and_real_total(self):
		self.assertEqual(self.utils.paginate_list(list(range(5)), 2, 2), ([2, 3], 3))

	def test_non_numeric_page_is_empty_with_real_total(self):
		self.assertEqual(self.utils.paginate_list(list(range(5)), 'stale', 2), ([], 3))


if __name__ == '__main__':
	unittest.main()
