import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from tests.test_livetv import load_livetv


class LiveTVSoccerTests(unittest.TestCase):
	def setUp(self):
		self.live = load_livetv()
		self.live.ku.set_property = lambda *args: None
		self.live.ku.get_property = lambda *args: ''
		self.live.ku.build_url = lambda params: repr(params)
		self.account = {'server': 'http://example.test', 'username': 'user', 'password': 'secret'}
		self.fixtures = [self.fixture('Alpha', 'Beta', 'Premier League'), self.fixture('Gamma', 'Delta', 'Other / unknown')]
		self.fixture_source = patch.object(self.live, '_live_soccer_fixtures', side_effect=lambda **kwargs: (self.fixtures, [], [], 'Live fixtures updated'))
		self.fixture_source.start()
		self.addCleanup(self.fixture_source.stop)
		self.streams = [
			{'stream_id': 9, 'name': 'Live | Alpha vs Beta | Premier League | US: SOCCER'},
			{'stream_id': 8, 'name': 'Live | Alpha vs Beta | Premier League | CA: SOCCER'},
			{'stream_id': 8, 'name': 'Live | Alpha vs Beta | Premier League | CA: SOCCER'},
			{'stream_id': 4, 'name': 'Live | Gamma vs Delta | Soccer match | CA: SOCCER'},
			{'stream_id': 5, 'name': 'End | Alpha vs Beta | Premier League | CA: SOCCER'},
		]

	@staticmethod
	def fixture(home, away, league):
		return {'home': home, 'away': away, 'league': league, 'status': 'LIVE', 'date': '2026-10-29T12:00:00Z'}

	def test_groups_live_matches_and_prefers_canadian_feed(self):
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=self.streams), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.soccer({'league': 'All'})
		self.assertEqual(len(item.call_args_list), 2)
		other, premier = item.call_args_list
		self.assertEqual(premier.args[1]['id'], '8')
		self.assertEqual(premier.args[1]['name'], self.streams[1]['name'])
		self.assertIn('Premier League', premier.args[0])
		self.assertIn('2 feeds', premier.args[0])
		self.assertIn('Choose another feed', str(premier.kwargs['extra_context']))
		self.assertIn('Other / unknown', other.args[0])
		self.assertEqual(other.kwargs['extra_context'], [])

	def test_matches_are_grouped_by_league_before_team_name(self):
		self.fixtures.extend([self.fixture('Zeta', 'Beta', 'Premier League'), self.fixture('Able', 'Baker', 'La Liga')])
		streams = self.streams + [
			{'stream_id': 10, 'name': 'Live | Zeta vs Beta | Premier League | CA: SOCCER'},
			{'stream_id': 11, 'name': 'Live | Able vs Baker | La Liga | CA: SOCCER'},
		]
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=streams), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.soccer({'league': 'All'})
		labels = [call.args[0] for call in item.call_args_list]
		self.assertEqual([label.split(' — ')[0] for label in labels], ['La Liga', 'Other / unknown', 'Premier League', 'Premier League'])

	def test_filter_and_empty_fallback(self):
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=self.streams), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.soccer({'league': 'Other / unknown'})
		self.assertEqual(len(item.call_args_list), 1)
		self.assertEqual(item.call_args.args[1]['id'], '4')
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=[]), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.soccer({'league': 'All'})
		self.assertEqual(len(item.call_args_list), 2)
		self.assertTrue(all('No feeds found yet' in call.args[0] and not call.args[1] for call in item.call_args_list))
		self.fixtures.clear()
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog') as catalog, patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.soccer({'league': 'All'})
		self.assertEqual(item.call_args.args[1]['mode'], 'livetv.refine')
		catalog.assert_not_called()

	def test_refresh_bypasses_cache(self):
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=[]) as catalog, patch.object(self.live, '_item'), patch.object(self.live, '_finish'):
			self.live.soccer({'league': 'All', 'refresh': '1'})
		self.assertTrue(catalog.call_args.kwargs['force_refresh'])

	def test_dated_tennis_evidence_excludes_fixture_but_generic_feeds_remain(self):
		now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
		streams = [{'stream_id': 58, 'name': 'Live | Djokovic vs. Struff | all | 8K EXCLUSIVE | US: SOCCER PPV 58'}, {'stream_id': 59, 'name': 'Live | Alpha vs. Beta | all | US: SOCCER PPV 59'}, {'stream_id': 60, 'name': 'Djokovic, Novak vs Struff, Jan-Lennard @ Oct 29 18:00 - ATP Basel :Tennis'}]
		groups = self.live._soccer_groups(streams, now)
		self.assertEqual(list(groups), [(('alpha', 'beta'), '')])

if __name__ == '__main__': unittest.main()
