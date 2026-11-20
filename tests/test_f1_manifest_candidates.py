import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from tests.module_isolation import temporary_modules

MODULES_LIB = str(Path(__file__).resolve().parents[1] / 'resources' / 'lib')


class F1ManifestCandidateTests(unittest.TestCase):
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
