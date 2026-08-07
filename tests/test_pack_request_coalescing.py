import importlib.util
import json
import sys
import threading
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'resources' / 'lib'))

from modules.source_search import RequestCoalescer


class FakeClient:
	def __init__(self, response_builder):
		self.calls = []
		self.response_builder = response_builder
		self.started = threading.Event()
		self.release = threading.Event()

	def request(self, url, timeout=None):
		self.calls.append((url, timeout))
		self.started.set()
		self.release.wait(1)
		return self.response_builder(url)


def load_provider(name, client):
	fenom = types.ModuleType('fenom')
	fenom.client = client
	fenom.source_utils = types.SimpleNamespace(scraper_error=lambda provider: None)
	old_fenom = sys.modules.get('fenom')
	sys.modules['fenom'] = fenom
	try:
		path = ROOT / 'resources' / 'lib' / 'magneto' / f'{name}.py'
		spec = importlib.util.spec_from_file_location(f'test_{name}_provider', path)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
	finally:
		if old_fenom is None: sys.modules.pop('fenom', None)
		else: sys.modules['fenom'] = old_fenom
	return module


class PackRequestCoalescingTests(unittest.TestCase):
	def run_shared_request(self, name, response_builder, expected):
		client = FakeClient(response_builder)
		module = load_provider(name, client)
		module.source._requests = RequestCoalescer()
		if name == 'dmm': module.get_secret = lambda: ('key', 'solution')
		results = []
		url = 'https://provider.invalid/title'
		threads = [threading.Thread(target=lambda: results.append(module.source()._get_files(url))) for _ in range(3)]
		for thread in threads: thread.start()
		self.assertTrue(client.started.wait(1))
		client.release.set()
		for thread in threads: thread.join(1)
		self.assertEqual(len(client.calls), 1)
		self.assertEqual(results, [expected] * 3)

	def test_dmm_shares_one_request_across_pack_instances(self):
		expected = [{'hash': 'dmm'}]
		self.run_shared_request('dmm', lambda url: json.dumps({'results': expected}), expected)

	def test_torz_shares_one_request_across_pack_instances(self):
		expected = [{'hash': 'torz'}]
		self.run_shared_request('torz', lambda url: json.dumps({'data': {'items': expected}}), expected)

	def test_torz_does_not_mix_episode_urls(self):
		client = FakeClient(lambda url: json.dumps({'data': {'items': [{'hash': url.rsplit('/', 1)[-1]}]}}))
		client.release.set()
		module = load_provider('torz', client)
		module.source._requests = RequestCoalescer()
		one = module.source()._get_files('https://provider.invalid/one')
		two = module.source()._get_files('https://provider.invalid/two')
		self.assertEqual(one, [{'hash': 'one'}])
		self.assertEqual(two, [{'hash': 'two'}])
		self.assertEqual(len(client.calls), 2)


if __name__ == '__main__':
	unittest.main()
