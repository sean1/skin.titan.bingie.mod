import json
import sys
import types
import unittest
from pathlib import Path

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'resources' / 'lib'
sys.path.insert(0, str(LIB))


class FakeSourceUtils:
	def aliases_to_array(self, aliases): return aliases
	def get_undesirables(self): return []
	def check_foreign_audio(self): return True
	def check_title(self, *args): return False
	def filter_season_pack(self, *args): return True, 1, 10
	def filter_show_pack(self, *args): return False, None
	def info_from_name(self, name, title, year, **kwargs): return 'parsed.%s' % kwargs.get('pack', 'single')
	def remove_lang(self, *args): return False
	def remove_undesirables(self, *args): return False
	def clean_name(self, name): return name
	def get_release_quality(self, name_info, url): return '1080p', ['WEB']
	def _size(self, size): return 1.5, '1.50 GB'
	def convert_size(self, size): return 2.0, '2.00 GB'
	def scraper_error(self, provider): raise AssertionError('%s adapter failed' % provider)


class FakeResponse:
	def __init__(self, payload=None, text=None):
		self.payload = payload
		self.text = text

	def json(self): return self.payload
	def raise_for_status(self): return None


class FakeRequests:
	def __init__(self, response): self.response = response
	def get(self, *args, **kwargs): return self.response


def load_provider(name, fake_utils):
	fenom = types.ModuleType('fenom')
	fenom.source_utils = fake_utils
	fenom.client = types.SimpleNamespace(request=lambda *args, **kwargs: json.dumps({'streams': [{'infoHash': 'hash', 'title': 'Show Season 1\n👤 12 💾 1.5 GB'}]}))
	magneto = types.ModuleType('magneto')
	magneto.__path__ = [str(LIB / 'magneto')]
	path = LIB / 'magneto' / ('%s.py' % name)
	stubs = {'magneto': magneto, 'fenom': fenom}
	isolate = ('magneto.common', 'magneto.common.stremio_utils')
	return load_module('test_stremio_%s' % name, path, stubs, isolate)


class StremioProviderTests(unittest.TestCase):
	data = {'tvshowtitle': 'Show', 'title': 'Episode', 'aliases': [], 'total_seasons': '3', 'year': '2024', 'imdb': 'tt123', 'season': '1', 'episode': '2'}

	def test_bitmagnet_keeps_malformed_seeders_as_zero_without_applying_minimum(self):
		payload = (
			'<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel><item><title>Show Season 1</title>'
			'<torznab:attr name="infohash" value="hash"/><torznab:attr name="seeders" value="12 peers"/>'
			'<torznab:attr name="size" value="2000000000"/></item></channel></rss>'
		)
		module = load_provider('bitmagnet', FakeSourceUtils())
		module.requests = FakeRequests(FakeResponse(text=payload))
		provider = module.source()
		provider.min_seeders = 20

		item = provider.sources(self.data, {})[0]

		self.assertEqual(item['seeders'], 0)

	def test_bitmagnet_accepts_a_legitimate_empty_feed(self):
		module = load_provider('bitmagnet', FakeSourceUtils())
		module.requests = FakeRequests(FakeResponse(text='<rss><channel></channel></rss>'))
		provider = module.source()

		self.assertEqual(provider.sources(self.data, {}), [])
		self.assertFalse(getattr(provider, 'scrape_failed', False))

	def test_bitmagnet_rejects_torznab_errors_and_invalid_xml_envelopes(self):
		for payload in ('<error code="100" description="failure"/>', '<html><body>Unavailable</body></html>', '<rss></rss>'):
			with self.subTest(payload=payload):
				fake_utils = FakeSourceUtils()
				fake_utils.scraper_error = lambda _provider: None
				module = load_provider('bitmagnet', fake_utils)
				module.requests = FakeRequests(FakeResponse(text=payload))
				provider = module.source()

				self.assertEqual(provider.sources(self.data, {}), [])
				self.assertTrue(provider.scrape_failed)


if __name__ == '__main__':
	unittest.main()
