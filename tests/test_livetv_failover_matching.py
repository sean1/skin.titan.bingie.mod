import json
import unittest
from unittest.mock import patch

from tests.test_livetv import load_livetv


class LiveTVFailoverMatchingTests(unittest.TestCase):
	def setUp(self):
		self.live = load_livetv()
		self.account = {'server': 'http://example.test', 'username': 'user', 'password': 'secret'}
		self.name = 'Live | Liverpool vs. Chelsea | all | 8K EXCLUSIVE | CA: SOCCER PPV 17'
		self.live.ku.set_property = lambda *args: None


	def test_same_teams_only_unique_and_bounded_without_credentials(self):
		streams = [{'stream_id': 1, 'name': self.name}, {'stream_id': 2, 'name': self.name.replace('CA:', 'US:')}, {'stream_id': 2, 'name': self.name}, {'stream_id': 3, 'name': self.name.replace('Chelsea', 'Everton')}, {'stream_id': 4, 'name': self.name.replace('Live', 'End')}]
		streams += [{'stream_id': n, 'name': self.name} for n in range(5, 30)]
		with patch.object(self.live, '_catalog', return_value=streams): self.live._prepare_failover(self.account, '1', self.name)
		payload = json.loads(self.live.xbmcgui.Window.return_value.setProperty.call_args.args[1])
		self.assertEqual(payload['id'], '1')
		self.assertEqual(payload['alternatives'], ['2', '5', '6', '7', '8', '9', '10', '11'])
		self.assertNotIn('secret', json.dumps(payload))
		self.assertTrue(payload['token'])

	def test_hockey_failover_uses_fixture_matched_feeds(self):
		name = 'Live | Blackhawks vs Red Wings | NHL | CA: Sports'
		streams = [
			{'stream_id': 1, 'name': name},
			{'stream_id': 2, 'name': 'Live | Blackhawks vs Red Wings | all | US: SOCCER PPV'},
			{'stream_id': 3, 'name': 'Live | Blackhawks vs Penguins | NHL | US: Sports'},
		]
		fixtures = [{'league': 'nhl', 'home': {'name': 'Chicago Blackhawks', 'aliases': ['Chicago Blackhawks', 'Blackhawks']}, 'away': {'name': 'Detroit Red Wings', 'aliases': ['Detroit Red Wings', 'Red Wings']}}]
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_sports_accounts', return_value=[('primary', self.account)]), patch.object(self.live, '_catalog', return_value=streams), patch.object(self.live._american_fixtures, 'get_schedule', return_value=fixtures):
			self.live._prepare_failover(self.account, '1', name, 'hockey')
		payload = json.loads(self.live.xbmcgui.Window.return_value.setProperty.call_args.args[1])
		self.assertEqual(payload['alternatives'], ['2'])
		self.assertEqual([feed['id'] for feed in payload['feeds']], ['1', '2'])

	def test_hockey_failover_rejects_name_not_matching_selected_id(self):
		streams = [{'stream_id': 1, 'name': 'Live | Jets vs Lightning | NHL | CA: Sports'}, {'stream_id': 2, 'name': 'Live | Jets vs Lightning | NHL | US: Sports'}]
		fixtures = [{'league': 'nhl', 'home': {'name': 'Winnipeg Jets', 'aliases': ['Winnipeg Jets', 'Jets']}, 'away': {'name': 'Tampa Bay Lightning', 'aliases': ['Tampa Bay Lightning', 'Lightning']}}]
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_sports_accounts', return_value=[('primary', self.account)]), patch.object(self.live, '_catalog', return_value=streams), patch.object(self.live._american_fixtures, 'get_schedule', return_value=fixtures):
			self.live._prepare_failover(self.account, '1', streams[1]['name'], 'hockey')
		self.live.xbmcgui.Window.return_value.setProperty.assert_not_called()


if __name__ == '__main__': unittest.main()
