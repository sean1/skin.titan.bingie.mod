import unittest

from tests.test_external_manager import load_debrid_module


class SourceIdentityResolutionTests(unittest.TestCase):
	def setUp(self):
		self.module = load_debrid_module()
		self.source = self.module.Source({}, {'imdb_id': 'tt0264235'})
		self.evidence = {'file_match_required': True, 'file_sid': 'tt0264235:33:1', 'file_path': 'Top Gear/Top.Gear.S33E01.mkv', 'file_name': 'Top.Gear.S33E01.mkv', 'file_index': 4}

	def test_exact_provider_file_used_instead_of_first_episode_match(self):
		wrong = {'filename': 'Top.Gear.S33E01.alternate.mkv', 'link': 'wrong'}
		correct = {'filename': 'Top.Gear.S33E01.mkv', 'link': 'correct'}
		self.assertEqual(self.source._identity_matched_files([wrong, correct], self.evidence, 33, 1), [correct])

	def test_real_debrid_flattened_path_matches(self):
		correct = {'filename': 'Top GearTop.Gear.S33E01.mkv'}
		self.assertEqual(self.source._identity_matched_files([correct], self.evidence, 33, 1), [correct])

	def test_missing_file_does_not_fall_back_to_episode_number(self):
		with self.assertRaises(ValueError): self.source._identity_matched_files([{'filename': 'Top.Gear.S33E01.alternate.mkv'}], self.evidence, 33, 1)

	def test_duplicate_filenames_fail_safely(self):
		with self.assertRaises(ValueError): self.source._identity_matched_files([{'filename': 'Top.Gear.S33E01.mkv'}] * 2, self.evidence, 33, 1)

	def test_stale_episode_identity_fails_safely(self):
		with self.assertRaises(ValueError): self.source._identity_matched_files([{'filename': 'Top.Gear.S33E01.mkv'}], self.evidence, 33, 2)

	def test_index_alone_cannot_be_mapped_to_filtered_debrid_list(self):
		evidence = {'file_match_required': True, 'file_sid': 'tt0264235:33:1', 'file_index': 0}
		with self.assertRaises(ValueError): self.source._identity_matched_files([{'filename': 'Top.Gear.S33E01.mkv'}], evidence, 33, 1)
