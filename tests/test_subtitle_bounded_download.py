import unittest
from unittest.mock import Mock

from tests.test_subtitle_providers import load_providers


class BinaryResponse:
	def __init__(self, chunks=(), status_code=200, headers=None, read_error=None):
		self.chunks = list(chunks)
		self.status_code = status_code
		self.ok = 200 <= status_code < 300
		self.headers = headers or {}
		self.read_error = read_error
		self.close = Mock()
		self.chunk_size = None

	def iter_content(self, chunk_size):
		self.chunk_size = chunk_size
		for chunk in self.chunks: yield chunk
		if self.read_error: raise self.read_error


class SubtitleBoundedDownloadTests(unittest.TestCase):
	def setUp(self):
		self.providers = load_providers()
		self.request = self.providers.requests.request


	def test_cumulative_cap_stops_stream_and_closes_response(self):
		response = BinaryResponse((b'abcd', b'efg', b'not-read'))
		self.request.return_value = response

		with self.assertRaisesRegex(self.providers.ProviderError, 'invalid_size'):
			self.providers._download_binary('https://download.invalid/subtitle', 'test', 6)

		response.close.assert_called_once_with()


if __name__ == '__main__':
	unittest.main()
