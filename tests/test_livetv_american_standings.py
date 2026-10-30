import unittest
import json
from io import BytesIO
from unittest.mock import patch
from resources.lib.modules import livetv_american_standings as standings


def team(name, location, nickname, abbreviation, record):
	return {'team': {'displayName': name, 'location': location, 'name': nickname, 'abbreviation': abbreviation},
		'stats': [{'name': 'wins', 'value': 99}, {'name': 'overall', 'type': 'total', 'summary': record}]}


class StandingsTests(unittest.TestCase):
	def test_nhl_division_and_highlight(self):
		data = {'standings': [
			{'teamName': {'default': 'Chicago Blackhawks'}, 'teamCommonName': {'default': 'Blackhawks'}, 'teamAbbrev': {'default': 'CHI'}, 'divisionName': 'Central', 'divisionSequence': 2, 'points': 9},
			{'teamName': {'default': 'Dallas Stars'}, 'teamCommonName': {'default': 'Stars'}, 'teamAbbrev': {'default': 'DAL'}, 'divisionName': 'Central', 'divisionSequence': 1, 'points': 12},
			{'teamName': {'default': 'Detroit Red Wings'}, 'teamCommonName': {'default': 'Red Wings'}, 'teamAbbrev': {'default': 'DET'}, 'divisionName': 'Atlantic', 'divisionSequence': 1, 'points': 11},
		]}
		with patch.object(standings, '_fetch', return_value=data): rows, heading, metric = standings.get('nhl', 'Blackhawks', 'Red Wings')
		self.assertEqual((heading, metric), ('NHL · Central Division', 'PTS'))
		self.assertEqual([(r['position'], r['team'], r['points'], r['highlight']) for r in rows], [(1, 'Dallas Stars', '12', False), (2, 'Chicago Blackhawks', '9', True)])

	def test_nfl_source_order_and_both_highlights(self):
		data = {'children': [{'name': 'NFC', 'standings': {'entries': [team('Green Bay Packers', 'Green Bay', 'Packers', 'GB', '5-2'), team('Carolina Panthers', 'Carolina', 'Panthers', 'CAR', '4-3-1')]}}]}
		with patch.object(standings, '_fetch', return_value=data): rows, heading, metric = standings.get('nfl', 'Panthers', 'Packers')
		self.assertEqual((heading, metric), ('NFL · NFC', 'W-L-T'))
		self.assertEqual([(r['points'], r['highlight']) for r in rows], [('5-2-0', True), ('4-3-1', True)])

	def test_ncaa_nested_division(self):
		data = {'children': [{'name': 'Sun Belt', 'children': [{'name': 'Sun Belt - West', 'standings': {'entries': [team('Troy Trojans', 'Troy', 'Trojans', 'TROY', '5-2')]}},
			{'name': 'Sun Belt - East', 'standings': {'entries': [team('James Madison Dukes', 'James Madison', 'Dukes', 'JMU', '6-1')]}}]}]}
		with patch.object(standings, '_fetch', return_value=data): rows, heading, metric = standings.get('ncaa', 'Troy', 'James Madison')
		self.assertEqual((heading, metric), ('College football · Sun Belt - West', 'OVERALL'))
		self.assertEqual([(r['team'], r['highlight']) for r in rows], [('Troy Trojans', True)])

	def test_ambiguous_team_does_not_choose_wrong_section(self):
		data = {'children': [{'name': 'One', 'standings': {'entries': [team('First Eagles', 'First', 'Eagles', 'ONE', '5-2')]}},
			{'name': 'Two', 'standings': {'entries': [team('Second Eagles', 'Second', 'Eagles', 'TWO', '4-3')]}}]}
		with patch.object(standings, '_fetch', return_value=data):
			self.assertEqual(standings.get('ncaa', 'Eagles', 'Unknown')[0], [])
			self.assertEqual(standings.get('ncaa', 'Eagles', 'Second')[1], 'College football · Two')

	def test_invalid_or_unavailable_response_is_empty(self):
		for result in ({}, {'children': [{'name': 'NFC', 'standings': {'entries': [team('Bad', 'Bad', 'Bad', 'BAD', 'bad')]}}]}, None):
			with self.subTest(result=result), patch.object(standings, '_fetch', return_value=result): self.assertEqual(standings.get('nfl', 'Bad', '')[0], [])
		with patch.object(standings, '_fetch', side_effect=TimeoutError()): self.assertEqual(standings.get('nfl', 'Packers', '')[0], [])

	def test_all_nhl_divisions_require_eight_teams(self):
		data = {'standings': [{'teamName': {'default': 'Team ' + str(rank)}, 'divisionName': 'Central', 'divisionSequence': rank, 'points': 20 - rank} for rank in range(1, 8)]}
		with patch.object(standings, '_fetch', return_value=data): groups, metric = standings.get_all('nhl')
		self.assertEqual((groups, metric), ([('NHL · Central Division (unavailable)', [])], 'PTS'))

	def test_all_nfl_reports_incomplete_division_data(self):
		conference = {'children': [{'name': 'American Football Conference', 'standings': {'entries': [team('Buffalo Bills', 'Buffalo', 'Bills', 'BUF', '5-2')]}},
			{'name': 'National Football Conference', 'standings': {'entries': [team('Green Bay Packers', 'Green Bay', 'Packers', 'GB', '4-3')]}}]}
		divisions = {'children': [{'name': 'AFC ' + name, 'standings': {'entries': [team('Example %s %d' % (name, n), name, name, name, '5-2') for n in range(4)]}} for name in ('East', 'West', 'North', 'South')]}
		with patch.object(standings, '_fetch', side_effect=lambda key: {'nfl': conference, 'nfl_afc': divisions, 'nfl_nfc': {}}[key]): groups, metric = standings.get_all('nfl')
		self.assertEqual(metric, 'W-L-T')
		self.assertEqual(len(groups), 6)
		self.assertIn('divisions unavailable', groups[1][0])
		self.assertEqual([g[0] for g in groups[2:]], ['NFL · AFC East', 'NFL · AFC West', 'NFL · AFC North', 'NFL · AFC South'])

	def test_all_nfl_rejects_missing_and_duplicate_division_teams(self):
		conference = {'children': [{'name': 'American Football Conference', 'standings': {'entries': [team('Buffalo Bills', 'Buffalo', 'Bills', 'BUF', '5-2')]}}]}
		for duplicate in (False, True):
			divisions = {'children': [{'name': 'AFC ' + name, 'standings': {'entries': [team('Example %s %d' % (name, n), name, name, name, '5-2') for n in range(4)]}} for name in ('East', 'West', 'North', 'South')]}
			entries = divisions['children'][0]['standings']['entries']
			if duplicate: entries[-1] = entries[0]
			else: entries.pop()
			with self.subTest(duplicate=duplicate), patch.object(standings, '_fetch', side_effect=lambda key: conference if key == 'nfl' else divisions if key == 'nfl_afc' else {}):
				groups, _ = standings.get_all('nfl')
				self.assertEqual(len(groups), 1)
				self.assertIn('divisions unavailable', groups[0][0])

	def test_all_nhl_marks_sections_with_duplicate_or_invalid_ranks_unavailable(self):
		for rank in (1, 0):
			data = {'standings': [
				{'teamName': {'default': 'First'}, 'divisionName': 'Central', 'divisionSequence': 1, 'points': 9},
				{'teamName': {'default': 'Second'}, 'divisionName': 'Central', 'divisionSequence': rank, 'points': 8},
				{'teamName': {'default': 'Third'}, 'divisionName': 'Atlantic', 'divisionSequence': 1, 'points': 7},
			]}
			data['standings'].extend({'teamName': {'default': 'Atlantic %d' % i}, 'divisionName': 'Atlantic', 'divisionSequence': i, 'points': 7} for i in range(2, 9))
			with self.subTest(rank=rank), patch.object(standings, '_fetch', return_value=data):
				groups, _ = standings.get_all('nhl')
				self.assertEqual([heading for heading, _ in groups], ['NHL · Central Division (unavailable)', 'NHL · Atlantic Division'])
				self.assertEqual(groups[0][1], [])

	def test_all_ncaa_sections_and_failure(self):
		data = {'children': [{'name': 'Sun Belt', 'children': [{'name': 'Sun Belt - West', 'standings': {'entries': [team('Troy Trojans', 'Troy', 'Trojans', 'TROY', '5-2')]}},
			{'name': 'Sun Belt - East', 'standings': {'entries': [team('James Madison Dukes', 'James Madison', 'Dukes', 'JMU', '6-1')]}}]}]}
		with patch.object(standings, '_fetch', return_value=data): groups, metric = standings.get_all('ncaa')
		self.assertEqual(metric, 'OVERALL')
		self.assertEqual([g[0] for g in groups], ['College football · Sun Belt - West', 'College football · Sun Belt - East'])
		with patch.object(standings, '_fetch', side_effect=TimeoutError()): self.assertEqual(standings.get_all('ncaa'), ([], 'OVERALL'))

	def test_all_groups_keep_same_named_branches_separate(self):
		data = {'children': [{'name': parent, 'children': [{'name': 'East', 'standings': {'entries': [team(parent + ' Team', parent, 'Team', parent, '5-2')]}}]} for parent in ('Conference One', 'Conference Two')]}
		with patch.object(standings, '_fetch', return_value=data): groups, _ = standings.get_all('ncaa')
		self.assertEqual([heading for heading, _ in groups], ['College football · Conference One · East', 'College football · Conference Two · East'])
		self.assertEqual([rows[0]['team'] for _, rows in groups], ['Conference One Team', 'Conference Two Team'])

	def test_nhl_all_groups_highlight_teams_in_different_divisions(self):
		data = {'standings': []}
		for group in ('Atlantic', 'Central', 'Pacific', 'Metropolitan'):
			for rank in range(1, 9):
				team_name = ('Boston Bruins' if group == 'Atlantic' else 'Chicago Blackhawks' if group == 'Central' else group + ' Team') if rank == 1 else group + ' Team ' + str(rank)
				data['standings'].append({'teamName': {'default': team_name}, 'teamCommonName': {'default': team_name.split()[-1]}, 'divisionName': group, 'divisionSequence': rank, 'points': 20 - rank})
		with patch.object(standings, '_fetch', return_value=data): groups, metric = standings.get_nhl_groups('Bruins', 'Blackhawks')
		self.assertEqual((len(groups), metric), (4, 'PTS'))
		self.assertEqual([row['team'] for _, rows in groups for row in rows if row['highlight']], ['Boston Bruins', 'Chicago Blackhawks'])
		data['standings'].pop()
		with patch.object(standings, '_fetch', return_value=data):
			groups, _ = standings.get_nhl_groups('Bruins', 'Blackhawks')
			self.assertEqual(groups[-1], ('Metropolitan Division (unavailable)', []))
			self.assertEqual(len(groups[0][1]), 8)

	def test_nfl_groups_mark_teams_and_partial_outage(self):
		divisions = {'children': [{'name': 'AFC ' + name, 'standings': {'entries': [team('Team %s %d' % (name, i), 'Town %s %d' % (name, i), 'Team %d' % i, 'T%d%s' % (i, name), '5-2') for i in range(4)]}} for name in ('East', 'North', 'South', 'West')]}
		with patch.object(standings, '_fetch', side_effect=lambda key: divisions if key == 'nfl_afc' else (_ for _ in ()).throw(TimeoutError())):
			groups, metric = standings.get_nfl_groups('Team East 0', '')
		self.assertEqual((len(groups), metric), (8, 'W-L-T'))
		self.assertTrue(groups[0][1][0]['highlight'])
		self.assertEqual(groups[4], ('NFC East (unavailable)', []))

	def test_ncaa_incomplete_section_is_not_renumbered(self):
		data = {'children': [{'name': 'Sun Belt', 'standings': {'entries': [team('Troy Trojans', 'Troy', 'Trojans', 'TROY', '5-2'), {'team': {}, 'stats': []}]}}]}
		with patch.object(standings, '_fetch', return_value=data): groups, _ = standings.get_ncaa_groups('Troy', 'Unknown')
		self.assertEqual(groups, [('Sun Belt (unavailable)', [])])

	def test_empty_college_section_is_explicitly_unavailable(self):
		data = {'children': [{'name': 'West', 'standings': {'entries': [team('Troy Trojans', 'Troy', 'Trojans', 'TROY', '5-2')]}}, {'name': 'East', 'standings': {'entries': []}}]}
		with patch.object(standings, '_fetch', return_value=data):
			self.assertEqual(standings.get_ncaa_groups('Troy', '')[0][1], ('East (unavailable)', []))
			self.assertEqual(standings.get_all('ncaa')[0][1], ('College football · East (unavailable)', []))

	def test_malformed_college_leaf_is_unavailable_but_parent_is_not_a_section(self):
		data = {'children': [{'name': 'Sun Belt', 'children': [{'name': 'West', 'standings': {'entries': [team('Troy Trojans', 'Troy', 'Trojans', 'TROY', '5-2')]}},
			{'name': 'East'}, {'name': 'Central', 'standings': {'entries': None}}]}]}
		with patch.object(standings, '_fetch', return_value=data): groups, _ = standings.get_ncaa_groups('Troy', '')
		self.assertEqual([name for name, _ in groups], ['West', 'East (unavailable)', 'Central (unavailable)'])

	def test_partial_college_payload_is_not_cached(self):
		standings._cache.clear()
		data = {'children': [{'name': 'West', 'standings': {'entries': [team('Troy Trojans', 'Troy', 'Trojans', 'TROY', '5-2')]}}, {'name': 'East', 'standings': {'entries': []}}]}
		payload = json.dumps(data).encode()
		with patch.object(standings, 'urlopen', side_effect=lambda *args, **kwargs: BytesIO(payload)) as fetch:
			standings._fetch('ncaa')
			standings._fetch('ncaa')
		self.assertEqual(fetch.call_count, 2)
		standings._cache.clear()

	def test_cache_expires_and_is_per_league(self):
		standings._cache.clear()
		payload = json.dumps({'children': [{'name': 'NFC', 'standings': {'entries': [team('Green Bay Packers', 'Green Bay', 'Packers', 'GB', '5-2')]}}]}).encode()
		with patch.object(standings.time, 'monotonic', side_effect=[0, 0, 299, 301, 301, 301, 301]), patch.object(standings, 'urlopen', side_effect=lambda *args, **kwargs: BytesIO(payload)) as fetch:
			standings._fetch('nfl')
			standings._fetch('nfl')
			standings._fetch('nfl')
			standings._fetch('ncaa')
		self.assertEqual(fetch.call_count, 3)
		standings._cache.clear()

	def test_incomplete_nfl_divisions_are_not_cached(self):
		standings._cache.clear()
		partial = json.dumps({'children': [{'name': 'AFC East', 'standings': {'entries': [team('Bills', 'Buffalo', 'Bills', 'BUF', '5-2')]}}]}).encode()
		full = json.dumps({'children': [{'name': 'AFC ' + name, 'standings': {'entries': [team('Team %s %d' % (name, i), name, str(i), str(i), '5-2') for i in range(4)]}} for name in ('East', 'North', 'South', 'West')]}).encode()
		with patch.object(standings, 'urlopen', side_effect=[BytesIO(partial), BytesIO(full)]) as fetch:
			with self.assertRaises(ValueError): standings._fetch('nfl_afc')
			standings._fetch('nfl_afc')
		self.assertEqual(fetch.call_count, 2)
		standings._cache.clear()

	def test_failed_payloads_are_not_cached(self):
		standings._cache.clear()
		valid = json.dumps({'children': [{'name': 'NFC', 'standings': {'entries': [team('Green Bay Packers', 'Green Bay', 'Packers', 'GB', '5-2')]}}]}).encode()
		with patch.object(standings, 'urlopen', side_effect=[TimeoutError(), BytesIO(b'{}'), BytesIO(b'{"children":[{}]}'), BytesIO(valid)]) as fetch:
			with self.assertRaises(TimeoutError): standings._fetch('nfl')
			with self.assertRaises(ValueError): standings._fetch('nfl')
			with self.assertRaises(ValueError): standings._fetch('nfl')
			standings._fetch('nfl')
		self.assertEqual(fetch.call_count, 4)
		standings._cache.clear()


if __name__ == '__main__': unittest.main()
