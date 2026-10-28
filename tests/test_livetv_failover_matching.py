import json
import unittest
from unittest.mock import Mock, patch

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
		with patch.object(self.live, '_request', return_value=streams): self.live._prepare_failover(self.account, '1', self.name)
		payload = json.loads(self.live.xbmcgui.Window.return_value.setProperty.call_args.args[1])
		self.assertEqual(payload['id'], '1')
		self.assertEqual(payload['alternatives'], ['2', '5', '6', '7', '8', '9', '10', '11'])
		self.assertNotIn('secret', json.dumps(payload))
		self.assertTrue(payload['token'])

	def test_unrecognized_play_avoids_network_and_clears_previous_session(self):
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_request') as request, patch.object(self.live, '_handle', return_value=1):
			self.live.play({'id': '1', 'name': 'TSN 1'})
		request.assert_not_called()
		self.live.xbmcgui.Window.return_value.clearProperty.assert_called_once_with('BingieLiveTVFailover')
		self.live.xbmcplugin.setResolvedUrl.assert_called_once()

	def test_route_name_must_match_selected_id_in_catalog(self):
		streams = [{'stream_id': 1, 'name': 'TSN 1'}, {'stream_id': 2, 'name': self.name}]
		with patch.object(self.live, '_request', return_value=streams): self.live._prepare_failover(self.account, '1', self.name)
		self.live.xbmcgui.Window.return_value.setProperty.assert_not_called()

	def test_request_failure_still_resolves_original_stream(self):
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_request', side_effect=OSError('network error')), patch.object(self.live, '_handle', return_value=1):
			self.live.play({'id': '1', 'name': self.name})
		self.live.xbmcplugin.setResolvedUrl.assert_called_once()
		self.live.xbmcgui.Window.return_value.setProperty.assert_not_called()

	def test_event_name_only_passed_for_recognized_soccer(self):
		self.assertEqual(self.live._play_params('1', self.name), {'mode': 'livetv.play', 'id': '1', 'name': self.name})
		self.assertEqual(self.live._play_params('1', 'TSN 1'), {'mode': 'livetv.play', 'id': '1'})


if __name__ == '__main__': unittest.main()
