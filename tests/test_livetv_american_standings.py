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
