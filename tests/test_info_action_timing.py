import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InfoActionTimingTests(unittest.TestCase):

	def test_native_movie_info_cancels_trailer_requests_on_close(self):
		window = ET.parse(ROOT / 'xml' / 'DialogVideoInfo.xml').getroot()
		actions = [(node.get('condition'), node.text) for node in window.findall('onunload') if 'BingieTrailerPreview' in (node.text or '')]
		self.assertEqual(actions, [
			('String.IsEqual(Window.Property(PovInfoType),movie)', 'SetProperty(BingieTrailerPreviewCancel,true,Home)'),
			('String.IsEqual(Window.Property(PovInfoType),movie)', 'ClearProperty(BingieTrailerPreviewRequest,Home)'),
		])

	def test_post_close_info_actions_use_zero_delay_alarms(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesDialogVideoInfo.xml').getroot()
		target_names = ('AlarmClock(PlayShow,', 'AlarmClock(PlaySeason,', 'AlarmClock(PlayMovie,', 'AlarmClock(BrowseEpisodes,')
		target_actions = []
		for control in root.iter('control'):
			actions = [(onclick.text or '').strip() for onclick in control.findall('onclick')]
			targets = [action for action in actions if action.startswith(target_names)]
			if not targets: continue
			self.assertIn('Dialog.Close(movieinformation)', actions)
			close_index = actions.index('Dialog.Close(movieinformation)')
			self.assertTrue(all(close_index < actions.index(action) for action in targets))
			target_actions.extend(targets)

		self.assertEqual(len(target_actions), 10)
		self.assertTrue(all(',00:00,silent)' in action for action in target_actions))
		self.assertTrue(all(',00:01,' not in action for action in target_actions))


if __name__ == '__main__':
	unittest.main()
