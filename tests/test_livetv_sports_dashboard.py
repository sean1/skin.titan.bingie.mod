import unittest
from unittest.mock import patch

from tests.test_livetv import load_livetv


class LiveTVSportsDashboardTests(unittest.TestCase):
	def setUp(self):
		self.live = load_livetv()
		self.properties = {}
		self.live.ku.get_property = lambda key: self.properties.get(key, '')
		self.live.ku.set_property = lambda key, value: self.properties.__setitem__(key, value)
		self.live.ku.clear_property = lambda key: self.properties.pop(key, None)
		self.live.ku.build_url = lambda params: repr(params)
		self.account = {'server': 'http://example.test', 'username': 'user', 'password': 'secret'}
		self.streams = [
			{'stream_id': 1, 'name': 'Live | Blackhawks vs Red Wings | NHL | US: Sports'},
			{'stream_id': 2, 'name': 'Live | Blackhawks vs Red Wings | NHL | CA: Sports'},
			{'stream_id': 3, 'name': 'Live | Panthers vs Packers | NFL | US: Sports'},
		]

	def test_sections_only_show_the_requested_sport(self):
		for sport, expected in (('hockey', 'Blackhawks vs Red Wings'), ('football', 'Panthers vs Packers')):
			with self.subTest(sport=sport), patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=self.streams), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
				self.live.soccer({'sport': sport})
				self.assertEqual([call.args[0] for call in item.call_args_list], [expected])
				self.assertEqual(self.properties['LiveTV.Sport'], sport)
				self.assertEqual(self.properties['LiveTV.Sport.Title'], 'LIVE ' + sport.upper())

	def test_refresh_reloads_current_section_and_bypasses_cache(self):
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=self.streams) as catalog, patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.soccer({'sport': 'hockey', 'action': 'refresh'})
			self.live.soccer({'sport': 'hockey'})
			self.assertTrue(catalog.call_args.kwargs['force_refresh'])
			self.assertEqual(item.call_args.args[0], 'Blackhawks vs Red Wings')
			self.assertEqual(self.properties['LiveTV.Sport'], 'hockey')

	def test_hockey_feed_picker_preserves_both_feeds(self):
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=self.streams), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.soccer({'sport': 'hockey'})
			self.assertIn("'sport': 'hockey'", item.call_args.kwargs['extra_context'][0][1])
			item.reset_mock()
			self.live.match_feeds({'sport': 'hockey', 'home': 'blackhawks', 'away': 'red wings', 'scope': ''})
			self.assertEqual([call.args[1]['id'] for call in item.call_args_list], ['2', '2', '1'])

	def test_standings_keys_identify_sport_and_match(self):
		streams = self.streams + [{'stream_id': 4, 'name': 'Live | Florida Atlantic vs North Texas | NCAA Football | US: Sports'}]
		for sport, expected in (('hockey', {'nhl|blackhawks|red wings'}), ('football', {'nfl|panthers|packers', 'ncaa|florida atlantic|north texas'})):
			with self.subTest(sport=sport), patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=streams), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
				self.live.soccer({'sport': sport})
				properties = [call.kwargs['extra_properties'] for call in item.call_args_list]
				self.assertEqual({entry['LiveTV.StandingsKey'] for entry in properties}, expected)
				self.assertTrue(all(entry['LiveTV.StandingsKey'] == entry['LiveTV.StandingsMatchKey'] for entry in properties))

	def test_empty_section_has_no_standings_identity(self):
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=[]), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.soccer({'sport': 'hockey'})
			self.assertEqual(item.call_args.args[1]['mode'], 'livetv.refine')
			self.assertNotIn('extra_properties', item.call_args.kwargs)


if __name__ == '__main__': unittest.main()
