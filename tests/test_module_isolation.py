import sys
import tempfile
import types
import unittest
from pathlib import Path

from tests.module_isolation import load_module, temporary_modules


class ModuleIsolationTests(unittest.TestCase):
	def test_restores_existing_and_removes_new_modules(self):
		existing_name, new_name = 'test_module_isolation_existing', 'test_module_isolation_new'
		existing = types.ModuleType(existing_name)
		replacement = types.ModuleType(existing_name)
		new = types.ModuleType(new_name)
		old_existing = sys.modules.get(existing_name)
		old_new = sys.modules.get(new_name)
		sys.modules[existing_name] = existing
		sys.modules.pop(new_name, None)
		try:
			with temporary_modules({existing_name: replacement, new_name: new}):
				self.assertIs(sys.modules[existing_name], replacement)
				self.assertIs(sys.modules[new_name], new)
			self.assertIs(sys.modules[existing_name], existing)
			self.assertNotIn(new_name, sys.modules)
		finally:
			if old_existing is None: sys.modules.pop(existing_name, None)
			else: sys.modules[existing_name] = old_existing
			if old_new is None: sys.modules.pop(new_name, None)
			else: sys.modules[new_name] = old_new

	def test_restores_isolated_module_after_failure(self):
		name = 'test_module_isolation_transitive'
		original = types.ModuleType(name)
		old_module = sys.modules.get(name)
		sys.modules[name] = original
		try:
			with self.assertRaises(RuntimeError):
				with temporary_modules(isolate=(name,)):
					self.assertNotIn(name, sys.modules)
					sys.modules[name] = types.ModuleType(name)
					raise RuntimeError('load failed')
			self.assertIs(sys.modules[name], original)
		finally:
			if old_module is None: sys.modules.pop(name, None)
			else: sys.modules[name] = old_module

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

	def test_dotted_stub_temporarily_replaces_existing_parent_attribute(self):
		parent_name, child_name = 'test_module_isolation_parent', 'test_module_isolation_parent.child'
		parent = types.ModuleType(parent_name)
		original = types.ModuleType(child_name)
		stub = types.ModuleType(child_name)
		parent.child = original
		old_parent = sys.modules.get(parent_name)
		old_child = sys.modules.get(child_name)
		sys.modules[parent_name] = parent
		sys.modules[child_name] = original
		try:
			with temporary_modules({child_name: stub}):
				imported_parent = __import__(parent_name, fromlist=('child',))
				self.assertIs(imported_parent.child, stub)
				self.assertIs(sys.modules[child_name], stub)
			self.assertIs(parent.child, original)
			self.assertIs(sys.modules[child_name], original)
		finally:
			if old_parent is None: sys.modules.pop(parent_name, None)
			else: sys.modules[parent_name] = old_parent
			if old_child is None: sys.modules.pop(child_name, None)
			else: sys.modules[child_name] = old_child


if __name__ == '__main__':
	unittest.main()
