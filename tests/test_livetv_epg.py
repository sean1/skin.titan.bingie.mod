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


if __name__ == '__main__': unittest.main()
