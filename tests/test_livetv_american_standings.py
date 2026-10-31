import unittest
import json
from io import BytesIO
from unittest.mock import patch
from resources.lib.modules import livetv_american_standings as standings


def team(name, location, nickname, abbreviation, record):
	return {'team': {'displayName': name, 'location': location, 'name': nickname, 'abbreviation': abbreviation},
		'stats': [{'name': 'wins', 'value': 99}, {'name': 'overall', 'type': 'total', 'summary': record}]}


def nba_entries(first):
	entries = [first] + [team('Example Team %d' % n, 'City %d' % n, 'Team %d' % n, 'T%d' % n, '5-5') for n in range(2, 16)]
	for seed, entry in enumerate(entries, 1): entry['stats'].append({'name': 'playoffSeed', 'value': seed})
	return entries


class StandingsTests(unittest.TestCase):


	def test_nba_conferences_show_records_and_highlight_both_teams(self):
		data = {'children': [{'name': 'Eastern Conference', 'standings': {'entries': nba_entries(team('Boston Celtics', 'Boston', 'Celtics', 'BOS', '8-2'))}},
			{'name': 'Western Conference', 'standings': {'entries': nba_entries(team('Los Angeles Lakers', 'Los Angeles', 'Lakers', 'LAL', '6-4'))}}]}
		with patch.object(standings, '_fetch', return_value=data):
			groups, metric = standings.get_nba_groups('Celtics', 'Lakers')
			all_groups, all_metric = standings.get_all('nba')
		self.assertEqual(metric, 'W-L')
		self.assertEqual([(name, [(row['team'], row['points']) for row in rows if row['highlight']]) for name, rows in groups],
			[('Eastern Conference', [('Boston Celtics', '8-2')]), ('Western Conference', [('Los Angeles Lakers', '6-4')])])
		self.assertEqual((all_metric, [name for name, _ in all_groups]), ('W-L', ['NBA · Eastern Conference', 'NBA · Western Conference']))


	def test_nba_uses_playoff_seed_instead_of_response_order(self):
		entries = nba_entries(team('Boston Celtics', 'Boston', 'Celtics', 'BOS', '8-2'))
		data = {'children': [{'name': 'Eastern Conference', 'standings': {'entries': list(reversed(entries))}}]}
		with patch.object(standings, '_fetch', return_value=data):
			for groups in (standings.get_nba_groups('Celtics', 'Nets')[0], standings.get_all('nba')[0]):
				self.assertEqual([(row['position'], row['team']) for row in groups[0][1]][:2], [(1, 'Boston Celtics'), (2, 'Example Team 2')])
			entries[1]['stats'][-1]['value'] = 1
			self.assertEqual(standings.get_nba_groups('Celtics', 'Nets')[0][0], ('Eastern Conference (unavailable)', []))
			entries[1]['stats'][-1]['value'] = 2
			data['children'][0]['standings']['entries'].pop(0)
			self.assertEqual(standings.get_nba_groups('Celtics', 'Nets')[0], [('Eastern Conference (unavailable)', []), ('Western Conference (unavailable)', [])])


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
