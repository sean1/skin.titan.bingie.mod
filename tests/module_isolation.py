import importlib.util
import sys
from contextlib import contextmanager


_MISSING = object()


@contextmanager
def temporary_modules(stubs=None, isolate=()):
	stubs = dict(stubs or {})
	names = tuple(dict.fromkeys((*stubs, *isolate)))
	previous = {name: sys.modules.get(name, _MISSING) for name in names}
	previous_names = set(sys.modules)
	previous_path = list(sys.path)
	for name in isolate: sys.modules.pop(name, None)
	sys.modules.update(stubs)
	parent_attributes = []
	desired_modules = {name: _MISSING for name in isolate if name not in stubs}
	desired_modules.update(stubs)
	for name, module in desired_modules.items():
		parent_name, separator, attribute = name.rpartition('.')
		if not separator or (parent := sys.modules.get(parent_name)) is None: continue
		parent_attributes.append((parent, attribute, getattr(parent, attribute, _MISSING)))
		if module is _MISSING:
			if hasattr(parent, attribute): delattr(parent, attribute)
		else: setattr(parent, attribute, module)
	try: yield
	finally:
		for name in set(sys.modules).difference(previous_names): sys.modules.pop(name, None)
		for parent, attribute, module in reversed(parent_attributes):
			if module is _MISSING:
				if hasattr(parent, attribute): delattr(parent, attribute)
			else: setattr(parent, attribute, module)
		for name, module in previous.items():
			if module is _MISSING: sys.modules.pop(name, None)
			else: sys.modules[name] = module
		sys.path[:] = previous_path


def load_module(module_name, path, stubs=None, isolate=()):
	with temporary_modules(stubs, isolate):
		spec = importlib.util.spec_from_file_location(module_name, path)
		if spec is None or spec.loader is None: raise ImportError('Unable to load %s from %s' % (module_name, path))
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
	return module
