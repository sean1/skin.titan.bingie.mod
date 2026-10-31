import unittest
from unittest.mock import patch, Mock
from tests.test_livetv import load_livetv


class AllStandingsTests(unittest.TestCase):
	def setUp(self):
		self.live = load_livetv()
		self.live.ku.dialog.textviewer = Mock()

	def test_football_lists_nfl_sections(self):
		groups = [('NFL · NFC', [{'position': 1, 'team': 'Packers', 'points': '5-2-0'}]), ('NFL · AFC', [{'position': 1, 'team': 'Bills', 'points': '6-1-0'}])]
		with patch.object(self.live._american_standings, 'get_all', return_value=(groups, 'W-L-T')) as get:
			self.live.all_standings({'sport': 'football'})
		get.assert_called_once_with('nfl')
		heading, body = self.live.ku.dialog.textviewer.call_args.args
		self.assertEqual(heading, 'All football standings')
		for text in ('NFL · NFC  ·  W-L-T', 'Packers  —  5-2-0', 'NFL · AFC', 'Bills  —  6-1-0'):
			self.assertIn(text, body)


	def test_hockey_sanitizes_provider_markup_and_invalid_sport_does_nothing(self):
		groups = [('NHL · Central', [{'position': 1, 'team': '[B]Wild[/B]', 'points': '20'}])]
		with patch.object(self.live._american_standings, 'get_all', return_value=(groups, 'PTS')) as get:
			self.live.all_standings({'sport': 'hockey'})
			self.live.all_standings({'sport': 'soccer'})
		self.assertEqual(get.call_count, 1)
		self.assertIn('NHL · Central  ·  PTS', self.live.ku.dialog.textviewer.call_args.args[1])
		self.assertNotIn('[B]', self.live.ku.dialog.textviewer.call_args.args[1])


if __name__ == '__main__': unittest.main()
