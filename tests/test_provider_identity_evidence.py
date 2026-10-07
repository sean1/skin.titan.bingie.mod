import unittest

from tests.test_stremio_providers import FakeRequests, FakeResponse, FakeSourceUtils, load_provider


class ProviderIdentityEvidenceTests(unittest.TestCase):
	def test_zilean_preserves_returned_identity_without_request_stamping(self):
		module = load_provider('zilean', FakeSourceUtils())
		row = {'info_hash': 'hash', 'raw_title': 'Show Season 1', 'imdb_id': 'tt999', 'imdb': {'imdbId': 'tt999', 'year': 2016}, 'year': 0, 'date': None}
		module.requests = FakeRequests(FakeResponse(payload=[row]))
		item = module.source().sources({'tvshowtitle': 'Show', 'title': 'Episode', 'aliases': [], 'total_seasons': '3', 'year': '2024', 'imdb': 'tt123', 'season': '1', 'episode': '2'}, {})[0]
		self.assertEqual(item['identity_evidence']['imdb_id'], 'tt999')
		self.assertEqual(item['identity_evidence']['catalogue_year'], 2016)
		self.assertNotIn('release_year', item['identity_evidence'])
		self.assertNotIn('imdb_id', module.source._identity_evidence({}))

	def test_zilean_preserves_conflicting_catalogue_ids_and_release_date(self):
		module = load_provider('zilean', FakeSourceUtils())
		evidence = module.source._identity_evidence({'imdb_id': 'tt999', 'imdb': {'imdbId': 'tt123'}, 'year': 2024, 'date': '2024-01-01'})
		self.assertEqual(evidence['catalogue_imdb_id'], 'tt123')
		self.assertEqual(evidence['release_year'], 2024)
		self.assertEqual(evidence['episode_date'], '2024-01-01')

	def test_torz_keeps_exact_file_evidence_and_index_zero(self):
		module = load_provider('torz', FakeSourceUtils())
		file = {'sid': 'tt123:1:2', 'i': 0, 'p': '/Show.S01E02.mkv', 'n': 'Show.S01E02.mkv', 'mi': {'video': {'h': 1080}}, 'src': 'dht'}
		evidence = module.source._identity_evidence({'files': [{'sid': 'tt123:1:2', 'p': '/Show.S01E02.srt'}, file]}, 'tt123', '01', '02')
		self.assertEqual(evidence['file_index'], 0)
		self.assertEqual(evidence['file_sid'], 'tt123:1:2')
		self.assertEqual(evidence['file_path'], file['p'])
		self.assertEqual(evidence['media_info'], file['mi'])

	def test_torz_rejects_wrong_episode_and_collections_without_exact_file(self):
		module = load_provider('torz', FakeSourceUtils())
		for files in ([], None, [{'sid': 'tt999:1:2'}], [{'sid': 'tt123:1:1'}], [{'p': '/Show.S01E02.mkv'}]):
			with self.subTest(files=files): self.assertIsNone(module.source._identity_evidence({'files': files}, 'tt123', 1, 2))
		self.assertNotIn('imdb_id', module.source._identity_evidence({}, 'tt123', 1, 2))


if __name__ == '__main__':
	unittest.main()
