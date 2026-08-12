import types
import unittest
from pathlib import Path

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


class FakeModuleLoader:
	def __init__(self, name):
		self.name = name

	def find_spec(self, name):
		return types.SimpleNamespace(loader=self)

	def load_module(self, name):
		return types.SimpleNamespace(source='source-%s' % name)


def load_magneto(settings):
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.get_setting = lambda key, fallback=None: settings.get(key, fallback)
	kodi_utils.logger = lambda *args: None
	path = ROOT / 'resources' / 'lib' / 'magneto' / '__init__.py'
	module = load_module('test_magneto_loader', path, {'modules.kodi_utils': kodi_utils})
	module.iter_modules = lambda paths: [
		(FakeModuleLoader('comet'), 'comet', False),
		(FakeModuleLoader('torrentio'), 'torrentio', False),
	]
	return module


class ProviderLoaderTests(unittest.TestCase):
	def test_providers_default_to_enabled(self):
		module = load_magneto({})
		self.assertEqual(module.sources(), [('comet', 'source-comet'), ('torrentio', 'source-torrentio')])

	def test_disabled_provider_is_filtered(self):
		module = load_magneto({'provider.external.comet.enabled': 'false'})
		self.assertEqual(module.sources(), [('torrentio', 'source-torrentio')])

	def test_ret_all_bypasses_disabled_provider_filter(self):
		module = load_magneto({'provider.external.comet.enabled': 'false'})
		self.assertEqual(module.sources(ret_all=True), [('comet', 'source-comet'), ('torrentio', 'source-torrentio')])


if __name__ == '__main__':
	unittest.main()
