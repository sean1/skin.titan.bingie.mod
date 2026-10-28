import io
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests.test_livetv import load_livetv

ROOT = Path(__file__).resolve().parents[1]


class LiveTVGuideTests(unittest.TestCase):
	def setUp(self):
		self.live = load_livetv()
		(ROOT / 'tmp').mkdir(exist_ok=True)
		self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
		self.addCleanup(self.temp.cleanup)
		self.live.ku.profile_path = self.temp.name
		self.live.ku.addon_path = self.temp.name
		self.account = {'server': 'http://example.test', 'username': 'user', 'password': 'secret'}
		self.live._save(self.account)

	def test_xmltv_offsets_match_channel_ids_and_title(self):
		now = datetime(2026, 10, 28, 20, 30, tzinfo=timezone.utc).timestamp()
		xml = b'<tv><channel id="sports.example"/><programme channel="sports.example" start="20261028130000 -0700" stop="20261028140000 -0700"><title>Soccer</title></programme></tv>'
		guide = self.live._parse_guide(io.BytesIO(xml), now)
		self.assertEqual(guide['sports.example'], [[datetime(2026, 10, 28, 20, tzinfo=timezone.utc).timestamp(), datetime(2026, 10, 28, 21, tzinfo=timezone.utc).timestamp(), 'Soccer']])
		self.assertEqual(self.live._parse_guide(io.BytesIO(xml), now, {'different'}), {})

	def test_rejects_dtd_and_oversized_decompressed_guide(self):
		with self.assertRaises(ValueError): self.live._parse_guide(io.BytesIO(b'<!DOCTYPE tv [<!ENTITY x "bad">]><tv/>'), time.time())
		with self.assertRaises(ValueError): self.live._parse_guide(io.BytesIO(b'<!DOCTYPE tv SYSTEM "guide.dtd" [<!ENTITY x "bad">]><tv/>'), time.time())
		with self.assertRaises(ValueError): self.live._parse_guide(io.BytesIO(b'<!DOCTYPE tv SYSTEM "guide.dtd" [ ]><tv/>'), time.time())
		with self.assertRaises(ValueError): self.live._parse_guide(io.BytesIO(b'<!DOCTYPE tv PUBLIC "one" "two"><tv/>'), time.time())
		with self.assertRaises(ValueError): self.live._parse_guide(io.BytesIO('<!DOCTYPE tv><tv/>'.encode('utf-16')), time.time())
		with patch.object(self.live, '_GUIDE_XML_LIMIT', 10):
			with self.assertRaises(ValueError): self.live._parse_guide(io.BytesIO(b'<tv>1234567890</tv>'), time.time())


	def test_corrupt_raw_feed_is_refetched_without_using_partial_data(self):
		now = datetime(2026, 10, 28, 20, 30, tzinfo=timezone.utc).timestamp()
		url = 'http://example.test/guide'
		xml = b'<tv><programme channel="a" start="20261028200000 +0000" stop="20261028210000 +0000"><title>Match</title></programme></tv>'
		with patch.object(self.live, 'urlopen', return_value=io.BytesIO(xml)):
			self.live._download_guide(url, now, {'a'})
		path = next(Path(self.temp.name).glob('livetv-xmltv-*.cache'))
		path.write_bytes(b'<tv><programme')
		with patch.object(self.live, 'urlopen', return_value=io.BytesIO(xml)) as request:
			self.assertEqual(self.live._download_guide(url, now, {'a'})['a'][0][2], 'Match')
			self.assertEqual(request.call_count, 1)
		self.assertEqual(path.read_bytes(), xml)


	def test_corrupt_stale_raw_feed_is_not_used_on_outage(self):
		now = datetime(2026, 10, 28, 20, 30, tzinfo=timezone.utc).timestamp()
		with patch.object(self.live, 'urlopen', return_value=io.BytesIO(b'<tv/>')):
			self.live._download_guide('http://example.test/guide', now, {'a'})
		path = next(Path(self.temp.name).glob('livetv-xmltv-*.cache'))
		path.write_bytes(b'<tv>')
		old = time.time() - self.live._GUIDE_FRESH_SECONDS - 1
		os.utime(path, (old, old))
		with patch.object(self.live, 'urlopen', side_effect=OSError('offline')):
			with self.assertRaises(OSError): self.live._download_guide('http://example.test/guide', now, {'b'})
		self.live.ku.dialog.notification.assert_not_called()

	def test_cache_is_private_and_invalidates_when_guide_changes(self):
		with patch.object(self.live, '_download_guide', side_effect=[{'a': [[1, 2, 'one']]}, {'b': [[1, 2, 'two']]}]) as download:
			self.assertIn('a', self.live._guide_data(self.account))
			self.assertIn('a', self.live._guide_data(self.account))
			self.assertEqual(download.call_count, 1)
			self.assertIn('b', self.live._guide_data({**self.account, 'epg_url': 'http://other.test/guide?password=other'}))
		self.assertEqual(os.stat(self.live._guide_cache_path()).st_mode & 0o777, 0o600)
		with open(self.live._guide_cache_path(), encoding='utf-8') as cache: self.assertNotIn('password=other', cache.read())


	def test_stale_cached_guide_is_available_during_outage(self):
		with patch.object(self.live.time, 'time', return_value=10000), patch.object(self.live, '_download_guide', return_value={'sports': [[1, 2, 'Match']]}):
			self.live._guide_data(self.account)
		with patch.object(self.live.time, 'time', return_value=10000 + 3 * 3600), patch.object(self.live, '_download_guide', side_effect=OSError('secret server URL')):
			self.assertIn('sports', self.live._guide_data(self.account))
		message = str(self.live.ku.dialog.notification.call_args)
		self.assertNotIn('secret server URL', message)


	def test_custom_guide_url_is_hidden_and_cancel_keeps_existing_url(self):
		url = 'http://guide.test/xmltv.php?username=test&password=secret'
		self.live.ku.dialog.input.return_value = url
		self.live.guide_settings({})
		self.assertEqual(self.live._load()['epg_url'], url)
		self.assertEqual(self.live.ku.dialog.input.call_args.kwargs['option'], self.live.xbmcgui.ALPHANUM_HIDE_INPUT)
		self.live.ku.dialog.input.return_value = ''
		self.live.guide_settings({})
		self.assertEqual(self.live._load()['epg_url'], url)


	def test_guide_shows_current_next_and_keeps_unmatched_channel_playable(self):
		now = 1000
		streams = [{'name': 'Sports', 'stream_id': 7, 'epg_channel_id': 'sports'}, {'name': 'Other', 'stream_id': 8, 'epg_channel_id': None}]
		guide = {'sports': [[900, 1100, 'Live match'], [1100, 1200, 'Next match']]}
		with patch.object(self.live, '_request', return_value=streams), patch.object(self.live, '_guide_data', return_value=guide), patch.object(self.live.time, 'time', return_value=now), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.guide_channels({'category': '3'})
		first, second = item.call_args_list
		self.assertIn('Now: Live match', first.args[0])
		self.assertIn('Next:', first.args[0])
		self.assertEqual(first.args[1]['mode'], 'livetv.schedule')
		self.assertEqual(second.args[1], {'mode': 'livetv.play', 'id': '8'})


if __name__ == '__main__': unittest.main()
