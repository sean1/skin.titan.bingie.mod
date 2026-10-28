import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class FakePlayer:
	def __init__(self):
		self.file = ''
		self.position = 0
		self.video = True
		self.speed = 1
		self.played = []

	def isPlaying(self): return bool(self.file)
	def getPlayingFile(self): return self.file
	def isPlayingVideo(self): return bool(self.file) and self.video
	def getTime(self): return self.position
	def getPlaySpeed(self): return self.speed
	def play(self, url):
		self.played.append(url)
		self.file = url


class LiveTVFailoverTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.ku = types.SimpleNamespace(xbmc_player=FakePlayer, dialog=types.SimpleNamespace(notification=lambda *args: None))
		modules = types.ModuleType('modules')
		modules.kodi_utils = cls.ku
		menus = types.ModuleType('menus')
		menus.__path__ = []
		livetv = types.ModuleType('menus.livetv')
		livetv._load = lambda: {'server': 'http://example.test', 'username': 'private', 'password': 'secret'}
		spec = importlib.util.spec_from_file_location('test_livetv_failover_module', Path(__file__).resolve().parents[1] / 'resources/lib/modules/livetv_failover.py')
		cls.module = importlib.util.module_from_spec(spec)
		with patch.dict(sys.modules, {'modules': modules, 'menus': menus, 'menus.livetv': livetv}): spec.loader.exec_module(cls.module)
		cls.imports = {'menus': menus, 'menus.livetv': livetv}

	def setUp(self):
		self.now = 0
		self.properties = {self.module.PROPERTY: json.dumps({'token': 'one', 'id': '1', 'alternatives': ['2', '3']})}
		self.ku.get_property = lambda name: self.properties.get(name, '')
		self.ku.clear_property = lambda name: self.properties.pop(name, None)
		self.notes = []
		self.ku.dialog.notification = lambda *args: self.notes.append(args)
		self.player = self.module.LiveTVFailover(clock=lambda: self.now)
		self.modules = patch.dict(sys.modules, self.imports)
		self.modules.start()
		self.addCleanup(self.modules.stop)

	def start(self):
		self.player.tick()
		self.player.file = self.player._url('1')
		self.player.onAVStarted()
		self.player.tick()

	def test_unobserved_startup_timeout_does_not_restart_playback(self):
		self.player.tick()
		self.now = 26
		self.assertFalse(self.player.tick())
		self.assertFalse(self.player.played)
		self.assertNotIn('private', json.dumps(self.properties))

	def test_observed_stream_that_never_starts_retries_after_timeout(self):
		self.player.tick()
		self.player.file = self.player._url('1')
		self.player.video = False
		self.now = 1
		self.player.tick()
		self.now = 26
		self.assertTrue(self.player.tick())
		self.assertEqual(self.player.played, [self.player._url('2')])

	def test_observed_startup_error_retries(self):
		self.player.tick()
		self.player.file = self.player._url('1')
		self.player.video = False
		self.now = 1
		self.player.tick()
		self.now = 3
		self.player.onPlayBackError()
		self.player.tick()
		self.assertEqual(self.player.played, [self.player._url('2')])

	def test_error_tries_next_stream(self):
		self.start()
		self.now = 3
		self.player.onPlayBackError()
		self.player.tick()
		self.assertEqual(self.player.played, [self.player._url('2')])

	def test_prolonged_stall_tries_next_but_progress_resets_timer(self):
		self.start()
		self.now = 10
		self.player.position = 5
		self.player.tick()
		self.now = 20
		self.player.tick()
		self.assertFalse(self.player.played)
		self.now = 26
		self.player.tick()
		self.assertEqual(self.player.played, [self.player._url('2')])

	def test_pause_and_manual_stop_do_not_switch(self):
		self.start()
		self.player.onPlayBackPaused()
		self.now = 100
		self.player.tick()
		self.player.file = ''
		self.player.onPlayBackStopped()
		self.assertFalse(self.player.tick())
		self.assertFalse(self.player.played)

	def test_different_playback_cancels_monitor(self):
		self.start()
		self.now = 4
		self.player.file = 'http://example.test/other'
		self.assertFalse(self.player.tick())
		self.assertFalse(self.player.played)

	def test_outgoing_stream_during_handoff_does_not_count_as_foreign(self):
		self.start()
		old = self.player.file
		self.now = 20
		self.player.tick()
		self.player.file = old
		self.now = 21
		self.assertTrue(self.player.tick())
		self.player.file = ''
		self.now = 22
		self.assertTrue(self.player.tick())
		self.player.file = self.player._url('2')
		self.player.onAVStarted()
		self.assertTrue(self.player.tick())

	def test_manual_stop_during_alternative_start_cancels(self):
		self.start()
		self.now = 20
		self.player.tick()
		self.player.file = ''
		self.player.onPlayBackStopped()
		self.now = 50
		self.assertFalse(self.player.tick())
		self.assertEqual(len(self.player.played), 1)

	def test_short_unrelated_playback_cancels_pending_start(self):
		self.player.tick()
		self.now = 1
		self.player.file = 'http://example.test/other'
		self.player.tick()
		self.player.file = ''
		self.now = 2
		self.assertFalse(self.player.tick())
		self.now = 30
		self.assertFalse(self.player.tick())
		self.assertFalse(self.player.played)

	def test_new_session_token_invalidates_old_events(self):
		self.start()
		self.player.onPlayBackError()
		self.properties[self.module.PROPERTY] = json.dumps({'token': 'two', 'id': '4', 'alternatives': ['5']})
		self.player.file = ''
		self.player.tick()
		self.assertFalse(self.player.played)
		self.assertEqual(self.player.session['id'], '4')

	def test_alternatives_are_capped_and_exhaustion_stops(self):
		self.properties[self.module.PROPERTY] = json.dumps({'token': 'one', 'id': '1', 'alternatives': ['2', '3', '4', '5']})
		self.start()
		for i in range(4):
			self.now += 3
			self.player.onPlayBackError()
			self.player.tick()
			if self.player.session: self.player.onAVStarted()
		self.assertEqual(len(self.player.played), 3)
		self.assertIsNone(self.player.session)
		self.assertIn('No more', self.notes[-1][1])

	def test_delayed_stop_from_old_feed_does_not_cancel_new_playback(self):
		self.start()
		self.now = 20
		self.player.tick()
		self.player.onPlayBackStopped()
		self.assertTrue(self.player.tick())
		self.assertEqual(self.player.session['id'], '2')

	def test_error_followed_by_stop_with_no_player_respects_stop(self):
		self.start()
		self.now = 3
		self.player.onPlayBackError()
		self.player.file = ''
		self.player.onPlayBackStopped()
		self.assertFalse(self.player.tick())
		self.assertFalse(self.player.played)


if __name__ == '__main__': unittest.main()
