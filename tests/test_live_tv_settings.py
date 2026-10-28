import unittest
from pathlib import Path
from xml.etree import ElementTree


class LiveTVSettingsTests(unittest.TestCase):
	def test_account_setup_is_not_in_settings(self):
		root = ElementTree.parse(Path(__file__).resolve().parents[1] / 'xml/Settings.xml').getroot()
		items = root.findall(".//control[@id='9000']/content/item")
		self.assertFalse(any(item.findtext('label') == 'Live TV account' or 'mode=livetv.settings' in (item.findtext('onclick') or '') for item in items))
		self.assertEqual(len({item.get('id') for item in items}), len(items))

	def test_guide_setup_is_available_from_settings(self):
		root = ElementTree.parse(Path(__file__).resolve().parents[1] / 'xml/Settings.xml').getroot()
		items = root.findall(".//control[@id='9000']/content/item")
		item = next(item for item in items if item.findtext('label') == 'Live TV guide')
		self.assertEqual(item.findtext('onclick'), 'RunPlugin(plugin://skin.titan.bingie.lite/?mode=livetv.guide_settings)')
		self.assertEqual(len({item.get('id') for item in items}), len(items))

	def test_live_tv_uses_the_existing_navigation_list_view(self):
		root = ElementTree.parse(Path(__file__).resolve().parents[1] / 'xml/Includes.xml').getroot()
		expression = root.find("expression[@name='IsPovLiteNavigationMenu']").text
		self.assertIn('String.Contains(Container.FolderPath,plugin://skin.titan.bingie.lite/)', expression)
		self.assertIn('String.Contains(Container.FolderPath,mode=livetv.)', expression)
		window = ElementTree.parse(Path(__file__).resolve().parents[1] / 'xml/MyVideoNav.xml').getroot()
		self.assertTrue(any(action.get('condition') == '$EXP[IsPovLiteNavigationMenu]' and action.text == 'Container.SetViewMode(50)' for action in window.findall('onload')))


if __name__ == '__main__': unittest.main()
