import io
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


	def test_sports_parser_requires_explicit_timezones_and_limits_window(self):
		now = datetime(2026, 10, 28, 20, 30, tzinfo=timezone.utc).timestamp()
		xml = b'''<tv>
			<programme channel="zoned" start="20261028200000 +0000" stop="20261028210000 +0000"><title>Live</title></programme>
			<programme channel="unzoned" start="20261028200000" stop="20261028210000"><title>Live</title></programme>
			<programme channel="partial" start="20261028200000 +0000" stop="20261028210000"><title>Live</title></programme>
			<programme channel="late" start="20261028230000 +0000" stop="20261029000000 +0000"><title>Later</title></programme>
		</tv>'''
		strict = self.live._parse_guide(io.BytesIO(xml), now, sports_window=True)
		self.assertEqual(set(strict), {'zoned'})
		permissive = self.live._parse_guide(io.BytesIO(xml), now, sports_window=True, require_zone=False)
		self.assertEqual(set(permissive), {'zoned', 'unzoned', 'partial'})
		self.assertEqual(permissive['unzoned'][0], strict['zoned'][0])


if __name__ == '__main__': unittest.main()
