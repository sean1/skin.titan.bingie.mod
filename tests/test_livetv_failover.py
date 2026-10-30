import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


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
		self.ku.set_property = lambda name, value: self.properties.__setitem__(name, value)
		self.ku.get_visibility = lambda condition: False
		self.ku.get_infolabel = lambda label: 'Playback failed'
		self.ku.xbmc = types.SimpleNamespace(getLocalizedString=lambda value: 'Playback failed' if value == 16026 else '', executebuiltin=Mock())
		self.notes = []
		self.ku.dialog.notification = lambda *args: self.notes.append(args)
		self.player = self.module.LiveTVFailover(clock=lambda: self.now)
		self.modules = patch.dict(sys.modules, self.imports)
		self.modules.start()
		self.addCleanup(self.modules.stop)
		self.probe = patch.object(self.module, '_probe_failure', return_value=False)
		self.probe.start()
		self.addCleanup(self.probe.stop)

	def start(self):
		self.player.tick()
		self.player.file = self.player._url('1')
		self.player.onAVStarted()
		self.player.tick()



	def test_cross_provider_same_stream_id_switches_both_directions_without_secrets_in_properties(self):
		accounts = {
			'primary': {'server': 'http://first.test', 'username': 'first-user', 'password': 'first-secret'},
			'backup': {'server': 'http://second.test', 'username': 'second-user', 'password': 'second-secret'},
		}
		self.imports['menus.livetv']._account_for_feed = accounts.get
		self.properties[self.module.PROPERTY] = json.dumps({'token': 'multi', 'id': 'primary:42', 'alternatives': ['backup:42'], 'feeds': [{'id': 'primary:42', 'name': 'Feed 1'}, {'id': 'backup:42', 'name': 'Feed 2'}]})
		self.player.tick()
		self.player.file = self.player._url('primary:42')
		self.player.onAVStarted()
		self.player.tick()
		self.properties[self.module.SWITCH_REQUEST] = json.dumps({'token': 'multi', 'current': 'primary:42', 'id': 'backup:42'})
		self.player.tick()
		self.assertEqual(self.player.played[-1], 'http://second.test/live/second-user/second-secret/42.ts')
		self.player.onAVStarted()
		self.player.tick()
		self.properties[self.module.SWITCH_REQUEST] = json.dumps({'token': 'multi', 'current': 'backup:42', 'id': 'primary:42'})
		self.player.tick()
		self.assertEqual(self.player.played[-1], 'http://first.test/live/first-user/first-secret/42.ts')
		self.player.onAVStarted()
		self.player.tick()
		public = json.dumps(self.properties)
		for secret in ('first-user', 'first-secret', 'second-user', 'second-secret', 'http://first.test', 'http://second.test'):
			self.assertNotIn(secret, public)


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
		self.now = 17
		self.player.tick()
		self.assertFalse(self.player.played)
		self.now = 19
		self.player.tick()
		self.assertEqual(self.player.played, [self.player._url('2')])


	def test_outdated_probe_does_not_switch_new_session(self):
		self.player.tick()
		self.player.probe_results.put(('one', '1', True))
		self.properties[self.module.PROPERTY] = json.dumps({'token': 'two', 'id': '4', 'alternatives': ['5']})
		self.now = 3
		self.player.tick()
		self.assertFalse(self.player.played)
		self.assertEqual(self.player.session['id'], '4')


	def test_pause_and_manual_stop_do_not_switch(self):
		self.start()
		self.player.onPlayBackPaused()
		self.now = 100
		self.player.tick()
		self.player.file = ''
		self.player.onPlayBackStopped()
		self.assertFalse(self.player.tick())
		self.assertFalse(self.player.played)


	def test_manual_stop_during_alternative_start_cancels(self):
		self.start()
		self.now = 20
		self.player.tick()
		self.player.file = ''
		self.player.onPlayBackStopped()
		self.now = 50
		self.assertFalse(self.player.tick())
		self.assertEqual(len(self.player.played), 1)


	def test_new_session_token_invalidates_old_events(self):
		self.start()
		self.player.onPlayBackError()
		self.properties[self.module.PROPERTY] = json.dumps({'token': 'two', 'id': '4', 'alternatives': ['5']})
		self.player.file = ''
		self.player.tick()
		self.assertFalse(self.player.played)
		self.assertEqual(self.player.session['id'], '4')


if __name__ == '__main__': unittest.main()
