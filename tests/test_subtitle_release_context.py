import sys
import types
import unittest
from unittest import mock

from tests.test_audio_selection import load_player
from tests.test_external_manager import load_sources_module


class SubtitleReleaseContextTests(unittest.TestCase):
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
