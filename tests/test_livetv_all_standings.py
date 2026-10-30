import unittest
import types
from pathlib import Path
from tests.module_isolation import load_module
from unittest.mock import patch, Mock
from tests.test_livetv import load_livetv


class AllStandingsTests(unittest.TestCase):
	def setUp(self):
		self.live = load_livetv()
		self.live.ku.dialog.textviewer = Mock()

	def test_football_lists_every_section_and_labels_unavailable_league(self):
		groups = [('NFL · NFC', [{'position': 1, 'team': 'Packers', 'points': '5-2-0'}]), ('NFL · AFC', [{'position': 1, 'team': 'Bills', 'points': '6-1-0'}])]
		with patch.object(self.live._american_standings, 'get_all', side_effect=[(groups, 'W-L-T'), ([], 'OVERALL')]) as get:
			self.live.all_standings({'sport': 'football'})
		self.assertEqual([call.args[0] for call in get.call_args_list], ['nfl', 'ncaa'])
		heading, body = self.live.ku.dialog.textviewer.call_args.args
		self.assertEqual(heading, 'All football standings')
		for text in ('NFL · NFC  ·  W-L-T', 'Packers  —  5-2-0', 'NFL · AFC', 'Bills  —  6-1-0', 'College football standings unavailable.'):
			self.assertIn(text, body)

	def test_hockey_sanitizes_provider_markup_and_invalid_sport_does_nothing(self):
		groups = [('NHL · Central', [{'position': 1, 'team': '[B]Wild[/B]', 'points': '20'}])]
		with patch.object(self.live._american_standings, 'get_all', return_value=(groups, 'PTS')) as get:
			self.live.all_standings({'sport': 'hockey'})
			self.live.all_standings({'sport': 'soccer'})
		self.assertEqual(get.call_count, 1)
		self.assertIn('NHL · Central  ·  PTS', self.live.ku.dialog.textviewer.call_args.args[1])
		self.assertNotIn('[B]', self.live.ku.dialog.textviewer.call_args.args[1])

	def test_unavailable_section_is_explicit_in_viewer(self):
		with patch.object(self.live._american_standings, 'get_all', return_value=([('NHL · Central Division (unavailable)', [])], 'PTS')):
			self.live.all_standings({'sport': 'hockey'})
		self.assertIn('Standings unavailable.', self.live.ku.dialog.textviewer.call_args.args[1])

	def test_route_dispatches_to_viewer(self):
		modules = types.ModuleType("modules")
		modules.kodi_utils = Mock()
		routing = load_module("test_sports_routing", Path(__file__).resolve().parents[1] / "resources/lib/routing.py", {"modules": modules})
		with patch.object(routing, '_import', return_value=Mock()) as loader:
			routing.POV_ROUTES['livetv.all_standings']({'sport': 'hockey'})
		loader.assert_called_once_with('menus.livetv', 'all_standings')
		loader.return_value.assert_called_once_with({'sport': 'hockey'})


if __name__ == '__main__': unittest.main()
