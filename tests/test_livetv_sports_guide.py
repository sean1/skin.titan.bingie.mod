import gzip
import io
import json
import tempfile
import threading
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from tests.module_isolation import load_module, temporary_modules
from tests.test_livetv import load_livetv

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 28, 20, 30, tzinfo=timezone.utc).timestamp()
XML = b'''<tv>
<programme channel="zoned" start="20261028200000 +0000" stop="20261028210000 +0000"><title>Live event</title></programme>
<programme channel="unzoned" start="20261028200000" stop="20261028210000"><title>Another event</title></programme>
<programme channel="expired" start="20261028180000 +0000" stop="20261028200000 +0000"><title>Past</title></programme>
<programme channel="future" start="20261028230000 +0000" stop="20261029000000 +0000"><title>Later</title></programme>
</tv>'''


class SportsGuideTests(unittest.TestCase):
	def setUp(self):
		self.live = load_livetv()
		(ROOT / 'tmp').mkdir(exist_ok=True)
		self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
		self.addCleanup(self.temp.cleanup)
		self.live.ku.profile_path = self.temp.name
		self.account = {'server': 'https://provider.example', 'username': 'private', 'password': 'secret'}
		self.menus = types.ModuleType('menus')
		self.menus.livetv = self.live
		self.modules = types.ModuleType('modules')
		self.modules.__path__ = [str(ROOT / 'resources/lib/modules')]
		self.modules.kodi_utils = self.live.ku
		relay = types.ModuleType('modules.livetv_vpn_relay')
		relay.open_url = Mock()
		self.stubs = {'menus': self.menus, 'menus.livetv': self.live, 'modules': self.modules, 'modules.kodi_utils': self.live.ku, 'modules.livetv_vpn_relay': relay}
		self.guide = load_module('test_sports_guide_module', ROOT / 'resources/lib/modules/livetv_sports_guide.py', self.stubs)

	def refresh(self, permissive=False, response=XML, canceled=None):
		updated = Mock()
		with temporary_modules(self.stubs), patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_sports_accounts', return_value=[('primary', self.account)]), patch.object(self.guide.time, 'time', return_value=NOW), patch.object(self.guide, 'open_url', return_value=io.BytesIO(response)) as request:
			self.guide.refresh(canceled or threading.Event(), updated, permissive)
		return request, updated

	def test_separate_cached_snapshots_preserve_timestamp_rules_and_window(self):
		strict_request, strict_update = self.refresh(response=gzip.compress(XML))
		permissive_request, permissive_update = self.refresh(permissive=True)
		strict_path = Path(self.live._sports_guide_cache_path(self.account))
		permissive_path = Path(self.live._sports_guide_cache_path(self.account, True))
		strict = json.loads(strict_path.read_text())['programmes']
		permissive = json.loads(permissive_path.read_text())['programmes']
		self.assertEqual(set(strict), {'zoned'})
		self.assertEqual(set(permissive), {'zoned', 'unzoned'})
		self.assertEqual(permissive['unzoned'][0][2], 'Another event')
		self.assertEqual(strict_path.stat().st_mode & 0o777, 0o600)
		self.assertNotIn('secret', strict_path.read_text())
		self.assertNotIn('private', strict_path.name)
		strict_request.assert_called_once()
		permissive_request.assert_called_once()
		strict_update.assert_called_once()
		permissive_update.assert_called_once()
		request, updated = self.refresh(permissive=True)
		request.assert_not_called()
		updated.assert_not_called()

	def test_cancellation_does_not_fetch_or_create_a_snapshot(self):
		canceled = threading.Event()
		canceled.set()
		request, updated = self.refresh(canceled=canceled)
		request.assert_not_called()
		updated.assert_not_called()
		self.assertFalse(Path(self.live._sports_guide_cache_path(self.account)).exists())


if __name__ == '__main__': unittest.main()
