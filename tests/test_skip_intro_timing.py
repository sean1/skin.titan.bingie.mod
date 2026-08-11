import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, call


ROOT = Path(__file__).resolve().parents[1]


def load_episode_tools():
	windows = types.ModuleType('windows')
	windows.open_window = lambda *args, **kwargs: None
	metadata = types.ModuleType('indexers.metadata')
	metadata.tvshow_meta = lambda *args: {}
	metadata.season_episodes_meta = lambda *args: []
	metadata.all_episodes_meta = lambda *args: []
	indexers = types.ModuleType('indexers')
	indexers.__path__ = []
	indexers.metadata = metadata
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.local_string = str
	kodi_utils.build_url = lambda params: 'plugin://test'
	settings = types.ModuleType('modules.settings')
	utils = types.ModuleType('modules.utils')
	utils.get_next_episode_pointer = lambda *args: (1, 1, False)
	utils.adjust_premiered_date = lambda value, hours: (value, value)
	utils.get_datetime = lambda: None
	sources = types.ModuleType('modules.sources')
	sources.Sources = type('Sources', (), {})
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	modules.settings = settings
	stubs = {
		'windows': windows,
		'indexers': indexers,
		'indexers.metadata': metadata,
		'modules': modules,
		'modules.kodi_utils': kodi_utils,
		'modules.settings': settings,
		'modules.utils': utils,
		'modules.sources': sources
	}
	previous = {name: sys.modules.get(name) for name in stubs}
	sys.modules.update(stubs)
	try:
		path = ROOT / 'resources' / 'lib' / 'modules' / 'episode_tools.py'
		spec = importlib.util.spec_from_file_location('test_skip_intro_episode_tools', path)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
	finally:
		for name, old_module in previous.items():
			if old_module is None: sys.modules.pop(name, None)
			else: sys.modules[name] = old_module
	return module


class FakePlayer:
	def __init__(self, current, playing_states=None, raise_time=False):
		self.intro = (10.0, 20.0)
		self.current = current
		self.playing_states = playing_states
		self.raise_time = raise_time
		self.seeks = []

	def isPlayingVideo(self):
		if self.playing_states is None: return True
		return self.playing_states.pop(0) if self.playing_states else False

	def getTime(self):
		if self.raise_time: raise RuntimeError('player unavailable')
		return self.current

	def seekTime(self, value):
		self.seeks.append(value)


class SkipIntroTimingTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.episode_tools = load_episode_tools()

	def setUp(self):
		self.sleeps = []
		self.episode_tools.open_window = Mock(return_value=False)

	def advance_on_sleep(self, player):
		def sleep(milliseconds):
			self.sleeps.append(milliseconds)
			player.current += milliseconds / 1000
		self.episode_tools.kodi_utils.sleep = sleep

	def test_prompt_is_detected_within_200_milliseconds(self):
		player = FakePlayer(9.81)
		prompt_times = []
		meta = {'title': 'Example'}
		self.advance_on_sleep(player)
		self.episode_tools.open_window.side_effect = lambda *args, **kwargs: prompt_times.append(player.current) or False

		result = self.episode_tools.execute_skip_intro(player, meta)

		self.assertIsNone(result)
		self.assertEqual(self.sleeps, [200])
		self.assertAlmostEqual(prompt_times[0], 10.01)
		self.assertLessEqual(prompt_times[0] - player.intro[0], 0.2)
		self.assertEqual(self.episode_tools.open_window.call_args_list, [call(('windows.episodes', 'NextEpisode'), 'episodes.xml', meta=meta, function='skip_intro')])
		self.assertEqual(player.seeks, [])

	def test_accepting_prompt_seeks_to_intro_end_once(self):
		player = FakePlayer(10.0)
		self.advance_on_sleep(player)
		self.episode_tools.open_window.return_value = True

		self.episode_tools.execute_skip_intro(player, {'title': 'Example'})

		self.assertEqual(self.sleeps, [])
		self.assertEqual(self.episode_tools.open_window.call_count, 1)
		self.assertEqual(player.seeks, [20.0])

	def test_intro_end_boundary_remains_inclusive(self):
		for current, prompt_count in ((20.0, 1), (20.001, 0)):
			with self.subTest(current=current):
				player = FakePlayer(current)
				self.advance_on_sleep(player)
				self.episode_tools.open_window.reset_mock()

				self.episode_tools.execute_skip_intro(player, {})

				self.assertEqual(self.episode_tools.open_window.call_count, prompt_count)
				self.assertEqual(self.sleeps, [])
				self.assertEqual(player.seeks, [])

	def test_playback_stop_before_intro_exits_without_prompt(self):
		player = FakePlayer(9.81, playing_states=[True, False])
		self.advance_on_sleep(player)

		self.episode_tools.execute_skip_intro(player, {})

		self.assertEqual(self.sleeps, [200])
		self.episode_tools.open_window.assert_not_called()
		self.assertEqual(player.seeks, [])

	def test_get_time_failure_exits_without_prompt(self):
		player = FakePlayer(9.81, raise_time=True)
		self.advance_on_sleep(player)

		self.episode_tools.execute_skip_intro(player, {})

		self.assertEqual(self.sleeps, [])
		self.episode_tools.open_window.assert_not_called()
		self.assertEqual(player.seeks, [])


if __name__ == '__main__':
	unittest.main()
