import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'resources' / 'lib'))

from modules.source_search import EXTERNAL_PROVIDERS


class ProviderManagerTests(unittest.TestCase):
	def run_manager(self, selection):
		writes, notifications = [], []
		enabled = {'comet', 'torrentio'}

		def get_setting(key, fallback=None):
			provider = key.removeprefix('provider.external.').removesuffix('.enabled')
			return 'true' if provider in enabled else 'false'

		kodi_utils = types.ModuleType('modules.kodi_utils')
		kodi_utils.local_string = lambda value: str(value)
		kodi_utils.get_setting = get_setting
		kodi_utils.logger = lambda *args: None
		kodi_utils.select_dialog = lambda function_list, **kwargs: selection
		kodi_utils.set_setting = lambda key, value: writes.append((key, value))
		kodi_utils.notification = lambda *args: notifications.append(args)
		previous = sys.modules.get('modules.kodi_utils')
		sys.modules['modules.kodi_utils'] = kodi_utils
		try:
			path = ROOT / 'resources' / 'lib' / 'modules' / 'utils.py'
			spec = importlib.util.spec_from_file_location('test_provider_manager_runtime', path)
			module = importlib.util.module_from_spec(spec)
			spec.loader.exec_module(module)
			module.toggle_provider()
		finally:
			if previous is None: sys.modules.pop('modules.kodi_utils', None)
			else: sys.modules['modules.kodi_utils'] = previous
		return writes, notifications

	def test_provider_selection_is_persisted(self):
		writes, notifications = self.run_manager(['comet', 'torrentio'])
		self.assertEqual(len(writes), len(EXTERNAL_PROVIDERS))
		self.assertEqual(dict(writes)['provider.external.comet.enabled'], 'true')
		self.assertEqual(dict(writes)['provider.external.dmm.enabled'], 'false')
		self.assertEqual(notifications, [(32576, 1500)])

	def test_cancel_does_not_change_settings(self):
		writes, notifications = self.run_manager(None)
		self.assertEqual(writes, [])
		self.assertEqual(notifications, [])

	def test_empty_selection_disables_all_providers(self):
		writes, notifications = self.run_manager([])
		self.assertEqual(set(dict(writes).values()), {'false'})
		self.assertEqual(notifications, [(32576, 1500)])


if __name__ == '__main__':
	unittest.main()
