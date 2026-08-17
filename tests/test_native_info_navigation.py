import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NativeInfoNavigationTests(unittest.TestCase):
	def test_episodes_marks_native_info_for_return_before_closing_dialog(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesDialogVideoInfo.xml').getroot()
		button = next(control for control in root.iter('control') if control.get('id') == '53')
		actions = [node.text for node in button.findall('onclick')]

		self.assertEqual(actions, [
			'SetProperty(BaseWindow,1,Home)',
			'Dialog.Close(movieinformation)',
			'AlarmClock(BrowseEpisodes,ActivateWindow(Videos,plugin://skin.titan.bingie.lite/?mode=build_season_list&tmdb_id=$INFO[ListItem.UniqueID(tmdb)],return),00:00,silent)'
		])


if __name__ == '__main__':
	unittest.main()
