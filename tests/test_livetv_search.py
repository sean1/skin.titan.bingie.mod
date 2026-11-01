import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_livetv import load_livetv

ROOT = Path(__file__).resolve().parents[1]


class LiveTVSearchTests(unittest.TestCase):
	def setUp(self):
		self.live = load_livetv()
		(ROOT / 'tmp').mkdir(exist_ok=True)
		self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
		self.addCleanup(self.temp.cleanup)
		self.live.ku.profile_path = self.temp.name
		self.live.ku.addon_path = self.temp.name
		self.live._save({'server': 'http://example.test', 'username': 'user', 'password': 'private'})


	def test_search_matches_channel_names_case_insensitively_and_orders_live_first(self):
		streams = [{'name': 'End | Liverpool vs Old', 'stream_id': 1}, {'name': 'Live | Liverpool vs Chelsea', 'stream_id': 2}, {'name': 'TSN 1', 'stream_id': 3, 'epg_channel_id': 'liverpool'}]
		with patch.object(self.live, '_catalog', return_value=streams), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish') as finish:
			self.live.search_results({'query': 'LiVeRpOoL'})
		calls = item.call_args_list
		self.assertEqual(len(calls), 2)
		self.assertEqual([call.args[1] for call in calls], [{'mode': 'livetv.play', 'id': '2'}, {'mode': 'livetv.play', 'id': '1'}])
		self.assertNotIn('private', str(calls))
		finish.assert_called_once_with()

	def test_search_groups_matching_live_event_feeds(self):
		streams = [{'name': 'Live | Liverpool vs Chelsea | Soccer', 'stream_id': 1}, {'name': 'Live | Liverpool vs Chelsea | Soccer', 'stream_id': 2}]
		with patch.object(self.live, '_catalog', return_value=streams), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.search_results({'query': 'chelsea'})
		item.assert_called_once_with('Liverpool vs Chelsea — 2 feeds', {'mode': 'livetv.match_feeds', 'home': 'liverpool', 'away': 'chelsea', 'scope': ''}, True, None)

	def test_no_channel_name_match_does_not_search_programmes(self):
		streams = [{'name': 'TSN 1', 'stream_id': 3, 'epg_channel_id': 'liverpool'}]
		with patch.object(self.live, '_catalog', return_value=streams), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'), patch.object(self.live.ku.dialog, 'notification') as notification:
			self.live.search_results({'query': 'liverpool'})
		item.assert_not_called()
		notification.assert_called_once_with('Live TV', 'No matches found.')


if __name__ == '__main__': unittest.main()
