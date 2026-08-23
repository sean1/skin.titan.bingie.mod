import sys
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

	def test_player_health_records_only_the_terminal_stream_outcome(self):
		player_module = load_player()
		events = []
		player_module.playback_health = types.SimpleNamespace(record=lambda context, event, **kwargs: events.append((event, kwargs)))
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.playback_health_context = {'provider': 'debrid'}
		player.playback_health_started_at = player_module.monotonic() - 70
		player.playback_health_finalized = False
		player.playback_health_qualified = True
		player.playback_health_bitrate_mbps = 40
		player.playback_stall_count = 0
		player.playback_event = True
		player.getTotalTime, player.getTime = lambda: 7200, lambda: 65

		player.onPlayBackError()
		player.onPlayBackEnded()

		self.assertEqual([event for event, _ in events], ['stream_error'])
		self.assertTrue(player.playback_health_finalized)
	def test_successfully_resolved_source_metadata_reaches_player(self):
		sources_module = load_sources_module()
		seen = {}

		class Player:
			def run(self, link, meta, progress):
				seen.update({'link': link, 'meta': meta, 'progress': progress})
				return 'played'

		sources_module.POVPlayer = Player
		instance = sources_module.Sources.__new__(sources_module.Sources)
		instance.background = False
		instance.autoplay = True
		instance.progress_dialog = types.SimpleNamespace(full_screen=False)
		instance.meta = {'title': 'Movie', 'release_name': 'stale'}
		instance._no_results = lambda: None
		item = {
			'name': 'Movie.2024.2160p.WEB-DL-GROUP.mkv', 'display_name': 'Movie 2024', 'unrestricted_link': 'https://stream.invalid/signed',
			'quality': '4K', 'extraInfo': 'HEVC | HDR', 'scrape_provider': 'fixture', 'provider': 'fixture'
		}

		result = instance.play_file([item])

		self.assertEqual(result, 'played')
		self.assertEqual(seen['meta']['release_name'], item['name'])
		self.assertEqual(seen['meta']['release_quality'], '4K')
		self.assertEqual(seen['meta']['release_info'], 'HEVC | HDR')
		self.assertNotEqual(seen['meta']['release_name'], seen['link'])

	def test_playback_start_failure_tries_next_resolved_source(self):
		sources_module = load_sources_module()
		played = []

		class Player:
			def run(self, link, meta, progress):
				played.append(link)
				return len(played) > 1

		sources_module.POVPlayer = Player
		instance = sources_module.Sources.__new__(sources_module.Sources)
		instance.background = False
		instance.autoplay = True
		instance.progress_dialog = types.SimpleNamespace(full_screen=False)
		instance.meta = {'title': 'Movie'}
		instance._no_results = lambda: None
		items = [
			{'name': 'first', 'unrestricted_link': 'https://stream.invalid/first', 'quality': '4K', 'extraInfo': '', 'scrape_provider': 'fixture', 'provider': 'fixture'},
			{'name': 'second', 'unrestricted_link': 'https://stream.invalid/second', 'quality': '4K', 'extraInfo': '', 'scrape_provider': 'fixture', 'provider': 'fixture'},
		]

		result = instance.play_file(items)

		self.assertTrue(result)
		self.assertEqual(played, ['https://stream.invalid/first', 'https://stream.invalid/second'])

	def test_autoplay_defers_unhealthy_host_but_keeps_last_resort(self):
		sources_module = load_sources_module()
		played = []
		sources_module.playback_health = types.SimpleNamespace(
			source_context=lambda item, link=None: {'provider': 'rd', 'host': link.split('/')[2] if link else ''},
			record=lambda *args, **kwargs: None,
			host_penalty=lambda context: 100 if context.get('host') == 'bad.invalid' else 0
		)

		class Player:
			def run(self, link, meta, progress):
				played.append(link)
				return True

		sources_module.POVPlayer = Player
		instance = sources_module.Sources.__new__(sources_module.Sources)
		instance.background, instance.autoplay = False, True
		instance.progress_dialog = types.SimpleNamespace(full_screen=False)
		instance.meta = {'title': 'Movie', 'mediatype': 'movie'}
		instance._no_results = lambda: None
		bad = {'name': 'bad', 'unrestricted_link': 'https://bad.invalid/file', 'quality': '4K', 'extraInfo': '', 'scrape_provider': 'fixture', 'provider': 'rd'}
		good = {'name': 'good', 'unrestricted_link': 'https://good.invalid/file', 'quality': '4K', 'extraInfo': '', 'scrape_provider': 'fixture', 'provider': 'rd'}

		self.assertTrue(instance.play_file([bad, good]))
		self.assertEqual(played, ['https://good.invalid/file'])
		played.clear()
		self.assertTrue(instance.play_file([bad]))
		self.assertEqual(played, ['https://bad.invalid/file'])

	def test_manual_source_selection_never_defers_unhealthy_host(self):
		sources_module = load_sources_module()
		played = []
		sources_module.playback_health = types.SimpleNamespace(
			source_context=lambda item, link=None: {'provider': 'rd', 'host': 'bad.invalid'}, record=lambda *args, **kwargs: None, host_penalty=lambda context: 100
		)
		sources_module.POVPlayer = type('Player', (), {'run': lambda self, link, meta, progress: played.append(link) or True})
		instance = sources_module.Sources.__new__(sources_module.Sources)
		instance.background, instance.autoplay = False, False
		instance.progress_dialog = types.SimpleNamespace(full_screen=False)
		instance.meta = {'title': 'Movie'}
		instance._no_results = lambda: None
		bad = {'name': 'bad', 'unrestricted_link': 'https://bad.invalid/file', 'quality': '4K', 'extraInfo': '', 'scrape_provider': 'fixture', 'provider': 'rd'}

		self.assertTrue(instance.play_file([bad], bad))
		self.assertEqual(played, ['https://bad.invalid/file'])

	def test_playback_error_resume_position_reaches_next_source(self):
		sources_module = load_sources_module()
		seen_meta = []

		class Player:
			def run(self, link, meta, progress):
				seen_meta.append(meta)
				self.retry_resume_percent = 37.5 if len(seen_meta) == 1 else 0
				return len(seen_meta) > 1

		sources_module.POVPlayer = Player
		instance = sources_module.Sources.__new__(sources_module.Sources)
		instance.background = False
		instance.autoplay = True
		instance.progress_dialog = types.SimpleNamespace(full_screen=False)
		instance.meta = {'title': 'Movie', 'bookmark': 12}
		instance._no_results = lambda: None
		items = [
			{'name': 'first', 'unrestricted_link': 'https://stream.invalid/first', 'quality': '4K', 'extraInfo': '', 'scrape_provider': 'fixture', 'provider': 'fixture'},
			{'name': 'second', 'unrestricted_link': 'https://stream.invalid/second', 'quality': '4K', 'extraInfo': '', 'scrape_provider': 'fixture', 'provider': 'fixture'},
		]

		self.assertTrue(instance.play_file(items))
		self.assertNotIn('_retry_resume_percent', seen_meta[0])
		self.assertEqual(seen_meta[1]['_retry_resume_percent'], 37.5)
		self.assertEqual(seen_meta[1]['release_name'], 'second')

	def test_duration_mismatch_retry_does_not_invent_resume_position(self):
		sources_module = load_sources_module()
		seen_meta = []

		class Player:
			def run(self, link, meta, progress):
				seen_meta.append(meta)
				self.retry_resume_percent = 0
				return len(seen_meta) > 1

		sources_module.POVPlayer = Player
		instance = sources_module.Sources.__new__(sources_module.Sources)
		instance.background = False
		instance.autoplay = True
		instance.progress_dialog = types.SimpleNamespace(full_screen=False)
		instance.meta = {'title': 'Movie'}
		instance._no_results = lambda: None
		items = [
			{'name': 'trailer', 'unrestricted_link': 'https://stream.invalid/trailer', 'quality': '4K', 'extraInfo': '', 'scrape_provider': 'fixture', 'provider': 'fixture'},
			{'name': 'movie', 'unrestricted_link': 'https://stream.invalid/movie', 'quality': '4K', 'extraInfo': '', 'scrape_provider': 'fixture', 'provider': 'fixture'},
		]

		self.assertTrue(instance.play_file(items))
		self.assertNotIn('_retry_resume_percent', seen_meta[1])

	def test_stall_sampling_ignores_pause_seek_and_records_sustained_freeze(self):
		player_module = load_player()
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.playback_paused, player.playback_seek_grace_until = False, 0
		player.playback_sample_wall, player.playback_sample_media = None, None
		player.playback_stall_seconds, player.playback_stall_count, player.playback_stall_active = 0, 0, False
		player.curr_time, player.remaining_time = 100, 1000
		player.getPlaySpeed = lambda: 1

		for now in (0, 1, 2, 3): player._sample_playback_stall(now)
		self.assertEqual(player.playback_stall_count, 1)
		player.onPlayBackPaused()
		player.curr_time = 100
		player._sample_playback_stall(20)
		player.onPlayBackResumed()
		player._sample_playback_stall(21)
		self.assertEqual(player.playback_stall_count, 1)
		with mock.patch.object(player_module, 'monotonic', return_value=22): player.onPlayBackSeek(600000, 0)
		player.curr_time = 700
		player._sample_playback_stall(23)
		self.assertEqual(player.playback_stall_count, 1)

	def test_clean_and_stalled_plays_use_distinct_terminal_health_outcomes(self):
		player_module = load_player()
		events = []
		player_module.playback_health = types.SimpleNamespace(record=lambda context, event, **kwargs: events.append(event))
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.playback_health_context = {'provider': 'rd'}
		player.playback_health_started_at = player_module.monotonic() - 70
		player.playback_health_bitrate_mbps = 30
		player.playback_health_qualified, player.playback_health_finalized = True, False
		player.playback_stall_count = 1
		player._finalize_stream(True)
		player._finalize_stream(False)

		self.assertEqual(events, ['stalled_play'])

	def test_player_passes_release_context_to_subtitle_task(self):
		player_module = load_player()
		player = player_module.POVPlayer.__new__(player_module.POVPlayer)
		player.subs_searched = False
		player.meta = {
			'poster': 'poster.jpg', 'release_name': 'Show.S01E02.WEB-DL-GROUP', 'release_quality': '1080p', 'release_info': 'HEVC'
		}
		player.mediatype, player.season, player.episode = 'episode', 1, 2
		player.title, player.imdb_id, player.year = 'Show', 'tt123', 2024
		calls = []

		class Thread:
			def __init__(self, target, args=(), **kwargs): calls.append((target, args))
			def start(self): pass

		subtitles = types.ModuleType('indexers.subtitles')
		subtitles.Subtitles = mock.Mock
		with mock.patch.object(player_module, 'Thread', Thread), mock.patch.dict(sys.modules, {'indexers.subtitles': subtitles}):
			player.exec_task('subtitles')

		args = calls[0][1]
		self.assertEqual(args, ('Show', 'tt123', 1, 2, 'poster.jpg', 'Show.S01E02.WEB-DL-GROUP', '1080p', 'HEVC', 2024))


if __name__ == '__main__':
	unittest.main()
