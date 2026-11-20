import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.module_isolation import temporary_modules

MODULES_LIB = str(Path(__file__).resolve().parents[1] / 'resources' / 'lib')


class F1ManifestCandidateTests(unittest.TestCase):
	def test_verified_monaco_manifest_keeps_pack_verification_through_index_failures_and_duplicates(self):
		fixture = {'event_id': '600057434', 'date': '2026-06-05', 'race_name': 'Monaco Grand Prix', 'race_round': 6, 'session_code': 'FP2'}
		stubs = {
			'modules.debrid': SimpleNamespace(debrid_enabled=lambda: ['realdebrid', 'alldebrid'], debrid_type_enabled=lambda kind, providers: providers),
			'modules.kodi_utils': SimpleNamespace(),
			'modules.settings': SimpleNamespace(),
			'modules.source_utils': SimpleNamespace(supported_video_extensions=lambda: ['.mkv']),
			'modules.f1_knaben': SimpleNamespace(search=Mock(side_effect=OSError), search_season=Mock(side_effect=OSError)),
			'modules.f1_blackhole': SimpleNamespace(search=Mock(side_effect=OSError)),
			'modules.f1_torrentdownload': SimpleNamespace(search=Mock(side_effect=OSError)),
		}
		with temporary_modules(stubs, isolate=('modules.f1_debrid', 'modules.f1_pack_files', 'modules.livetv_f1_search', 'modules.f1_calendar')):
			sys.path.insert(0, MODULES_LIB)
			from modules import f1_debrid, livetv_f1_search

			hash_value = next(iter(livetv_f1_search._archive_rows('2026', fixture['event_id'])))
			duplicate = json.dumps([{'name': 'Formula 1 2026 R06 Monaco Grand Prix Practice 2', 'info_hash': hash_value}]).encode()
			for response in (Mock(side_effect=OSError), lambda *args, **kwargs: BytesIO(duplicate)):
				status = []
				with patch.object(livetv_f1_search, 'urlopen', side_effect=response):
					candidates = livetv_f1_search.search(fixture, status)
					self.assertEqual(len(candidates), 1)
					self.assertEqual(candidates[0]['pack'], 'unlabelled')
					verified = f1_debrid.cached_pack_candidates(candidates, fixture)
					self.assertEqual(f1_debrid.cached_pack_candidates(candidates, dict(fixture, session_code='FP4')), [])
				self.assertEqual(status, ['partial'])
				self.assertCountEqual([row['debrid'] for row in verified], ['realdebrid', 'alldebrid'])
				self.assertTrue(all(row['session_verified'] and row['availability'] == 'unknown' for row in verified))
			stale = {hash_value: {'name': 'Formula 1 2026 R07 Barcelona Grand Prix SkyUHD', 'info_hash': hash_value, 'files': ['Practice 2.mkv']}}
			with patch.object(livetv_f1_search, '_archive_rows', return_value=stale), patch.object(livetv_f1_search, 'urlopen', side_effect=lambda *args, **kwargs: BytesIO(duplicate)):
				results = livetv_f1_search.search(fixture)
			self.assertEqual([row['name'] for row in results], ['Formula 1 2026 R06 Monaco Grand Prix Practice 2'])
			self.assertEqual(results[0]['pack'], '')
	def test_public_manifest_budget_preserves_three_candidates_per_pack_class(self):
		fixture = {'date': '2026-11-08', 'race_name': 'São Paulo Grand Prix', 'race_round': 20, 'session_code': 'FP2'}
		press_conference = '01.F1.2026.R20.São.Paulo.Grand.Prix.Drivers.Press.Conference.SkyF1HD.1080P.mkv'
		practice_two = '02.F1.2026.R20.São.Paulo.Grand.Prix.Practice.Two.F1TV.UHD.2160p.Multi.mkv'
		pack_types = ('unlabelled',) * 4 + ('season',) * 4
		candidates = [{'hash': str(number) * 40, 'magnet': 'magnet:?xt=urn:btih:' + str(number) * 40, 'detail_path': '/' + str(number) * 40 + '/archive', 'pack': pack}
			for number, pack in enumerate(pack_types, 1)]
		manifests = {candidate['hash']: [practice_two if index in (3, 4, 7, 8) else press_conference] for index, candidate in enumerate(candidates, 1)}
		lookup = Mock(side_effect=lambda info_hash, detail_path: manifests[info_hash])
		stubs = {
			'modules.debrid': SimpleNamespace(debrid_enabled=lambda: ['realdebrid'], debrid_type_enabled=lambda kind, providers: providers),
			'modules.kodi_utils': SimpleNamespace(),
			'modules.settings': SimpleNamespace(),
			'modules.source_utils': SimpleNamespace(supported_video_extensions=lambda: ['.mkv']),
			'modules.f1_torrentdownload': SimpleNamespace(manifest_files=lookup),
		}
		with temporary_modules(stubs, isolate=('modules.f1_debrid', 'modules.f1_pack_files', 'modules.livetv_f1_search', 'modules.f1_calendar')):
			sys.path.insert(0, MODULES_LIB)
			from modules import f1_debrid

			results = f1_debrid.cached_pack_candidates(candidates, fixture)

		self.assertCountEqual([entry.args[0] for entry in lookup.call_args_list], [candidates[index]['hash'] for index in (0, 1, 2, 4, 5, 6)])
		self.assertCountEqual([row['hash'] for row in results if row.get('session_verified')], [candidates[2]['hash'], candidates[6]['hash']])
		self.assertEqual([row['hash'] for row in results if row['pack'] == 'unlabelled'], [candidates[2]['hash']])
		self.assertTrue(all(row['availability'] == 'unknown' for row in results))
