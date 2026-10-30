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

	def test_dated_other_sports_exclude_matching_events(self):
		now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
		streams = [
			{'stream_id': 1, 'name': 'Live | Blackhawks vs. Red Wings | all | CA: SOCCER PPV'},
			{'stream_id': 2, 'name': 'Live | Jets vs. Lightning | all | CA: SOCCER PPV'},
			{'stream_id': 3, 'name': 'Live | Florida Atlantic vs. North Texas | all | CA: SOCCER PPV'},
			{'stream_id': 4, 'name': 'Live | Panthers vs. Packers | all | CA: SOCCER PPV'},
			{'stream_id': 5, 'name': 'Live | Alpha vs. Beta | all | CA: SOCCER PPV'},
			{'name': 'NHL: Detroit Red Wings vs Chicago Blackhawks @ Oct 30 00:35 :TeliaPlay SE'},
			{'name': 'NHL: Jets @ Lightning @ Oct 29 23:25 :TeliaPlay SE'},
			{'name': 'CA (TSN+ 012) | NCAA Football: Florida Atlantic vs. North Texas (2026-10-29 19:30:00)'},
			{'name': 'Next | Green Bay Packers Vs Carolina Panthers || English | NFL | 29-10-2026 | 23:55 (GMT)'},
		]
		self.assertEqual(list(self.live._soccer_groups(streams, now)), [(('alpha', 'beta'), '')])

	def test_other_sport_evidence_requires_both_teams_and_current_date(self):
		now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
		match = {'stream_id': 1, 'name': 'Live | Jets vs. Lightning | all | CA: SOCCER PPV'}
		for evidence in ('NHL: Jets vs Lightning @ Oct 25 23:25', 'NHL: Jets vs Thunder @ Oct 29 23:25', 'Jets vs Lightning @ Oct 29 23:25', 'NHL: Jets vs Lightning', 'Live | Jets vs Lightning | SOCCER | NFL highlights | 29-10-2026'):
			with self.subTest(evidence=evidence):
				self.assertEqual(len(self.live._soccer_groups([match, {'name': evidence}], now)), 1)

	def test_generic_team_suffix_does_not_hide_soccer(self):
		now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
		streams = [{'stream_id': 1, 'name': 'Live | United vs City | all | CA: SOCCER'}, {'name': 'NHL: Example United vs Example City @ Oct 29 23:25'}]
		self.assertEqual(len(self.live._soccer_groups(streams, now)), 1)

	def test_squash_requires_live_exact_match(self):
		now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
		match = {'stream_id': 1, 'name': 'Live | Philadelphia Freedom vs. Seattle Freeze | all | CA: SOCCER'}
		live = {'name': 'Live | Philadelphia Freedom vs. Seattle Freeze | National Squash League | DE: DAZN PPV 1'}
		self.assertFalse(self.live._soccer_groups([match, live], now))
		for name in (live['name'].replace('Live |', 'Next |'), live['name'].replace('Philadelphia Freedom', 'Other Freedom')):
			with self.subTest(name=name): self.assertEqual(len(self.live._soccer_groups([match, {'name': name}], now)), 1)

	def test_other_sport_date_must_belong_to_event_record(self):
		now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
		match = {'stream_id': 1, 'name': 'Live | Jets vs. Lightning | all | CA: SOCCER'}
		evidence = {'name': 'NHL: Jets vs Lightning | 2026-10-29'}
		self.assertEqual(len(self.live._soccer_groups([match, evidence], now)), 1)

	def test_dated_soccer_listing_is_not_hidden_by_other_sport_fixture(self):
		now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
		match = {'stream_id': 1, 'name': 'Live | Jets vs. Lightning | Oct 27 2026 | CA: SOCCER'}
		evidence = {'name': 'NHL: Jets @ Lightning @ Oct 29 23:25 :TeliaPlay SE'}
		self.assertEqual(len(self.live._soccer_groups([match, evidence], now)), 1)

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
