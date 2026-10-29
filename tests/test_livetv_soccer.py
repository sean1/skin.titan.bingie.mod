import unittest
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from tests.test_livetv import load_livetv


class LiveTVSoccerTests(unittest.TestCase):
	def setUp(self):
		self.live = load_livetv()
		self.live.ku.set_property = lambda *args: None
		self.live.ku.get_property = lambda *args: ''
		self.live.ku.build_url = lambda params: repr(params)
		self.account = {'server': 'http://example.test', 'username': 'user', 'password': 'secret'}
		self.streams = [
			{'stream_id': 9, 'name': 'Live | Alpha vs Beta | Premier League | US: SOCCER'},
			{'stream_id': 8, 'name': 'Live | Alpha vs Beta | Premier League | CA: SOCCER'},
			{'stream_id': 8, 'name': 'Live | Alpha vs Beta | Premier League | CA: SOCCER'},
			{'stream_id': 4, 'name': 'Live | Gamma vs Delta | Soccer match | CA: SOCCER'},
			{'stream_id': 5, 'name': 'End | Alpha vs Beta | Premier League | CA: SOCCER'},
		]

	def test_groups_live_matches_and_prefers_canadian_feed(self):
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=self.streams), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.soccer({'league': 'All'})
		self.assertEqual(len(item.call_args_list), 2)
		self.assertEqual(item.call_args_list[0].args[1]['id'], '8')
		self.assertEqual(item.call_args_list[0].args[1]['name'], self.streams[1]['name'])
		self.assertIn('Premier League', item.call_args_list[0].args[0])
		self.assertIn('2 feeds', item.call_args_list[0].args[0])
		self.assertIn('Choose another feed', str(item.call_args_list[0].kwargs['extra_context']))
		self.assertIn('Other / unknown', item.call_args_list[1].args[0])
		self.assertEqual(item.call_args_list[1].kwargs['extra_context'], [])

	def test_matches_are_grouped_by_league_before_team_name(self):
		streams = self.streams + [
			{'stream_id': 10, 'name': 'Live | Zeta vs Beta | Premier League | CA: SOCCER'},
			{'stream_id': 11, 'name': 'Live | Able vs Baker | La Liga | CA: SOCCER'},
		]
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=streams), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.soccer({'league': 'All'})
		labels = [call.args[0] for call in item.call_args_list]
		self.assertEqual([label.split(' — ')[0] for label in labels], ['La Liga', 'Premier League', 'Premier League', 'Other / unknown'])

	def test_filter_and_empty_fallback(self):
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=self.streams), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.soccer({'league': 'Other / unknown'})
		self.assertEqual(len(item.call_args_list), 1)
		self.assertEqual(item.call_args.args[1]['id'], '4')
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=[]), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.soccer({'league': 'All'})
		self.assertEqual(item.call_args.args[1]['mode'], 'livetv.refine')

	def test_refresh_bypasses_cache(self):
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=[]) as catalog, patch.object(self.live, '_item'), patch.object(self.live, '_finish'):
			self.live.soccer({'league': 'All', 'refresh': '1'})
		self.assertTrue(catalog.call_args.kwargs['force_refresh'])

	def test_dated_tennis_evidence_excludes_fixture_but_generic_feeds_remain(self):
		now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
		streams = [{'stream_id': 58, 'name': 'Live | Djokovic vs. Struff | all | 8K EXCLUSIVE | US: SOCCER PPV 58'}, {'stream_id': 59, 'name': 'Live | Alpha vs. Beta | all | US: SOCCER PPV 59'}, {'stream_id': 60, 'name': 'Djokovic, Novak vs Struff, Jan-Lennard @ Oct 29 18:00 - ATP Basel :Tennis'}]
		groups = self.live._soccer_groups(streams, now)
		self.assertEqual(list(groups), [(('alpha', 'beta'), '')])

	def test_old_or_unrelated_tennis_evidence_does_not_hide_match(self):
		now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
		match = {'stream_id': 58, 'name': 'Live | Djokovic vs. Struff | all | US: SOCCER PPV 58'}
		for evidence in ('Djokovic, Novak vs Struff, Jan-Lennard @ Oct 29 2025 - ATP Tennis', 'Smith vs Jones @ Oct 29 2026 - ATP Tennis Djokovic Struff', 'Djokovic vs Struff @ yesterday - ATP Tennis'):
			with self.subTest(evidence=evidence):
				self.assertEqual(len(self.live._soccer_groups([match, {'name': evidence}], now)), 1)

	def test_refresh_reports_cached_snapshot_when_provider_fails(self):
		from tests.test_livetv_catalog import catalog
		root = Path(__file__).resolve().parents[1] / 'tmp'
		root.mkdir(exist_ok=True)
		with tempfile.TemporaryDirectory(dir=root) as profile:
			fetch = Mock(side_effect=[self.streams, OSError('offline')])
			status = Mock()
			catalog.get(self.account, 'get_live_streams', fetch, profile, now=1000, on_status=status)
			self.assertEqual(catalog.get(self.account, 'get_live_streams', fetch, profile, now=1010, on_status=status, force_refresh=True), self.streams)
			self.assertEqual(fetch.call_count, 2)
			status.assert_called_with(1000, True)


if __name__ == '__main__': unittest.main()
