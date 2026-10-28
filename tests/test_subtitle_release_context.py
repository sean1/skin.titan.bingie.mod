import types
import unittest
from unittest import mock

from tests.test_audio_selection import load_player
from tests.test_external_manager import load_sources_module


class SubtitleReleaseContextTests(unittest.TestCase):
	def test_resolve_and_private_playback_health_context_are_recorded_without_url(self):
		sources_module = load_sources_module()
		events, seen = [], {}
		sources_module.playback_health = types.SimpleNamespace(
			source_context=lambda item, link=None: {'provider': item['provider'], 'host': 'stream.invalid' if link else ''},
			record=lambda context, event, **kwargs: events.append((context, event, kwargs))
		)

		class Player:
			def run(self, link, meta, progress):
				seen.update(meta)
				return True

		sources_module.POVPlayer = Player
		instance = sources_module.Sources.__new__(sources_module.Sources)
		instance.background, instance.autoplay = False, True
		instance.progress_dialog = types.SimpleNamespace(full_screen=False)
		instance.meta = {'title': 'Movie'}
		instance._no_results = lambda: None
		item = {'name': 'movie', 'unrestricted_link': 'https://stream.invalid/private-token', 'quality': '4K', 'extraInfo': '', 'scrape_provider': 'fixture', 'provider': 'debrid'}

		self.assertTrue(instance.play_file([item]))
		self.assertEqual(events[0][1], 'resolve_ok')
		self.assertEqual(seen['_playback_health_context'], {'provider': 'debrid', 'host': 'stream.invalid'})
		self.assertNotIn('private-token', repr(seen))


	def test_player_exception_tries_next_resolved_source(self):
		sources_module = load_sources_module()
		played = []

		class Player:
			def run(self, link, meta, progress):
				played.append(link)
				if len(played) == 1: raise RuntimeError('decoder setup')
				return True

		sources_module.POVPlayer = Player
		instance = sources_module.Sources.__new__(sources_module.Sources)
		instance.background, instance.autoplay = False, True
		instance.progress_dialog = types.SimpleNamespace(full_screen=False)
		instance.meta = {'title': 'Movie'}
		instance._no_results = lambda: None
		items = [
			{'name': 'first', 'unrestricted_link': 'https://stream.invalid/first', 'quality': '4K', 'extraInfo': '', 'scrape_provider': 'fixture', 'provider': 'fixture'},
			{'name': 'second', 'unrestricted_link': 'https://stream.invalid/second', 'quality': '4K', 'extraInfo': '', 'scrape_provider': 'fixture', 'provider': 'fixture'},
		]

		self.assertTrue(instance.play_file(items))
		self.assertEqual(played, ['https://stream.invalid/first', 'https://stream.invalid/second'])


	def test_two_confirmed_stalls_request_autoplay_fallback_with_resume(self):
		player_module = load_player()
		events = []
		player_module.playback_health = types.SimpleNamespace(record=lambda context, event, **kwargs: events.append(event))
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.playback_health_context = {'provider': 'rd'}
		player.playback_health_started_at = player_module.monotonic() - 70
		player.playback_health_bitrate_mbps = 30
		player.playback_health_qualified, player.playback_health_finalized = True, False
		player.playback_paused, player.playback_seek_grace_until = False, 0
		player.playback_sample_wall, player.playback_sample_media = None, None
		player.playback_stall_seconds, player.playback_stall_count, player.playback_stall_active = 0, 0, False
		player.allow_stall_recovery, player.has_stall_fallback, player.stall_recovery_requested = True, True, False
		player.playback_error, player.retry_resume_percent = False, 0
		player.curr_time, player.remaining_time = 100, 900
		player.getPlaySpeed, player.getTime, player.getTotalTime = lambda: 1, lambda: 100, lambda: 1000
		player.stop = mock.Mock()

		for now in (0, 1, 2, 3): player._sample_playback_stall(now)
		player.curr_time = 101
		player._sample_playback_stall(4)
		for now in (5, 6, 7): player._sample_playback_stall(now)

		self.assertTrue(player.playback_error)
		self.assertEqual(player.retry_resume_percent, 10)
		player.stop.assert_called_once_with()
		self.assertEqual(events, ['stalled_play'])


if __name__ == '__main__':
	unittest.main()
