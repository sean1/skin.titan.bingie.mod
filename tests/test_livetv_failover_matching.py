import json
import unittest
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


	def test_failover_rejects_alternatives_with_different_date_or_competition(self):
		selected = 'Live | Liverpool vs. Chelsea | Premier League | 2026-10-28 | CA: SOCCER 1'
		streams = [
			{'stream_id': 1, 'name': selected},
			{'stream_id': 2, 'name': selected.replace('CA:', 'US:')},
			{'stream_id': 3, 'name': selected.replace('2026-10-28', '2026-11-04')},
			{'stream_id': 4, 'name': selected.replace('Premier League', 'Carabao Cup')},
			{'stream_id': 5, 'name': self.name},
		]
		with patch.object(self.live, '_catalog', return_value=streams): self.live._prepare_failover(self.account, '1', selected)
		payload = json.loads(self.live.xbmcgui.Window.return_value.setProperty.call_args.args[1])
		self.assertEqual(payload['alternatives'], ['2'])


if __name__ == '__main__': unittest.main()
