import sys
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.module_isolation import temporary_modules

MODULES_LIB = str(Path(__file__).resolve().parents[1] / 'resources' / 'lib')

with temporary_modules(isolate=('modules.f1_calendar', 'modules.livetv_f1_search', 'modules.f1_pack_files')):
	sys.path.insert(0, MODULES_LIB)
	from modules import f1_pack_files, livetv_f1_search


class F1ReplayMatchingTests(unittest.TestCase):
	def test_practice_search_matches_unseeded_titles_by_venue_and_session(self):
		fixture = {'date': '2026-06-26', 'race_name': 'Austrian Grand Prix', 'race_round': 8, 'aliases': ['Austria'], 'session_code': 'FP3'}
		correct = 'Formula 1 2026 Austria Rd 08 FP3 1080P Multi-Audio F1TV WEB-DL AAC2.0 H.265 F1GP'
		wrong_sessions = [{'name': 'Formula1.2026.Austrian.Grand.Prix.Practice.One.HLG.2160p', 'info_hash': '2' * 40}]
		wrong_venue = {'name': 'Formula 1 2026 Belgian Grand Prix Rd 10 FP3 1080p', 'info_hash': '3' * 40}
		practice_results = {'Formula 1 2026 practice': wrong_sessions, 'F1 2026 fp3': [{'name': correct, 'info_hash': '1' * 40}, wrong_venue]}
		knaben_search = Mock(side_effect=lambda query, include_dead=False: practice_results.get(query, []) if include_dead else [])
		stubs = {
			'modules.f1_knaben': SimpleNamespace(search=knaben_search, search_season=lambda year: ([], False)),
			'modules.f1_blackhole': SimpleNamespace(search=lambda query: ([], False)),
			'modules.f1_torrentdownload': SimpleNamespace(search=lambda query: []),
		}
		with temporary_modules(stubs), patch.object(livetv_f1_search, 'urlopen', side_effect=lambda *args, **kwargs: BytesIO(b'[]')):
			sys.path.insert(0, MODULES_LIB)
			results = livetv_f1_search.search(fixture)
		self.assertEqual([result['name'] for result in results], [correct])
		knaben_search.assert_any_call('Formula 1 2026 practice', include_dead=True)
		knaben_search.assert_any_call('F1 2026 fp3', include_dead=True)

	def test_2002_monaco_practice_uses_day_and_position(self):
		fixture = {'race_name': 'Monaco Grand Prix', 'race_round': 7, 'split_daily_practice': True}
		for number, slot in enumerate(('Thursday First', 'Thursday Second', 'Saturday First', 'Saturday Second'), 1):
			weekday = 3 if number <= 2 else 5
			date = '2002-05-23' if weekday == 3 else '2002-05-25'
			selected = dict(fixture, date=date, session_code=f'FP{number}', practice_weekday=weekday)
			title = f'Formula 1 2002 Monaco Grand Prix {slot} Free Practice'
			with self.subTest(slot=slot):
				self.assertTrue(livetv_f1_search._match(title, selected))
				self.assertFalse(livetv_f1_search._match(f'Formula 1 2002 Monaco Grand Prix FP{number}', selected))
				self.assertFalse(livetv_f1_search._match(title.replace(slot.split()[0], 'Friday'), selected))
				other = 'Second' if 'First' in slot else 'First'
				self.assertFalse(livetv_f1_search._match(title.replace(slot.split()[1], other), selected))
				self.assertFalse(livetv_f1_search._match(title.replace(slot, 'Post ' + slot), selected))
				self.assertFalse(livetv_f1_search._match(f'{title} and {other} Practice', selected))
				files = [{'filename': f'{slot} Free Practice/{title}.mkv'}]
				self.assertEqual(f1_pack_files.matching_files(files, selected, 'weekend'), files)

	def test_2001_belgium_second_saturday_practice_keeps_its_identity(self):
		fixture = {'date': '2001-09-01', 'race_name': 'Belgian Grand Prix', 'race_round': 14, 'session_code': 'FP4', 'split_daily_practice': True, 'practice_weekday': 5}
		title = 'Formula 1 2001 Belgian Grand Prix Saturday Second Free Practice'
		self.assertTrue(livetv_f1_search._match(title, fixture))
		self.assertFalse(livetv_f1_search._match(title.replace('Second', 'First'), fixture))

	def test_2013_australian_qualifying_editions_stay_separate(self):
		fixture = {'date': '2013-03-16', 'race_name': 'Australian Grand Prix', 'race_round': 1, 'session_code': 'QUAL'}
		base = 'Formula 1 2013 Australian Grand Prix'
		listings = {
			'full': [f'{base} Qualifying'],
			'q1': [f'{base}-Qualifying-Q1-Only', f'{base} Saturday Qualifying'],
			'q2-q3': [f'{base}-Qualifying-Q2-and-Q3', f'{base} Qualifying - Q2 & Q3 -', f'{base} Sunday Qualifying', f'{base} Qualifying Sunday'],
		}
		for portion, titles in listings.items():
			selected = dict(fixture, qualifying_portion=portion)
			for listing_portion, candidates in listings.items():
				for title in candidates:
					with self.subTest(portion=portion, title=title):
						self.assertEqual(livetv_f1_search._match(title, selected), portion == listing_portion)
						self.assertFalse(livetv_f1_search._match(title.replace('Australian', 'Malaysian'), selected))
			self.assertFalse(livetv_f1_search._match(f'{base} Qualifying Q2', selected))
			self.assertFalse(livetv_f1_search._match(f'{base} Qualifying Q2 and Q3 Highlights', selected))
			self.assertFalse(livetv_f1_search._match(f'{base} Qualifying Q1 Only Sunday', selected))
			self.assertFalse(livetv_f1_search._match(f'{base} Qualifying Q2 and Q3 Saturday', selected))
			self.assertFalse(livetv_f1_search._match(f'{base} Saturday Qualifying Sunday', selected))
		files = [{'filename': 'Q2 and Q3/Qualifying Q2 and Q3.mp4'}]
		self.assertEqual(f1_pack_files.matching_files(files, dict(fixture, qualifying_portion='q2-q3'), 'weekend'), files)
		self.assertEqual(f1_pack_files.matching_files(files, dict(fixture, qualifying_portion='q1'), 'weekend'), [])

	def test_canadian_manifest_bare_practice_requires_single_practice_and_exact_round(self):
		practice = '03.F1.2026.R05.Canadian.Grand.Prix.Practice.Sky.Sports.F1.UHD.2160p.mkv'
		fixture = {'date': '2026-05-22', 'race_name': 'Canadian Grand Prix', 'race_round': 5, 'session_code': 'FP1', 'single_practice': True}
		files = [{'filename': practice}]
		self.assertEqual(f1_pack_files.matching_files(files, fixture, 'weekend'), files)
		self.assertEqual(f1_pack_files.matching_files(files, dict(fixture, single_practice=False), 'weekend'), [])
		for filename in (practice.replace('R05.', ''), practice.replace('R05.', 'R06.'), practice.replace('Canadian', 'Miami'), practice.replace('Practice.', 'Practice.Highlights.')):
			with self.subTest(filename=filename):
				self.assertEqual(f1_pack_files.matching_files([{'filename': filename}], fixture, 'weekend'), [])

	def test_named_multi_episodes_match_bahrain_not_their_episode_number(self):
		fixture = {'date': '2026-10-04', 'race_name': 'Bahrain Grand Prix in Malaysia', 'race_round': 16, 'aliases': ['Bahrain', 'Sepang', 'Malaysia']}
		for session, episode, label in (('RACE', 89, 'Race'), ('QUAL', 88, 'Qualifying')):
			with self.subTest(session=session):
				selected = dict(fixture, session_code=session)
				title = f'Formula.1.2026x{episode}.Bahrain.{label}.MULTi.1080p.SS'
				self.assertTrue(livetv_f1_search._match(title, selected))
				self.assertFalse(livetv_f1_search._match(title.replace('.Bahrain.', '.R17.Bahrain.'), selected))
				self.assertFalse(livetv_f1_search._match(title.replace('.Bahrain.', '.'), selected))

	def test_miami_sprint_inventory_routes_all_five_sessions_to_file_matching(self):
		listings = (
			('2024-05-05', 'Formula 1. Season 2024. Round 6. Miami Grand Prix. Practice. Sprint Qualifying. Sprint. Qualifying. Race. F1TV'),
			('2025-05-04', 'Formula 1 / Stage 06 / Miami Grand Prix / Practice, Sprint Qualification, Sprint, Qualification, Race / F1 TV / Season 2025'),
		)
		for date, title in listings:
			fixture = {'date': date, 'race_name': 'Miami Grand Prix', 'race_round': 6}
			for code in ('FP1', 'SS', 'SR', 'QUAL', 'RACE'):
				with self.subTest(date=date, session=code):
					selected = dict(fixture, session_code=code)
					self.assertEqual(livetv_f1_search.pack_type(title, selected), 'weekend')
					self.assertFalse(livetv_f1_search._match(title, selected))
			self.assertEqual(livetv_f1_search.pack_type(title, dict(fixture, session_code='FP2')), '')

	def test_miami_sprint_inventory_rejects_conflicting_stage_and_round(self):
		title = 'Formula 1 / Stage 06 / Miami Grand Prix / Practice, Sprint Qualification, Sprint, Qualification, Race / F1 TV / Season 2025'
		fixture = {'date': '2025-05-04', 'race_name': 'Miami Grand Prix', 'race_round': 6, 'session_code': 'RACE'}
		for listing in (title.replace('Stage 06', 'Stage 07'), title + ' / Stage 07 /', title + ' Round 7'):
			with self.subTest(listing=listing):
				self.assertEqual(livetv_f1_search.pack_type(listing, fixture), '')
				self.assertFalse(livetv_f1_search._match(listing, fixture))
