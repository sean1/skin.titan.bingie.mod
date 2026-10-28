import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]


class MainMenuTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.root = ET.parse(ROOT / 'xml' / 'IncludesStaticMenus.xml').getroot()

	def test_combined_search_is_a_main_menu_destination_without_duplicate_submenu_entries(self):
		items = self.root.findall("include[@name='StaticMainMenu']/item")
		self.assertEqual([item.findtext('label2') for item in items], ['Home', 'Search', 'Movies', 'TV shows', 'Live TV', 'My Videos', 'My List'])
		search = items[1]
		self.assertEqual(search.findtext("property[@name='hasSubmenu']"), 'False')
		self.assertIn('RunPlugin(plugin://skin.titan.bingie.lite/?mode=get_search_term)', [action.text for action in search.findall('onclick')])
		self.assertFalse(any(item.findtext('label') == 'Search' for item in self.root.findall("include[@name='StaticSubmenu']/item")))
		self.assertEqual(len({item.get('id') for item in items}), len(items))

	def test_live_tv_opens_the_channel_browser(self):
		item = next(item for item in self.root.findall("include[@name='StaticMainMenu']/item") if item.findtext('label2') == 'Live TV')
		url = 'plugin://skin.titan.bingie.lite/?mode=livetv.browse'
		action = 'ActivateWindow(Videos,%s,return)' % url
		self.assertIn(action, [entry.text for entry in item.findall('onclick')])
		self.assertEqual(item.findtext("property[@name='path']"), action)
		self.assertEqual(item.findtext("property[@name='list']"), url)
		self.assertEqual(item.findtext("property[@name='hasSubmenu']"), 'False')

	def test_movies_and_tv_open_combined_browsing_directly_without_submenu_items(self):
		for label, mediatype in (('Movies', 'movie'), ('TV shows', 'tvshow')):
			with self.subTest(mediatype=mediatype):
				item = next(item for item in self.root.findall("include[@name='StaticMainMenu']/item") if item.findtext('label2') == label)
				url = 'plugin://skin.titan.bingie.lite/?mode=refine.browse&mediatype=' + mediatype
				self.assertIn('RunPlugin(%s)' % url, [action.text for action in item.findall('onclick')])
				self.assertEqual(item.findtext("property[@name='hasSubmenu']"), 'False')
				self.assertFalse(any(subitem.findtext("property[@name='mainmenuid']") == item.get('id') for subitem in self.root.findall("include[@name='StaticSubmenu']/item")))


	def test_my_list_quick_submenu_opens_separate_saved_movie_and_show_views(self):
		static_submenu = self.root.find("include[@name='StaticSubmenu']")
		items = [item for item in static_submenu.findall('item') if item.findtext("property[@name='group']") == 'mylist']
		self.assertEqual([item.findtext('label') for item in items], ['Movies', 'TV Shows'])
		self.assertEqual(len({item.get('id') for item in items}), 2)
		for item, mode, action in zip(items, ('build_movie_list', 'build_tvshow_list'), ('my_list_movies', 'my_list_tvshows')):
			with self.subTest(action=action):
				self.assertEqual(item.findtext("property[@name='mainmenuid']"), '5')
				self.assertEqual(item.findtext("property[@name='isSubmenu']"), 'True')
				self.assertEqual(item.findtext('visible'), 'String.IsEqual(Container(900).ListItem.Property(submenuVisibility),mylist)')
				url = item.findtext("property[@name='list']")
				self.assertEqual(urlsplit(url).netloc, 'skin.titan.bingie.lite')
				params = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
				self.assertEqual((params['mode'], params['action']), (mode, action))
				self.assertEqual(params.get('group'), 'mylist')
				self.assertEqual(item.findtext('onclick'), 'ActivateWindow(Videos,%s,return)' % url)
				self.assertEqual(item.findtext("property[@name='path']"), item.findtext('onclick'))

	def test_my_list_quick_submenu_is_visible_and_keyboard_reachable(self):
		bingie = ET.parse(ROOT / 'xml' / 'IncludesBingie.xml').getroot()
		main_menu = bingie.find(".//control[@type='list'][@id='900']")
		self.assertIn(('Integer.IsGreater(Container(4445).NumItems,0)', '4444'), [(action.get('condition'), action.text) for action in main_menu.findall('onright')])
		submenu = bingie.find(".//control[@type='list'][@id='4444']")
		self.assertIn('Integer.IsGreater(Container(4445).NumItems,0)', submenu.findtext('visible'))
		self.assertEqual(submenu.findtext('onleft'), '900')
		self.assertEqual(submenu.findtext('onback'), '900')
		for control_id in ('4444', '4445'):
			control = bingie.find(".//control[@type='list'][@id='%s']" % control_id)
			self.assertTrue(any(content.findtext('include') == 'StaticSubmenu' for content in control.findall('content')))



if __name__ == '__main__':
	unittest.main()
