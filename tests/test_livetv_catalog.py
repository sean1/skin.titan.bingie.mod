import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('test_livetv_catalog_module', ROOT / 'resources/lib/modules/livetv_catalog.py')
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


class LiveTVCatalogTests(unittest.TestCase):
	def setUp(self):
		(ROOT / 'tmp').mkdir(exist_ok=True)
		self.directory = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
		self.addCleanup(self.directory.cleanup)
		self.profile = self.directory.name
		self.account = {'server': 'http://provider.example', 'username': 'test-user-secret', 'password': 'test-pass-secret'}
		self.data = [{'stream_id': 42, 'name': 'Live | Alpha vs Beta | SOCCER', 'category_id': '1', 'epg_channel_id': 'alpha', 'stream_icon': 'https://example.test/icon.png'}]


	def test_expired_cache_refreshes_and_failed_refresh_uses_bounded_stale(self):
		fetch = Mock(side_effect=[self.data, OSError('private'), [{'stream_id': 43, 'name': 'new'}]])
		stale = Mock()
		catalog.get(self.account, 'get_live_streams', fetch, self.profile, on_stale=stale, now=1000)
		self.assertEqual(catalog.get(self.account, 'get_live_streams', fetch, self.profile, on_stale=stale, now=1121), self.data)
		stale.assert_called_once_with()
		self.assertEqual(catalog.get(self.account, 'get_live_streams', fetch, self.profile, now=1122)[0]['stream_id'], 43)
		fetch.side_effect = OSError('private')
		with self.assertRaises(OSError): catalog.get(self.account, 'get_live_streams', fetch, self.profile, now=1122 + catalog.STALE_SECONDS)


	def test_credential_and_signed_icon_data_is_not_persisted(self):
		data = [{'stream_id': 1, 'name': 'Safe name', 'stream_icon': 'https://cdn.test/logo?token=' + self.account['password'], 'private_payload': self.account}]
		fetch = Mock(return_value=data)
		catalog.get(self.account, 'get_live_streams', fetch, self.profile, now=1000)
		stored = next(Path(self.profile).glob('livetv-catalog-*.json')).read_text()
		self.assertNotIn(self.account['password'], stored)
		self.assertNotIn(self.account['username'], stored)
		self.assertNotIn('private_payload', stored)
		self.assertNotIn('stream_icon', stored)


	def test_invalid_cache_is_not_used_and_bad_server_response_is_rejected(self):
		path = Path(self.profile) / ('livetv-catalog-' + catalog._key(self.account, 'get_live_streams', {}) + '.json')
		path.write_text('{invalid')
		with self.assertRaises(ValueError): catalog.get(self.account, 'get_live_streams', Mock(return_value={}), self.profile, now=1000)
		self.assertEqual(catalog.get(self.account, 'get_live_streams', Mock(return_value=self.data), self.profile, now=1001), self.data)


if __name__ == '__main__': unittest.main()
