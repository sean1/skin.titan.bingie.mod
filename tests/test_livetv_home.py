import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from tests.test_livetv import load_livetv

ROOT = Path(__file__).resolve().parents[1]


class LiveTVHomeTests(unittest.TestCase):
	def setUp(self):
		self.live = load_livetv()
		(ROOT / 'tmp').mkdir(exist_ok=True)
		self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
		self.addCleanup(self.temp.cleanup)
		self.live.ku.profile_path = self.temp.name
		self.live.ku.addon_path = self.temp.name
		self.account = {'server': 'https://example.test', 'username': 'user123', 'password': 'private-secret'}
		self.live._save(self.account)

	def test_add_deduplicates_and_remove_updates_playable_home_items(self):
		channel = {'id': '926772', 'name': 'VN| VTV1 HD', 'icon': 'https://images.example.test/vtv1.png'}
		with patch.object(self.live.xbmc, 'getCondVisibility', return_value=False):
			self.live.home_action({'action': 'add', **channel})
			self.live.home_action({'action': 'add', **channel})
			with patch.object(self.live, '_item') as item, patch.object(self.live, '_finish') as finish:
				self.live.home_channels({})
			item.assert_called_once_with('VN| VTV1 HD', {'mode': 'livetv.play', 'id': '926772'}, False, channel['icon'])
			finish.assert_called_once_with()
			self.live.home_action({'action': 'remove', **channel})
		self.assertEqual(self.live._home_read(self.account), [])

	def test_selected_channels_are_isolated_between_accounts(self):
		with patch.object(self.live.xbmc, 'getCondVisibility', return_value=False):
			self.live.home_action({'action': 'add', 'id': '7', 'name': 'Channel 7', 'icon': ''})
			self.live._save({'server': 'https://another.example.test', 'username': 'other', 'password': 'different'})
			with patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
				self.live.home_channels({})
			item.assert_not_called()
			self.live._save(self.account)
		self.assertEqual([channel['id'] for channel in self.live._home_read(self.account)], ['7'])

	def test_home_channels_without_account_finishes_without_dialog(self):
		with patch.object(self.live, '_load', return_value=None), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish') as finish, patch.object(self.live, '_error') as error:
			self.live.home_channels({})
		item.assert_not_called()
		finish.assert_called_once_with()
		error.assert_not_called()

	def test_saved_channel_does_not_retain_private_icon_credentials(self):
		channel = {'action': 'add', 'id': '42', 'name': 'Channel 42', 'icon': 'https://example.test/user123/private-secret/logo.png'}
		with patch.object(self.live.xbmc, 'getCondVisibility', return_value=False):
			self.live.home_action(channel)
		path = Path(self.live._home_path(self.account))
		content = path.read_text(encoding='utf-8')
		self.assertNotIn(self.account['username'], content)
		self.assertNotIn(self.account['password'], content)

	def test_playable_channel_offers_add_then_remove_context_action(self):
		params = {'mode': 'livetv.play', 'id': '926772'}
		with patch.object(self.live.ku, 'build_url', side_effect=lambda query: 'plugin://test/?' + urlencode(query), create=True):
			add = self.live._home_context('VN| VTV1 HD', params, '')
			self.assertEqual(add[0], 'Add to Home channels')
			self.assertIn('action=add', add[1])
			with patch.object(self.live.xbmc, 'getCondVisibility', return_value=False):
				self.live.home_action({'action': 'add', 'id': '926772', 'name': 'VN| VTV1 HD', 'icon': ''})
			remove = self.live._home_context('VN| VTV1 HD', params, '')
			self.assertEqual(remove[0], 'Remove from Home channels')
			self.assertIn('action=remove', remove[1])
			self.assertNotIn(self.account['password'], remove[1])


if __name__ == '__main__': unittest.main()
