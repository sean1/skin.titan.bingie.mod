import sys
import tempfile
import types
import unittest
from pathlib import Path

from tests.module_isolation import load_module, temporary_modules


class ModuleIsolationTests(unittest.TestCase):


	def test_load_removes_transitive_modules_and_restores_sys_path(self):
		package_name = 'test_module_isolation_package'
		child_name = '%s.child' % package_name
		old_package = sys.modules.pop(package_name, None)
		old_child = sys.modules.pop(child_name, None)
		old_path = list(sys.path)
		try:
			with tempfile.TemporaryDirectory() as temp_dir:
				root = Path(temp_dir)
				package = root / package_name
				package.mkdir()
				(package / '__init__.py').write_text('', encoding='utf-8')
				(package / 'child.py').write_text('value = 1\n', encoding='utf-8')
				loader = root / 'loader.py'
				loader.write_text('import sys\nsys.path.insert(0, %r)\nfrom %s import child\n' % (str(root), package_name), encoding='utf-8')

				load_module('test_module_isolation_loader', loader)

				self.assertNotIn(package_name, sys.modules)
				self.assertNotIn(child_name, sys.modules)
				self.assertEqual(sys.path, old_path)
		finally:
			if old_package is not None: sys.modules[package_name] = old_package
			if old_child is not None: sys.modules[child_name] = old_child
			sys.path[:] = old_path



if __name__ == '__main__':
	unittest.main()
