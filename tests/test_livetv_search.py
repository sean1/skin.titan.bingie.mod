import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_livetv import load_livetv

ROOT = Path(__file__).resolve().parents[1]


class LiveTVSearchTests(unittest.TestCase):
	def setUp(self):
		self.live = load_livetv()
		(ROOT / 'tmp').mkdir(exist_ok=True)
		self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
		self.addCleanup(self.temp.cleanup)
		self.live.ku.profile_path = self.temp.name
		self.live.ku.addon_path = self.temp.name
		self.live._save({'server': 'http://example.test', 'username': 'user', 'password': 'private'})


	def test_matches_event_names_and_guide_titles_without_credentials(self):
		now = 1000
		streams = [{'name': 'End | Liverpool vs Old', 'stream_id': 1}, {'name': 'Live | Liverpool vs Chelsea', 'stream_id': 2}, {'name': 'TSN 1', 'stream_id': 3, 'epg_channel_id': 'tsn'}]
		programmes = {'tsn': [[900, 1100, 'Premier League: Liverpool v Chelsea'], [1200, 1300, 'Liverpool postgame']]}
		with patch.object(self.live, '_request', return_value=streams), patch.object(self.live, '_download_guide', return_value=programmes) as guide, patch.object(self.live.time, 'time', return_value=now), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.search_results({'query': 'LiVeRpOoL'})
		calls = item.call_args_list
		self.assertEqual(len(calls), 4)
		self.assertTrue(calls[0].args[0].startswith('Live | Liverpool'))
		self.assertEqual(calls[0].args[1], {'mode': 'livetv.play', 'id': '2'})
		self.assertIn('Now — Premier League: Liverpool v Chelsea — TSN 1', calls[2].args[0])
		self.assertEqual(calls[2].args[1], {'mode': 'livetv.schedule', 'id': '3', 'epg': 'tsn', 'name': 'TSN 1'})
		self.assertNotIn('private', str(calls))
		self.assertEqual(guide.call_args.kwargs['search_term'], 'liverpool')


	def test_live_soccer_variants_are_grouped_without_hiding_other_results(self):
		streams = [
			{'name': 'Live | Liverpool vs. Chelsea | all | US: SOCCER PPV 77', 'stream_id': 1},
			{'name': 'Live | Liverpool vs. Chelsea | all | CA: SOCCER PPV 17', 'stream_id': 2},
			{'name': 'Live | Liverpool vs. Chelsea | all | CA: SOCCER PPV 17', 'stream_id': 2},
			{'name': 'End | Liverpool vs. Chelsea | CA: SOCCER PPV 18', 'stream_id': 3},
			{'name': 'Live | Liverpool vs. Everton | CA: SOCCER PPV 19', 'stream_id': 4},
		]
		with patch.object(self.live, '_request', return_value=streams), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.search_results({'query': 'Liverpool'})
		calls = item.call_args_list
		self.assertEqual(len(calls), 3)
		self.assertEqual(calls[0].args[:3], ('Liverpool vs. Chelsea — 2 feeds', {'mode': 'livetv.match_feeds', 'home': 'liverpool', 'away': 'chelsea', 'scope': ''}, True))
		self.assertIn('Everton', calls[1].args[0])
		self.assertIn('End | Liverpool', calls[2].args[0])
		self.assertNotIn('private', str(calls))

	def test_explicit_dates_and_competitions_do_not_merge_distinct_matches(self):
		streams = [
			{'name': 'Live | Liverpool vs. Chelsea | Premier League | 2026-10-28 | CA: SOCCER 1', 'stream_id': 1},
			{'name': 'Live | Liverpool vs. Chelsea | Premier League | 2026-10-28 | US: SOCCER 2', 'stream_id': 2},
			{'name': 'Live | Liverpool vs. Chelsea | Premier League | 2026-11-04 | CA: SOCCER 3', 'stream_id': 3},
			{'name': 'Live | Liverpool vs. Chelsea | Carabao Cup | 2026-10-28 | CA: SOCCER 4', 'stream_id': 4},
			{'name': 'Live | Liverpool vs. Chelsea | all | CA: SOCCER 5', 'stream_id': 5},
		]
		with patch.object(self.live, '_request', return_value=streams), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.search_results({'query': 'Liverpool'})
		calls = item.call_args_list
		self.assertEqual(len(calls), 4)
		group = calls[0].args[1]
		self.assertEqual(group['mode'], 'livetv.match_feeds')
		self.assertEqual(group['scope'], '2026-10-28|premier league')
		with patch.object(self.live, '_request', return_value=streams), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.match_feeds(group)
		self.assertEqual([call.args[1]['id'] for call in item.call_args_list], ['1', '1', '2'])


	def test_match_folder_rejects_invalid_or_disappeared_fixtures(self):
		with patch.object(self.live, '_request') as request, patch.object(self.live, '_finish') as finish:
			self.live.match_feeds({'home': '', 'away': 'chelsea'})
		request.assert_not_called()
		finish.assert_called_once_with(False)
		with patch.object(self.live, '_request', return_value=[]), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.match_feeds({'home': 'liverpool', 'away': 'chelsea'})
		item.assert_not_called()
		self.assertIn('No matching live feeds', self.live.ku.dialog.notification.call_args.args[1])


if __name__ == '__main__': unittest.main()
