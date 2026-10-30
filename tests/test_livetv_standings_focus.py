import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


class StandingsFocusTests(unittest.TestCase):
	def setUp(self):
		self.props = {}
		self.labels = {}
		ku = types.ModuleType('modules.kodi_utils')
		ku.get_visibility = lambda _: True
		ku.get_infolabel = lambda label: self.labels.get(label, '')
		ku.set_property = lambda key, value: self.props.__setitem__(key, value)
		ku.clear_property = lambda key: self.props.pop(key, None)
		ku.translate_path = lambda path: path
		ku.profile_path = '/profile'
		ku.addon_path = '/addon'
		self.soccer = types.ModuleType('modules.livetv_sportapi')
		self.soccer.KEY_FILE = 'key'
		self.soccer.get_standings = Mock(return_value=([{'position': 1, 'team': 'Alpha', 'team_id': 1, 'points': 12}], 'updated'))
		self.american = types.ModuleType('modules.livetv_american_standings')
		self.american.get = Mock(return_value=([{'position': 1, 'team': 'Jets', 'points': '8', 'highlight': True}], 'NHL · Central', 'PTS'))
		modules = types.ModuleType('modules')
		modules.kodi_utils, modules.livetv_sportapi, modules.livetv_american_standings = ku, self.soccer, self.american
		self.modules = modules
		self.module = load_module('test_standings_focus_module', ROOT / 'resources/lib/modules/livetv_standings_focus.py', {'modules': modules, 'modules.kodi_utils': ku, 'modules.livetv_sportapi': self.soccer, 'modules.livetv_american_standings': self.american})
		self.focus = self.module.FocusedStandings()

	def selected(self, key, league='League', match=''):
		self.labels = {'Container(523).ListItem.Property(LiveTV.StandingsKey)': key, 'Container(523).ListItem.Property(LiveTV.League)': league, 'Container(523).ListItem.Property(LiveTV.StandingsMatchKey)': match}

	def test_tagged_sport_publishes_heading_metric_and_highlight(self):
		for sport in ('nhl', 'nfl', 'ncaa'):
			with self.subTest(sport=sport):
				key = sport + '|Jets|Lightning'
				self.selected(key)
				self.focus.identity = key
				with patch.dict('sys.modules', {'modules': self.modules, 'modules.livetv_american_standings': self.american}): self.focus._load(key)
				self.focus.tick()
				self.american.get.assert_called_with(sport, 'Jets', 'Lightning')
				self.assertEqual(self.props['LiveTV.Standings.League'], 'NHL · Central')
				self.assertEqual(self.props['LiveTV.Standings.Metric'], 'PTS')
				self.assertEqual(self.props['LiveTV.Standings.MatchKey'], key)
				self.assertEqual(self.props['LiveTV.Standings.Row1.Highlight'], 'true')

	def test_stale_result_is_discarded_and_properties_cleared(self):
		old, new = 'nhl|Jets|Lightning', 'nfl|Jets|Dolphins'
		self.selected(old)
		self.focus.identity = old
		self.focus.results.put((old, [{'position': 1, 'team': 'Jets', 'points': '1'}], 'Old', 'PTS'))
		self.selected(new)
		self.focus.tick()
		self.assertNotIn('LiveTV.Standings.Key', self.props)
		self.assertNotIn('LiveTV.Standings.League', self.props)

	def test_invalid_sport_key_does_not_start_fetch(self):
		for key in ('nhl|Jets', 'nhl|../jets|Lightning', 'nfl|Jets|Jets', 'mlb|Jets|Lightning', 'nhl|' + 'x' * 81 + '|Lightning'):
			with self.subTest(key=key):
				self.selected(key)
				self.assertEqual(self.focus._focused(), ('', ''))

	def test_soccer_numeric_identity_and_highlighting_remain_supported(self):
		self.selected('123', 'Premier League', '1:2')
		self.focus.identity, self.focus.league = '123', 'Premier League'
		self.focus._load('123')
		self.focus.tick()
		self.assertEqual(self.props['LiveTV.Standings.League'], 'Premier League')
		self.assertEqual(self.props['LiveTV.Standings.Metric'], 'PTS')
		self.assertEqual(self.props['LiveTV.Standings.Row1.Highlight'], 'true')


if __name__ == '__main__': unittest.main()
