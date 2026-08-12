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

	def test_movie_context_and_direct_release_preserve_positional_contract(self):
		context = self.helper.request_context({'title': 'Law & Order/Special Victims Unit', 'aliases': ['Alt'], 'year': '2024', 'imdb': 'tt1'})
		self.helper.add_filter_settings(context)

		name_info = self.helper.direct_release(context, 'Law.and.Order.2024')

		self.assertEqual(context['title'], 'Law and Order SVU')
		self.assertEqual(context['hdlr'], '2024')
		self.assertEqual(name_info, 'info:direct')
		self.assertEqual(self.source_utils.info_calls[-1], (('Law.and.Order.2024', 'Law and Order SVU', '2024', '2024', None), {}))

	def test_episode_context_and_direct_release_preserve_episode_contract(self):
		context = self.helper.request_context({
			'tvshowtitle': 'Show', 'title': 'Episode Name', 'aliases': [], 'year': '2024', 'imdb': 'tt2', 'season': '1', 'episode': '2'
		})
		self.helper.add_filter_settings(context)

		self.helper.direct_release(context, 'Show.S01E02')

		self.assertEqual(context['hdlr'], 'S01E02')
		self.assertEqual(self.source_utils.info_calls[-1], (('Show.S01E02', 'Show', '2024', 'S01E02', 'Episode Name'), {}))

	def test_season_pack_preserves_partial_episode_range_and_filter_name(self):
		context = self.helper.pack_context({'tvshowtitle': 'Show', 'title': 'Episode', 'aliases': [], 'year': '2024', 'imdb': 'tt2', 'season': '1'})
		self.helper.add_filter_settings(context)

		release = self.helper.pack_release(context, 'Show.(Archie.Bunker.S01', filter_name='Show.S01')

		self.assertEqual((release['package'], release['episode_start'], release['episode_end']), ('season', 2, 8))
		self.assertEqual(self.source_utils.season_calls[-1][-1], 'Show.S01')
		self.assertEqual(self.source_utils.info_calls[-1][1], {'season': '1', 'pack': 'season'})

	def test_show_pack_and_bypass_preserve_last_season(self):
		context = self.helper.pack_context({'tvshowtitle': 'Show', 'title': 'Episode', 'aliases': [], 'year': '2024', 'imdb': 'tt2', 'season': '1'})
		self.helper.add_filter_settings(context)

		filtered = self.helper.pack_release(context, 'Show.Complete', search_series=True, total_seasons='5')
		bypassed = self.helper.pack_release(context, 'Show.Complete', search_series=True, total_seasons='5', bypass_filter=True)

		self.assertEqual(filtered['last_season'], 4)
		self.assertEqual(bypassed['last_season'], '5')
		self.assertEqual(len(self.source_utils.show_calls), 1)

	def test_rejected_direct_and_pack_releases_return_none(self):
		context = self.helper.pack_context({'tvshowtitle': 'Show', 'title': 'Episode', 'aliases': [], 'year': '2024', 'imdb': 'tt2', 'season': '1'})
		self.helper.add_filter_settings(context)
		self.source_utils.season_result = (False, 0, 0)
		self.assertIsNone(self.helper.pack_release(context, 'Wrong.Season'))

		direct = self.helper.request_context({'title': 'Movie', 'aliases': [], 'year': '2024', 'imdb': 'tt1'})
		self.helper.add_filter_settings(direct)
		self.source_utils.direct_valid = False
		self.assertIsNone(self.helper.direct_release(direct, 'Wrong.Movie'))

	def test_result_builder_preserves_direct_season_and_show_shapes(self):
		direct = self.helper.build_result('dmm', 'hash', 'Movie', 'info', '1080p', '2 GB | WEB', 2.0)
		season_release = {'package': 'season', 'last_season': None, 'episode_start': 2, 'episode_end': 8}
		season = self.helper.build_result('torz', 'hash', 'Show.S01', 'info', '1080p', '10 GB | WEB', 10.0, 20, season_release)
		show_release = {'package': 'show', 'last_season': 4, 'episode_start': 0, 'episode_end': 0}
		show = self.helper.build_result('torz', 'hash', 'Show.Complete', 'info', '1080p', '40 GB | WEB', 40.0, 30, show_release)

		self.assertNotIn('package', direct)
		self.assertEqual((season['package'], season['episode_start'], season['episode_end']), ('season', 2, 8))
		self.assertEqual((show['package'], show['last_season']), ('show', 4))

	def test_dmm_and_torz_preserve_direct_movie_and_episode_results(self):
		movie = {'title': 'Movie', 'aliases': [], 'year': '2024', 'imdb': 'tt1'}
		episode = {'tvshowtitle': 'Show', 'title': 'Episode', 'aliases': [], 'year': '2024', 'imdb': 'tt2', 'season': '1', 'episode': '2'}
		provider_records = {
			'dmm': {'hash': 'dmm-hash', 'title': 'Release.Name', 'fileSize': 2},
			'torz': {'hash': 'torz-hash', 'name': 'Release.Name', 'size': 2000000000, 'seeders': 12}
		}
		for provider_name, record in provider_records.items():
			for data in (movie, episode):
				with self.subTest(provider=provider_name, mediatype='episode' if 'tvshowtitle' in data else 'movie'):
					module = load_provider(provider_name, self.source_utils)
					provider = module.source()
					provider._get_files = lambda url, result=record: [result]
					item = provider.sources(data, {})[0]
					self.assertEqual((item['provider'], item['name_info'], item['quality']), (provider_name, 'info:direct', '1080p'))
					self.assertEqual(item['seeders'], 12 if provider_name == 'torz' else 0)

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
