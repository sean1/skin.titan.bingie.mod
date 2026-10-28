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

	def test_cancellation_during_stream_closes_response(self):
		response = BinaryResponse((b'abc', b'def'))
		self.request.return_value = response
		checks = Mock(side_effect=(False, False, False, True))

		with self.assertRaises(self.providers.ProviderCancelled):
			self.providers._download_binary('https://download.invalid/subtitle', 'test', 20, checks)

		response.close.assert_called_once_with()

	def test_non_success_and_empty_responses_are_rejected_and_closed(self):
		for response, category in ((BinaryResponse(status_code=302), 'http_302'), (BinaryResponse(status_code=404), 'http_404'), (BinaryResponse(), 'empty_response')):
			with self.subTest(category=category):
				self.request.reset_mock()
				self.request.return_value = response
				with self.assertRaisesRegex(self.providers.ProviderError, category):
					self.providers._download_binary('https://download.invalid/subtitle', 'test', 20)
				response.close.assert_called_once_with()


	def test_retryable_status_retries_without_reading_first_body(self):
		first = BinaryResponse((b'error body',), status_code=503)
		second = BinaryResponse((b'subtitle',))
		self.request.side_effect = (first, second)

		content = self.providers._download_binary('https://download.invalid/subtitle', 'test', 20)

		self.assertEqual(content, b'subtitle')
		self.assertIsNone(first.chunk_size)
		first.close.assert_called_once_with()
		self.assertEqual(self.request.call_count, 2)


if __name__ == '__main__':
	unittest.main()
