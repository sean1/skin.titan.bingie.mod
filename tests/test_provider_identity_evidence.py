import unittest

from tests.test_stremio_providers import FakeSourceUtils, load_provider


class ProviderIdentityEvidenceTests(unittest.TestCase):


	def test_torz_rejects_wrong_episode_and_collections_without_exact_file(self):
		module = load_provider('torz', FakeSourceUtils())
		for files in ([], None, [{'sid': 'tt999:1:2'}], [{'sid': 'tt123:1:1'}], [{'p': '/Show.S01E02.mkv'}]):
			with self.subTest(files=files): self.assertIsNone(module.source._identity_evidence({'files': files}, 'tt123', 1, 2))
		self.assertNotIn('imdb_id', module.source._identity_evidence({}, 'tt123', 1, 2))


if __name__ == '__main__':
	unittest.main()
