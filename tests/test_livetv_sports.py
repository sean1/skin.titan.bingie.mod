import unittest
from datetime import datetime, timezone
from resources.lib.modules import livetv_sports
from tests.test_livetv import load_livetv


class LiveTVSportsTests(unittest.TestCase):
	def setUp(self):
		self.live = load_livetv()
		self.now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
		self.callbacks = {'evidence': self.live._other_sport_fixtures, 'matches': self.live._other_sport_match, 'soccer_event': self.live._soccer_event, 'scope': self.live._fixture_scope}

	def test_hockey_mislabeled_feed_and_explicit_live(self):
		streams = [
			{'stream_id': 1, 'name': 'Live | Blackhawks vs. Red Wings | all | CA: SOCCER PPV 30'},
			{'stream_id': 2, 'name': 'Live | Capitals @ Penguins | NHL.TV | MA: DAZN PPV 11'},
			{'name': 'NHL: Detroit Red Wings vs Chicago Blackhawks @ Oct 30 00:35 :TeliaPlay SE'},
		]
		groups = livetv_sports.groups(streams, 'hockey', self.now, **self.callbacks)
		self.assertEqual({key[0] for key in groups}, {('blackhawks', 'red wings'), ('capitals', 'penguins')})
		self.assertEqual(groups[(('blackhawks', 'red wings'), '')]['1'], streams[0])
		self.assertFalse(livetv_sports.groups(streams, 'football', self.now, **self.callbacks))

	def test_football_requires_current_evidence_and_live_feed(self):
		streams = [
			{'stream_id': 1, 'name': 'Live | Panthers vs. Packers | all | US: SOCCER PPV 95'},
			{'stream_id': 2, 'name': 'Next | Panthers vs. Packers | NFL | 29-10-2026 | 23:55'},
			{'stream_id': 3, 'name': 'NFL | 01 - TNF 8:15pm Panthers at Packers'},
			{'stream_id': 4, 'name': 'Live | Jets vs. Lightning | all | US: SOCCER PPV 1'},
			{'name': 'Next | Green Bay Packers Vs Carolina Panthers || English | NFL | 29-10-2026 | 23:55'},
			{'name': 'NHL: Jets @ Lightning @ Oct 25 23:25'},
		]
		groups = livetv_sports.groups(streams, 'football', self.now, **self.callbacks)
		self.assertEqual(list(groups), [(('packers', 'panthers'), '')])
		self.assertEqual(list(next(iter(groups.values()))), ['1'])
		self.assertFalse(livetv_sports.groups(streams, 'hockey', self.now, **self.callbacks))

	def test_basketball_explicit_and_evidenced_feeds_group_together(self):
		streams = [
			{'stream_id': 1, 'name': 'Live | Los Angeles Lakers vs Portland Trail Blazers | NBA | US'},
			{'stream_id': 2, 'name': 'Live | Trail Blazers vs Lakers | all | CA: SOCCER PPV'},
			{'name': 'NBA: Portland Trail Blazers @ Los Angeles Lakers @ Oct 29 23:25'},
			{'stream_id': 3, 'name': 'Live | Bulls vs Celtics | NBA | REPLAY'},
		]
		groups = livetv_sports.groups(streams, 'basketball', self.now, **self.callbacks)
		self.assertEqual(set(groups[(('lakers', 'trail blazers'), '')]), {'1', '2'})
		self.assertFalse(livetv_sports.groups(streams, 'hockey', self.now, **self.callbacks))

	def test_dated_soccer_event_is_not_reassigned(self):
		streams = [{'stream_id': 1, 'name': 'Live | Jets vs. Lightning | 2026-10-25 | SOCCER'}, {'name': 'NHL: Jets @ Lightning @ Oct 29 23:25'}]
		self.assertFalse(livetv_sports.groups(streams, 'hockey', self.now, **self.callbacks))
		streams[0]['name'] = 'Live | Jets vs. Lightning | Oct 25 2026 | SOCCER'
		self.assertFalse(livetv_sports.groups(streams, 'hockey', self.now, **self.callbacks))

	def test_full_names_and_reversed_nicknames_merge_only_matching_scope(self):
		streams = [
			{'stream_id': 1, 'name': 'Live | Green Bay Packers Vs Carolina Panthers | NFL | US'},
			{'stream_id': 2, 'name': 'Live | Panthers vs. Packers | all | SOCCER'},
			{'stream_id': 3, 'name': 'Live | Panthers vs. Packers | 2026-10-29 | NFL'},
			{'name': 'Next | Green Bay Packers Vs Carolina Panthers | NFL | 29-10-2026 | 23:55'},
		]
		groups = livetv_sports.groups(streams, 'football', self.now, **self.callbacks)
		self.assertEqual(set(groups[(('packers', 'panthers'), '')]), {'1', '2'})
		self.assertEqual(set(groups[(('packers', 'panthers'), '2026-10-29')]), {'3'})

	def test_nonleague_names_do_not_merge_by_suffix(self):
		streams = [{'stream_id': 1, 'name': 'Live | Example United vs Example City | NFL'}, {'stream_id': 2, 'name': 'Live | United vs City | NFL'}]
		self.assertEqual(len(livetv_sports.groups(streams, 'football', self.now, **self.callbacks)), 2)

	def test_college_teams_with_nfl_nicknames_stay_separate(self):
		streams = [
			{'stream_id': 1, 'name': 'Live | Georgia State Panthers vs Georgia Southern Eagles | NCAA Football'},
			{'stream_id': 2, 'name': 'Live | Carolina Panthers vs Philadelphia Eagles | NFL'},
			{'stream_id': 3, 'name': 'Live | Eagles vs Panthers | NCAA Football'},
		]
		self.assertEqual(len(livetv_sports.groups(streams, 'football', self.now, **self.callbacks)), 3)

	def test_month_first_dates_do_not_merge(self):
		streams = [
			{'stream_id': 1, 'name': 'Live | Green Bay Packers vs Carolina Panthers | NFL | Oct 29 2026'},
			{'stream_id': 2, 'name': 'Live | Panthers vs Packers | NFL | Oct 30 2026'},
		]
		self.assertEqual(len(livetv_sports.groups(streams, 'football', self.now, **self.callbacks)), 2)

	def test_generic_channels_and_malformed_events_are_not_listed(self):
		streams = [{'stream_id': 1, 'name': 'US | NHL NETWORK'}, {'stream_id': 2, 'name': 'Live | NHL Network | NHL | US'}, {'stream_id': 3, 'name': 'Live | Team vs. Team | NFL | US'}, {'stream_id': 4, 'name': 'Live | Panthers vs. Packers | NFL | REPLAY'}]
		self.assertFalse(livetv_sports.groups(streams, 'hockey', self.now, **self.callbacks))
		self.assertFalse(livetv_sports.groups(streams, 'football', self.now, **self.callbacks))


if __name__ == '__main__': unittest.main()
