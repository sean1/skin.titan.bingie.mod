import importlib.util
import json
import os
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

	def test_fresh_cache_avoids_network_and_keeps_required_fields(self):
		fetch = Mock(return_value=self.data)
		self.assertEqual(catalog.get(self.account, 'get_live_streams', fetch, self.profile, now=1000), self.data)
		self.assertEqual(catalog.get(self.account, 'get_live_streams', fetch, self.profile, now=1010), self.data)
		self.assertEqual(fetch.call_count, 1)
		path = next(Path(self.profile).glob('livetv-catalog-*.json'))
		self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
		self.assertNotIn(self.account['username'], path.read_text())
		self.assertNotIn(self.account['password'], path.read_text())

	def test_expired_cache_refreshes_and_failed_refresh_uses_bounded_stale(self):
		fetch = Mock(side_effect=[self.data, OSError('private'), [{'stream_id': 43, 'name': 'new'}]])
		stale = Mock()
		catalog.get(self.account, 'get_live_streams', fetch, self.profile, on_stale=stale, now=1000)
		self.assertEqual(catalog.get(self.account, 'get_live_streams', fetch, self.profile, on_stale=stale, now=1121), self.data)
		stale.assert_called_once_with()
		self.assertEqual(catalog.get(self.account, 'get_live_streams', fetch, self.profile, now=1122)[0]['stream_id'], 43)
		fetch.side_effect = OSError('private')
		with self.assertRaises(OSError): catalog.get(self.account, 'get_live_streams', fetch, self.profile, now=1122 + catalog.STALE_SECONDS)

	def test_account_action_and_category_do_not_share_cache(self):
		fetch = Mock(return_value=self.data)
		catalog.get(self.account, 'get_live_streams', fetch, self.profile, category_id='1', now=1000)
		catalog.get(self.account, 'get_live_streams', fetch, self.profile, category_id='2', now=1000)
		catalog.get({**self.account, 'password': 'other'}, 'get_live_streams', fetch, self.profile, category_id='1', now=1000)
		catalog.get(self.account, 'get_live_categories', fetch, self.profile, now=1000)
		self.assertEqual(fetch.call_count, 4)

	def test_credential_and_signed_icon_data_is_not_persisted(self):
		data = [{'stream_id': 1, 'name': 'Safe name', 'stream_icon': 'https://cdn.test/logo?token=' + self.account['password'], 'private_payload': self.account}]
		fetch = Mock(return_value=data)
		catalog.get(self.account, 'get_live_streams', fetch, self.profile, now=1000)
		stored = next(Path(self.profile).glob('livetv-catalog-*.json')).read_text()
		self.assertNotIn(self.account['password'], stored)
		self.assertNotIn(self.account['username'], stored)
		self.assertNotIn('private_payload', stored)
		self.assertNotIn('stream_icon', stored)

	def test_matching_credential_in_name_does_not_change_name_or_persist(self):
		data = [{'stream_id': 1, 'name': 'Name ' + self.account['password']}]
		fetch = Mock(return_value=data)
		self.assertEqual(catalog.get(self.account, 'get_live_streams', fetch, self.profile, now=1000), data)
		self.assertEqual(catalog.get(self.account, 'get_live_streams', fetch, self.profile, now=1001), data)
		self.assertEqual(fetch.call_count, 2)
		self.assertFalse(list(Path(self.profile).glob('livetv-catalog-*.json')))

	def test_benign_image_query_is_preserved_but_secret_query_is_not_cached(self):
		data = [
			{'stream_id': 1, 'name': 'Alpha', 'stream_icon': 'https://cdn.test/logo.png?v=17'},
			{'stream_id': 2, 'name': 'Beta', 'stream_icon': 'https://cdn.test/logo.png?access_token=abc'},
			{'stream_id': 3, 'name': 'Gamma', 'stream_icon': 'https://cdn.test/logo.png?jwt=abc'},
			{'stream_id': 4, 'name': 'Delta', 'stream_icon': 'https://cdn.test/logo.png?Policy=abc&Key-Pair-Id=xyz'},
			{'stream_id': 5, 'name': 'Epsilon', 'stream_icon': 'https://cdn.test/logo.png?key=abc'},
		]
		fetch = Mock(return_value=data)
		catalog.get(self.account, 'get_live_streams', fetch, self.profile, now=1000)
		cached = catalog.get(self.account, 'get_live_streams', fetch, self.profile, now=1001)
		self.assertEqual(cached[0]['stream_icon'], data[0]['stream_icon'])
		for item in cached[1:]: self.assertNotIn('stream_icon', item)
		self.assertNotIn('access_token', next(Path(self.profile).glob('livetv-catalog-*.json')).read_text())

	def test_invalid_cache_is_not_used_and_bad_server_response_is_rejected(self):
		path = Path(self.profile) / ('livetv-catalog-' + catalog._key(self.account, 'get_live_streams', {}) + '.json')
		path.write_text('{invalid')
		with self.assertRaises(ValueError): catalog.get(self.account, 'get_live_streams', Mock(return_value={}), self.profile, now=1000)
		self.assertEqual(catalog.get(self.account, 'get_live_streams', Mock(return_value=self.data), self.profile, now=1001), self.data)

	def test_identifier_matching_credential_is_never_changed_or_cached(self):
		account = {**self.account, 'username': '42'}
		fetch = Mock(return_value=self.data)
		self.assertEqual(catalog.get(account, 'get_live_streams', fetch, self.profile, now=1000), self.data)
		self.assertEqual(catalog.get(account, 'get_live_streams', fetch, self.profile, now=1001), self.data)
		self.assertEqual(fetch.call_count, 2)
		self.assertFalse(list(Path(self.profile).glob('livetv-catalog-*.json')))

	def test_unwritable_cache_does_not_block_live_data(self):
		profile = Path(self.profile) / 'not-a-directory'
		profile.write_text('x')
		self.assertEqual(catalog.get(self.account, 'get_live_streams', Mock(return_value=self.data), str(profile), now=1000), self.data)

	def test_cache_count_is_bounded(self):
		fetch = Mock(return_value=[])
		for i in range(catalog.MAX_FILES + 3): catalog.get(self.account, 'get_live_streams', fetch, self.profile, category_id=str(i), now=1000)
		self.assertLessEqual(len(list(Path(self.profile).glob('livetv-catalog-*.json'))), catalog.MAX_FILES)


if __name__ == '__main__': unittest.main()
