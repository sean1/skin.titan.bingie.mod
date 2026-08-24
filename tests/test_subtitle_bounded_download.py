import unittest
from unittest.mock import Mock, patch

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

	def test_success_streams_fixed_chunks_and_closes_response(self):
		response = BinaryResponse((b'abc', b'', b'def'), headers={'Content-Length': '6'})
		self.request.return_value = response

		content = self.providers._download_binary('https://download.invalid/subtitle', 'test', 6)

		self.assertEqual(content, b'abcdef')
		self.request.assert_called_once_with('GET', 'https://download.invalid/subtitle', timeout=self.providers.REQUEST_TIMEOUT, stream=True)
		self.assertEqual(response.chunk_size, 64 * 1024)
		response.close.assert_called_once_with()

	def test_content_length_over_cap_rejects_before_reading(self):
		response = BinaryResponse((b'not-read',), headers={'Content-Length': '7'})
		self.request.return_value = response

		with self.assertRaisesRegex(self.providers.ProviderError, 'invalid_size'):
			self.providers._download_binary('https://download.invalid/subtitle', 'test', 6)

		self.assertIsNone(response.chunk_size)
		response.close.assert_called_once_with()

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

	def test_connection_failure_retries_before_streaming(self):
		response = BinaryResponse((b'subtitle',))
		self.request.side_effect = (self.providers.requests.Timeout(), response)

		content = self.providers._download_binary('https://download.invalid/subtitle', 'test', 20)

		self.assertEqual(content, b'subtitle')
		self.assertEqual(self.request.call_count, 2)

	def test_retryable_status_retries_without_reading_first_body(self):
		first = BinaryResponse((b'error body',), status_code=503)
		second = BinaryResponse((b'subtitle',))
		self.request.side_effect = (first, second)

		content = self.providers._download_binary('https://download.invalid/subtitle', 'test', 20)

		self.assertEqual(content, b'subtitle')
		self.assertIsNone(first.chunk_size)
		first.close.assert_called_once_with()
		self.assertEqual(self.request.call_count, 2)

	def test_body_read_error_is_rejected_without_retry(self):
		response = BinaryResponse((b'partial',), read_error=OSError('connection dropped'))
		self.request.return_value = response

		with self.assertRaisesRegex(self.providers.ProviderError, 'read_OSError'):
			self.providers._download_binary('https://download.invalid/subtitle', 'test', 20)

		self.request.assert_called_once()
		response.close.assert_called_once_with()

	def test_providers_select_direct_and_archive_caps(self):
		opensubtitles = self.providers.OpenSubtitlesProvider({'api_key': 'key', 'user_agent': 'Test'}, {})
		opensubtitles.authenticated_json = Mock(return_value={'link': 'https://download.invalid/subtitle'})
		subdl = self.providers.SubDLProvider({'api_key': 'key'}, {})
		subsource = self.providers.SubSourceProvider({'api_key': 'key'}, {})

		with patch.object(self.providers, '_download_binary', return_value=b'subtitle') as download:
			self.assertIsNotNone(opensubtitles.download({'id': '1', 'extension': 'srt'}))
			self.assertIsNotNone(subdl.download({'id': 'file:2:3', 'extension': 'srt'}))
			download.assert_any_call('https://download.invalid/subtitle', 'opensubtitles', self.providers.MAX_SUBTITLE_BYTES, None)
			download.assert_any_call('https://dl.subdl.com/subtitle/2/3', 'subdl', self.providers.MAX_SUBTITLE_BYTES, None, headers={'X-API-Key': 'key'})

		archive_payload = {'content': b'subtitle', 'extension': 'srt'}
		with patch.object(self.providers, '_download_binary', return_value=b'archive') as download, patch.object(self.providers, 'extract_subtitle_archive', return_value=archive_payload):
			self.assertEqual(subdl.download({'id': 'archive:file.zip'}), archive_payload)
			self.assertEqual(subsource.download({'id': '4'}), archive_payload)
			download.assert_any_call('https://dl.subdl.com/subtitle/file.zip', 'subdl', self.providers.MAX_ARCHIVE_BYTES, None, headers={'X-API-Key': 'key'})
			download.assert_any_call('https://api.subsource.net/api/v1/subtitles/4/download', 'subsource', self.providers.MAX_ARCHIVE_BYTES, None, headers=subsource.headers())


if __name__ == '__main__':
	unittest.main()
