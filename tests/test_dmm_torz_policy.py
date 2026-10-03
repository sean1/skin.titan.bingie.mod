import sys
import types
import unittest
from pathlib import Path

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'resources' / 'lib'
HELPER = LIB / 'magneto' / 'common' / 'provider_utils.py'
sys.path.insert(0, str(LIB))


class FakeSourceUtils:
	def __init__(self):
		self.direct_valid = True
		self.season_result = (True, 2, 8)
		self.show_result = (True, 4)
		self.remove_language = False
		self.remove_unwanted = False
		self.info_calls = []
		self.season_calls = []
		self.show_calls = []
		self.convert_calls = []

	def aliases_to_array(self, aliases): return ['alias:%s' % item for item in aliases]
	def get_undesirables(self): return ['blocked']
	def check_foreign_audio(self): return True
	def check_title(self, *args): return self.direct_valid
	def filter_season_pack(self, *args):
		self.season_calls.append(args)
		return self.season_result
	def filter_show_pack(self, *args):
		self.show_calls.append(args)
		return self.show_result
	def info_from_name(self, *args, **kwargs):
		self.info_calls.append((args, kwargs))
		return 'info:%s' % (kwargs.get('pack') or 'direct')
	def remove_lang(self, *args): return self.remove_language
	def remove_undesirables(self, *args): return self.remove_unwanted
	def clean_name(self, name): return name
	def get_release_quality(self, *args): return '1080p', ['WEB']
	def convert_size(self, size, **kwargs):
		self.convert_calls.append((size, kwargs))
		return float(size), 'SIZE'
	def scraper_error(self, provider): raise AssertionError('%s provider failed' % provider)


def load_helper(fake_utils):
	fenom = types.ModuleType('fenom')
	fenom.source_utils = fake_utils
	return load_module('test_dmm_torz_provider_utils', HELPER, {'fenom': fenom})


def load_provider(name, fake_utils):
	fenom = types.ModuleType('fenom')
	fenom.client = types.SimpleNamespace(request=lambda *args, **kwargs: None)
	fenom.source_utils = fake_utils
	magneto = types.ModuleType('magneto')
	magneto.__path__ = [str(LIB / 'magneto')]
	path = LIB / 'magneto' / ('%s.py' % name)
	return load_module('test_policy_%s' % name, path, {'fenom': fenom, 'magneto': magneto}, ('magneto.common', 'magneto.common.provider_utils'))


class DmmTorzPolicyTests(unittest.TestCase):
	def setUp(self):
		self.source_utils = FakeSourceUtils()
		self.helper = load_helper(self.source_utils)

	def test_rejected_direct_and_pack_releases_return_none(self):
		context = self.helper.pack_context({'tvshowtitle': 'Show', 'title': 'Episode', 'aliases': [], 'year': '2024', 'imdb': 'tt2', 'season': '1'})
		self.helper.add_filter_settings(context)
		self.source_utils.season_result = (False, 0, 0)
		self.assertIsNone(self.helper.pack_release(context, 'Wrong.Season'))

		direct = self.helper.request_context({'title': 'Movie', 'aliases': [], 'year': '2024', 'imdb': 'tt1'})
		self.helper.add_filter_settings(direct)
		self.source_utils.direct_valid = False
		self.assertIsNone(self.helper.direct_release(direct, 'Wrong.Movie'))

	def test_dmm_and_torz_preserve_season_show_and_rejection_results(self):
		data = {'tvshowtitle': 'Show', 'title': 'Episode', 'aliases': [], 'year': '2024', 'imdb': 'tt2', 'season': '1', 'episode': '2'}
		provider_records = {
			'dmm': {'hash': 'dmm-hash', 'title': 'Show.S01', 'fileSize': 2},
			'torz': {'hash': 'torz-hash', 'name': 'Show.S01', 'size': 2000000000, 'seeders': 12}
		}
		for provider_name, record in provider_records.items():
			with self.subTest(provider=provider_name):
				module = load_provider(provider_name, self.source_utils)
				provider = module.source()
				provider._get_files = lambda url, result=record: [result]
				season = provider.sources_packs(data, {})[0]
				show = provider.sources_packs(data, {}, search_series=True, total_seasons='5')[0]
				self.assertEqual((season['package'], season['episode_start'], season['episode_end']), ('season', 2, 8))
				self.assertEqual((show['package'], show['last_season']), ('show', 4))

				self.source_utils.season_result = (False, 0, 0)
				self.assertEqual(provider.sources_packs(data, {}), [])
				self.source_utils.season_result = (True, 2, 8)


if __name__ == '__main__':
	unittest.main()
