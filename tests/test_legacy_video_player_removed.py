import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LegacyVideoPlayerRemovalTests(unittest.TestCase):
	def test_unreachable_imdb_video_player_feature_is_removed_coherently(self):
		self.assertFalse((ROOT / 'resources/lib/windows/videoplayer.py').exists())
		self.assertFalse((ROOT / 'resources/skins/Default/1080i/videoplayer.xml').exists())
		for relative_path in (
			'resources/lib/windows/extras.py', 'resources/lib/windows/people.py', 'resources/lib/modules/dialogs.py',
			'resources/skins/Default/1080i/extras.xml', 'resources/skins/Default/1080i/people.xml'
		):
			contents = (ROOT / relative_path).read_text(encoding='utf-8')
			self.assertNotIn('windows.videoplayer', contents)
			self.assertNotIn('videoplayer.xml', contents)
			self.assertNotIn('imdb_videos_choice', contents)


if __name__ == '__main__':
	unittest.main()
