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

	def test_native_info_return_condition_is_unchanged(self):
		root = ET.parse(ROOT / 'xml' / 'MyVideoNav.xml').getroot()
		action = 'AlarmClock(loadinfo,Action(Info),00:00,silent)'
		onunload = next(node for node in root.findall('onunload') if node.text == action)

		self.assertEqual(onunload.get('condition'), '!String.IsEmpty(Window(Home).Property(BaseWindow)) + String.IsEmpty(Window(Home).Property(ListItem.TVShowID)) + !Player.HasVideo')

	def test_custom_info_opens_dedicated_season_window(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesPovInfo.xml').getroot()
		button = next(control for control in root.iter('control') if control.get('id') == '53')
		actions = [(node.get('condition'), node.text) for node in button.findall('onclick')]

		resolved = '!String.IsEmpty(Window(Home).Property(PovInfoTmdb))'
		pending = 'String.IsEmpty(Window(Home).Property(PovInfoTmdb)) + !String.IsEmpty(Window(Home).Property(PovInfoPendingTmdb))'
		self.assertEqual(actions, [
			(resolved, 'ActivateWindow(1124,return)'),
			(pending, 'ActivateWindow(1124,return)'),
		])

	def test_season_browser_is_a_unique_normal_window(self):
		window_path = ROOT / 'xml' / 'Custom_1124_PovSeasons.xml'
		root = ET.parse(window_path).getroot()

		self.assertEqual(root.tag, 'window')
		self.assertEqual(root.get('id'), '1124')
		self.assertIsNone(root.get('type'))
		self.assertEqual([node.text for node in root.find('controls').findall('include')], ['GlobalBackground', 'View_527_Seasons'])

		window_ids = []
		for path in (ROOT / 'xml').glob('Custom_*.xml'):
			window_id = ET.parse(path).getroot().get('id')
			if window_id: window_ids.append(window_id)
		self.assertEqual(window_ids.count('1124'), 1)

	def test_dedicated_season_window_owns_season_content_and_native_back(self):
		root = ET.parse(ROOT / 'xml' / 'View_527_Bingie_Seasons.xml').getroot()
		season_list = next(control for control in root.iter('control') if control.get('id') == '527')
		content_include = next(node for node in season_list.findall('include') if node.text == 'PovSeasonBrowserContent')
		self.assertEqual(content_include.get('condition'), 'Window.IsActive(1124)')

		for control_id in ('527', '5027', '60'):
			control = next(control for control in root.iter('control') if control.get('id') == control_id)
			self.assertIn(('Window.IsActive(1124)', 'PreviousMenu'), [(node.get('condition'), node.text) for node in control.findall('onback')])

		content = next(include for include in root.findall('include') if include.get('name') == 'PovSeasonBrowserContent').find('content')
		self.assertEqual(content.get('target'), 'videos')
		self.assertEqual(content.get('browse'), 'never')
		self.assertEqual(content.text, '$VAR[PovSeasonBrowserPath]')

		episode_list = next(control for control in root.iter('control') if control.get('id') == '5027')
		self.assertEqual(episode_list.find('content').text, '$INFO[Container(527).ListItem.FolderPath]')

	def test_custom_season_navigation_has_no_recovery_shims(self):
		for filename in ('IncludesPovInfo.xml', 'Custom_1123_PovInfo.xml', 'MyVideoNav.xml', 'View_527_Bingie_Seasons.xml'):
			contents = (ROOT / 'xml' / filename).read_text()
			self.assertNotIn('PovInfoSeasonBrowserReturn', contents)
			self.assertNotIn('ReplaceWindow(1123)', contents)


if __name__ == '__main__':
	unittest.main()
