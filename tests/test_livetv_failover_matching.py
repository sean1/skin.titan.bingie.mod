import json
import unittest
from datetime import datetime
from unittest.mock import patch

from tests.test_livetv import load_livetv


class LiveTVFailoverMatchingTests(unittest.TestCase):
	def setUp(self):
		self.live = load_livetv()
		self.account = {'server': 'http://example.test', 'username': 'user', 'password': 'secret'}
		self.name = 'Live | Liverpool vs. Chelsea | all | 8K EXCLUSIVE | CA: SOCCER PPV 17'

	def test_only_recognizes_explicit_live_soccer_events(self):
		event = self.live._soccer_event(self.name)
		self.assertEqual(event, ('liverpool', 'chelsea'))
		for name in ('END | Liverpool vs. Chelsea | CA: SOCCER PPV 17', 'Live | Liverpool vs. Everton | NHL', 'Live | Liverpool | Soccer', 'Live | Liverpool vs. Chelsea | UPCOMING SOCCER'):
			with self.subTest(name=name): self.assertIsNone(self.live._soccer_event(name))

	def test_same_teams_only_unique_and_bounded_without_credentials(self):
		streams = [{'stream_id': 1, 'name': self.name}, {'stream_id': 2, 'name': self.name.replace('CA:', 'US:')}, {'stream_id': 2, 'name': self.name}, {'stream_id': 3, 'name': self.name.replace('Chelsea', 'Everton')}, {'stream_id': 4, 'name': self.name.replace('Live', 'End')}]
		streams += [{'stream_id': n, 'name': self.name} for n in range(5, 30)]
		with patch.object(self.live, '_catalog', return_value=streams): self.live._prepare_failover(self.account, '1', self.name)
		payload = json.loads(self.live.xbmcgui.Window.return_value.setProperty.call_args.args[1])
		self.assertEqual(payload['id'], '1')
		self.assertEqual(payload['alternatives'], ['2', '5', '6', '7', '8', '9', '10', '11'])
		self.assertNotIn('secret', json.dumps(payload))
		self.assertTrue(payload['token'])

	def test_hockey_failover_uses_matching_explicit_and_corroborated_feeds(self):
		today = datetime.now().astimezone().strftime('%Y-%m-%d')
		name = 'Live | Blackhawks vs Red Wings | NHL | CA: Sports'
		streams = [
			{'stream_id': 1, 'name': name},
			{'stream_id': 2, 'name': 'Live | Blackhawks vs Red Wings | all | US: SOCCER PPV'},
			{'stream_id': 3, 'name': 'Live | Blackhawks vs Penguins | NHL | US: Sports'},
			{'name': 'NHL: Detroit Red Wings vs Chicago Blackhawks (%s 19:30:00)' % today},
		]
		with patch.object(self.live, '_catalog', return_value=streams): self.live._prepare_failover(self.account, '1', name, 'hockey')
		payload = json.loads(self.live.xbmcgui.Window.return_value.setProperty.call_args.args[1])
		self.assertEqual(payload['alternatives'], ['2'])
		self.assertEqual([feed['id'] for feed in payload['feeds']], ['1', '2'])

	def test_hockey_failover_rejects_name_not_matching_selected_id(self):
		streams = [{'stream_id': 1, 'name': 'Live | Jets vs Lightning | NHL | CA: Sports'}, {'stream_id': 2, 'name': 'Live | Jets vs Lightning | NHL | US: Sports'}]
		with patch.object(self.live, '_catalog', return_value=streams): self.live._prepare_failover(self.account, '1', streams[1]['name'], 'hockey')
		self.live.xbmcgui.Window.return_value.setProperty.assert_not_called()


if __name__ == '__main__': unittest.main()
