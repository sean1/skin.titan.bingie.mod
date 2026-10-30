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

	def test_ncaa_all_text_has_every_group_and_safe_highlights(self):
		key = 'ncaa|Troy|Dukes'
		groups = [('Conference %d' % n, [{'position': 1, 'team': '[COLOR red]Team %d' % n, 'points': '5-2-0', 'highlight': n == 12}]) for n in range(1, 13)]
		self.american.get_ncaa_groups = Mock(return_value=(groups, 'OVERALL'))
		self.selected(key)
		self.focus.identity = key
		with patch.dict('sys.modules', {'modules': self.modules, 'modules.livetv_american_standings': self.american}): self.focus._load(key)
		self.focus.tick()
		text = self.props['LiveTV.Standings.AllText']
		self.assertIn('Conference 12', text)
		self.assertIn('[B]1. COLOR redTeam 12  —  5-2-0[/B]', text)
		self.assertNotIn('[COLOR red]', text)
		self.assertEqual(self.props['LiveTV.Standings.Layout'], 'ncaa')
		self.selected('')
		self.focus.tick()
		self.assertNotIn('LiveTV.Standings.AllText', self.props)

	def test_college_columns_and_fallbacks(self):
		key = 'ncaa|Troy|Dukes'
		def row(n, highlight=False): return {'position': n, 'team': 'Team %d' % n, 'points': '5-2', 'highlight': highlight}
		cases = [
			([('East', [row(1, True), row(2, True), row(3), row(4)])], 'college', 'East (continued)'),
			([('East', [row(1, True)]), ('West', [row(2, True)])], 'college', 'West'),
			([('East', [row(1, True)])], 'ncaa', None),
			([('East', [row(1, True), row(1, True)])], 'ncaa', None),
			([('East', [row(1, True), row(2, True), row(3, True)])], 'ncaa', None),
			([('East', [row(n, n in (1, 2)) for n in range(1, 20)])], 'ncaa', None),
		]
		for groups, layout, second in cases:
			with self.subTest(layout=layout, groups=groups):
				self.american.get_ncaa_groups = Mock(return_value=(groups, 'OVERALL'))
				self.selected(key)
				self.focus.identity = key
				with patch.dict('sys.modules', {'modules': self.modules, 'modules.livetv_american_standings': self.american}): self.focus._load(key)
				self.focus.tick()
				self.assertEqual(self.props['LiveTV.Standings.Layout'], layout)
				if second: self.assertEqual(self.props['LiveTV.Standings.Group2.Name'], second)
				else: self.assertIn('LiveTV.Standings.AllText', self.props)
				self.selected('')
				self.focus.tick()
				self.assertNotIn('LiveTV.Standings.Group1.Row1.Team', self.props)

	def test_college_columns_retry_when_another_group_is_unavailable(self):
		key = 'ncaa|Troy|Dukes'
		groups = [('East', [{'position': 1, 'team': 'Troy', 'points': '5-2', 'highlight': True}, {'position': 2, 'team': 'Dukes', 'points': '4-3', 'highlight': True}]), ('West (unavailable)', [])]
		self.american.get_ncaa_groups = Mock(return_value=(groups, 'OVERALL'))
		self.selected(key)
		self.focus.identity = key
		with patch.dict('sys.modules', {'modules': self.modules, 'modules.livetv_american_standings': self.american}): self.focus._load(key)
		with patch.object(self.module, 'monotonic', return_value=1000): self.focus.tick()
		self.assertEqual(self.props['LiveTV.Standings.Layout'], 'college')
		self.assertEqual(self.focus.published_at, 1000 - self.module._REFRESH_SECONDS + self.module._RETRY_SECONDS)

	def test_nba_conferences_order_rows_and_clear(self):
		key = 'nba|Lakers|Celtics'
		rows = [{'position': n, 'team': 'Team %d' % n, 'points': '5-2', 'highlight': n == 15} for n in range(1, 16)]
		self.american.get_nba_groups = Mock(return_value=([('Western Conference', rows), ('Eastern Conference', rows)], 'W-L'))
		self.selected(key)
		self.focus.identity = key
		with patch.dict('sys.modules', {'modules': self.modules, 'modules.livetv_american_standings': self.american}): self.focus._load(key)
		self.focus.tick()
		self.american.get_nba_groups.assert_called_once_with('Lakers', 'Celtics')
		self.assertEqual(self.props['LiveTV.Standings.Layout'], 'nba')
		self.assertEqual(self.props['LiveTV.Standings.Group1.Name'], 'Eastern Conference')
		self.assertEqual(self.props['LiveTV.Standings.Group2.Name'], 'Western Conference')
		self.assertEqual(self.props['LiveTV.Standings.Group2.Row15.Team'], 'Team 15')
		self.assertEqual(self.props['LiveTV.Standings.Group1.Row15.Highlight'], 'true')
		self.selected('')
		self.focus.tick()
		self.assertNotIn('LiveTV.Standings.Group2.Row15.Team', self.props)

	def test_nba_unavailable_conference_is_published(self):
		key = 'nba|Lakers|Celtics'
		self.american.get_nba_groups = Mock(return_value=([('Eastern Conference (unavailable)', []), ('Western Conference', [{'position': 1, 'team': 'Lakers', 'points': '4-3'}])], 'W-L'))
		self.selected(key)
		self.focus.identity = key
		with patch.dict('sys.modules', {'modules': self.modules, 'modules.livetv_american_standings': self.american}): self.focus._load(key)
		self.focus.tick()
		self.assertEqual(self.props['LiveTV.Standings.Group1.Name'], 'Eastern Conference (unavailable)')

	def test_nhl_publishes_four_groups_and_clears_on_focus_change(self):
		key = 'nhl|Jets|Lightning'
		groups = [('Division %d' % g, [{'position': i, 'team': 'Team %d %d' % (g, i), 'points': str(i), 'highlight': g == 4 and i == 8} for i in range(1, 9)]) for g in range(1, 5)]
		self.american.get_nhl_groups = Mock(return_value=(groups, 'PTS'))
		self.selected(key)
		self.focus.identity = key
		with patch.dict('sys.modules', {'modules': self.modules, 'modules.livetv_american_standings': self.american}): self.focus._load(key)
		self.focus.tick()
		self.assertEqual(self.props['LiveTV.Standings.Key'], key)
		self.assertEqual(self.props['LiveTV.Standings.Group4.Name'], 'Division 4')
		self.assertEqual(self.props['LiveTV.Standings.Layout'], 'hockey')
		self.assertEqual(self.props['LiveTV.Standings.GroupCount'], '4')
		self.assertEqual(self.props['LiveTV.Standings.Group4.Row8.Highlight'], 'true')
		self.selected('')
		self.focus.tick()
		self.assertNotIn('LiveTV.Standings.Group4.Row8.Highlight', self.props)
		self.assertNotIn('LiveTV.Standings.Key', self.props)

	def test_nfl_publishes_eight_groups_including_unavailable(self):
		key = 'nfl|Panthers|Packers'
		groups = [('Division %d' % g, [{'position': i, 'team': 'Team %d %d' % (g, i), 'points': '2-1-0', 'highlight': g == 8 and i == 4} for i in range(1, 5)]) for g in range(1, 9)]
		groups[0] = ('AFC East (unavailable)', [])
		self.american.get_nfl_groups = Mock(return_value=(groups, 'W-L-T'))
		self.selected(key)
		self.focus.identity = key
		with patch.dict('sys.modules', {'modules': self.modules, 'modules.livetv_american_standings': self.american}): self.focus._load(key)
		self.focus.tick()
		self.assertEqual(self.props['LiveTV.Standings.Layout'], 'nfl')
		self.assertEqual(self.props['LiveTV.Standings.GroupCount'], '8')
		self.assertEqual(self.props['LiveTV.Standings.Group1.Name'], 'AFC East (unavailable)')
		self.assertEqual(self.props['LiveTV.Standings.Group8.Row4.Highlight'], 'true')
		self.assertAlmostEqual(self.focus.published_at, self.module.monotonic() - self.module._REFRESH_SECONDS + self.module._RETRY_SECONDS, delta=1)

	def test_incomplete_nhl_groups_not_published(self):
		key = 'nhl|Jets|Lightning'
		self.selected(key)
		self.focus.identity = key
		self.focus.results.put((key, [('Central', [{'position': 1, 'team': 'Jets', 'points': '5'}])], 'NHL', 'PTS'))
		self.focus.tick()
		self.assertNotIn('LiveTV.Standings.Key', self.props)

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
