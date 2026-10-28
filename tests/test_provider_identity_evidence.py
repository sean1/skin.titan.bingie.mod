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


	def test_torz_rejects_wrong_episode_and_collections_without_exact_file(self):
		module = load_provider('torz', FakeSourceUtils())
		for files in ([], None, [{'sid': 'tt999:1:2'}], [{'sid': 'tt123:1:1'}], [{'p': '/Show.S01E02.mkv'}]):
			with self.subTest(files=files): self.assertIsNone(module.source._identity_evidence({'files': files}, 'tt123', 1, 2))
		self.assertNotIn('imdb_id', module.source._identity_evidence({}, 'tt123', 1, 2))


if __name__ == '__main__':
	unittest.main()
