import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]


class MainMenuTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.root = ET.parse(ROOT / 'xml' / 'IncludesStaticMenus.xml').getroot()

	def test_my_list_sidebar_entry_reaches_the_saved_movie_and_show_chooser(self):
		menu = self.root.find("include[@name='StaticMainMenu']")
		main_item = next(item for item in menu.findall('item') if item.findtext('label2') == 'My List')
		path = 'plugin://skin.titan.bingie.lite/?mode=navigator.my_list&group=mylist'
		self.assertEqual(main_item.findtext("property[@name='list']"), path)
		self.assertIn('ActivateWindow(Videos,%s,return)' % path, [action.text for action in main_item.findall('onclick')])
		self.assertEqual(main_item.findtext("property[@name='path']"), 'ActivateWindow(Videos,%s,return)' % path)
		self.assertEqual(main_item.findtext("property[@name='hasSubmenu']"), 'True')
		self.assertEqual(main_item.findtext("property[@name='submenuVisibility']"), 'mylist')
		self.assertEqual(main_item.findtext("property[@name='labelID']"), 'my-list')
		self.assertNotEqual(main_item.findtext("property[@name='labelID']"), 'my-videos')

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

	def test_my_videos_loads_registered_video_sources_dynamically(self):
		menu = self.root.find("include[@name='StaticMainMenu']")
		main_item = next(item for item in menu.findall('item') if item.findtext('label2') == 'My Videos')
		self.assertEqual(main_item.findtext("property[@name='submenuVisibility']"), 'myvideos')
		self.assertEqual(main_item.findtext("property[@name='hasSubmenu']"), 'True')
		self.assertEqual(main_item.findtext('icon'), 'shortcuts/mylist.png')
		self.assertEqual(main_item.findtext('onclick'), 'ActivateWindow(Videos,plugin://skin.titan.bingie.lite/?mode=navigator.video_sources&group=myvideos,return)')
		self.assertEqual(main_item.findtext("property[@name='list']"), 'plugin://skin.titan.bingie.lite/?mode=navigator.video_sources&group=myvideos')
		self.assertFalse(any(item.findtext("property[@name='group']") == 'myvideos' for item in self.root.find("include[@name='StaticSubmenu']").findall('item')))

		bingie = ET.parse(ROOT / 'xml' / 'IncludesBingie.xml').getroot()
		for control_id in ('4444', '4445'):
			control = bingie.find(".//control[@type='list'][@id='%s']" % control_id)
			self.assertTrue(any(content.find('include') is not None and content.find('include').text == 'StaticSubmenu' for content in control.findall('content')))
			dynamic = next(content for content in control.findall('content') if content.text and 'navigator.video_sources' in content.text)
			self.assertIn('group=$INFO[Container(900).ListItem.Property(submenuVisibility)]', dynamic.text)
			self.assertEqual(dynamic.get('target'), 'videos')
			self.assertEqual(dynamic.get('browse'), 'never')

	def test_video_sources_use_the_native_list_with_add_source_item(self):
		nav = ET.parse(ROOT / 'xml' / 'MyVideoNav.xml').getroot()
		onload_conditions = [action.get('condition') for action in nav.findall('onload') if action.text == 'Container.SetViewMode(50)']
		self.assertIn('String.IsEqual(Container.FolderPath,sources://video/)', onload_conditions)

		list_view = ET.parse(ROOT / 'xml' / 'View_50_List.xml').getroot().find(".//control[@type='list'][@id='50']")
		self.assertIn('String.IsEqual(Container.FolderPath,sources://video/)', list_view.findtext('visible'))

		bingie_view = ET.parse(ROOT / 'xml' / 'View_523_BingieMainLandscape.xml').getroot().find(".//control[@id='523']")
		self.assertIn('!String.IsEqual(Container.FolderPath,sources://video/)', [visible.text for visible in bingie_view.findall('visible')])

	def test_movie_and_tv_submenus_belong_to_their_main_menu_items(self):
		menu = self.root.find("include[@name='StaticMainMenu']")
		menu_ids = {item.findtext('label2'): item.get('id') for item in menu.findall('item')}
		submenu = self.root.find("include[@name='StaticSubmenu']")
		for group, label in (('movies', 'Movies'), ('tvshows', 'TV shows')):
			items = [item for item in submenu.findall('item') if item.findtext("property[@name='group']") == group]
			self.assertTrue(items)
			self.assertTrue(all(item.findtext("property[@name='mainmenuid']") == menu_ids[label] for item in items))
			search_items = [item for item in items if item.findtext('label') == 'Search']
			self.assertEqual(len(search_items), 1)
			actions = [action.text for action in search_items[0].findall('onclick')]
			self.assertIn('SetFocus(1510)', actions)
			self.assertTrue(any(action.startswith('RunPlugin(plugin://skin.titan.bingie.lite/?mode=get_search_term') for action in actions))
			self.assertFalse(any('ActivateWindow' in action for action in actions))
			self.assertNotIn('Pick My Night', [item.findtext('label') for item in items])

	def test_listing_refine_sideblade_replaces_legacy_options_sideblade(self):
		listing_root = ET.parse(ROOT / 'xml' / 'MyVideoNav.xml').getroot()
		refine_menu = listing_root.find(".//control[@type='grouplist'][@id='9000']")
		self.assertIsNotNone(refine_menu)
		self.assertIn('Container.Content(movies) | Container.Content(tvshows)', refine_menu.getparent().findtext('visible') if hasattr(refine_menu, 'getparent') else ''.join(listing_root.itertext()))
		buttons = refine_menu.findall("control[@type='button']")
		self.assertEqual([button.findtext('label').split(':', 1)[0] for button in buttons], [
			'Preset', 'Sort by', 'Order', 'Genres', 'Theme', 'Year', 'Released only', 'Released within', 'Minimum rating', 'Minimum votes', 'Maximum votes', 'Language', 'Original network', 'MPAA rating',
			'SHOW RESULTS', 'CLEAR ALL'
		])
		self.assertEqual([button.findtext('onclick') for button in buttons], [
			'RunPlugin(plugin://skin.titan.bingie.lite/?mode=refine.%s)' % mode
			for mode in ('preset', 'sort', 'order', 'genres', 'theme', 'year', 'released', 'release_window', 'rating', 'votes', 'max_votes', 'language', 'network', 'mpaa', 'apply', 'clear')
		])
		self.assertEqual(buttons[0].get('id'), '9112')
		for button in buttons[:14]:
			self.assertIn('Window(Home).Property(Refine.', button.findtext('label'))
		theme_button = buttons[4]
		self.assertEqual(theme_button.get('id'), '9115')
		self.assertIsNone(theme_button.find('visible'))
		released_button = buttons[6]
		self.assertEqual(released_button.get('id'), '9113')
		self.assertIsNone(released_button.find('visible'))
		release_window_button = buttons[7]
		self.assertEqual(release_window_button.get('id'), '9116')
		self.assertIsNone(release_window_button.find('visible'))
		max_votes_button = buttons[10]
		self.assertEqual(max_votes_button.get('id'), '9114')
		self.assertIsNone(max_votes_button.find('visible'))
		network_button = buttons[12]
		self.assertEqual(network_button.get('id'), '9111')
		self.assertEqual(network_button.findtext('visible'), 'Container.Content(tvshows)')
		mpaa_button = buttons[13]
		self.assertEqual(mpaa_button.get('id'), '9110')
		self.assertEqual(mpaa_button.findtext('visible'), 'Container.Content(movies)')
		self.assertEqual(buttons[-2].findtext('label'), 'SHOW RESULTS')
		self.assertEqual(buttons[-1].findtext('label'), 'CLEAR ALL')
		self.assertEqual(refine_menu.findtext('onleft'), '523')
		self.assertEqual(refine_menu.findtext('onback'), '523')
		right_actions = refine_menu.findall('onright')
		self.assertEqual([(action.get('condition'), action.text) for action in right_actions], [
			('String.IsEqual(Window(Home).Property(Refine.Changed),true)', 'RunPlugin(plugin://skin.titan.bingie.lite/?mode=refine.apply)'),
			('!String.IsEqual(Window(Home).Property(Refine.Changed),true)', '523'),
		])

		view_root = ET.parse(ROOT / 'xml' / 'View_523_BingieMainLandscape.xml').getroot()
		all_left_actions = view_root.findall('.//onleft')
		initialize_actions = [action for action in all_left_actions if (action.text or '').strip() == 'RunPlugin(plugin://skin.titan.bingie.lite/?mode=refine.initialize)']
		self.assertEqual(len(initialize_actions), 1)
		self.assertEqual(initialize_actions[0].get('condition'), 'Container.Content(movies) | Container.Content(tvshows)')
		open_actions = [action for action in all_left_actions if (action.text or '').strip() == '9000']
		self.assertEqual(len(open_actions), 1)
		self.assertEqual(open_actions[0].get('condition'), 'Container.Content(movies) | Container.Content(tvshows)')

		for filename in ('MyPrograms.xml', 'MyPlaylist.xml', 'MyFavourites.xml', 'AddonBrowser.xml'):
			root = ET.parse(ROOT / 'xml' / filename).getroot()
			self.assertIsNone(root.find(".//include[.='SideBladeModern']"), filename)
			self.assertIsNone(root.find(".//control[@id='9000']"), filename)
		includes = ET.parse(ROOT / 'xml' / 'IncludesViews.xml').getroot()
		for name in ('videoViewIds', 'genericViewIds'):
			self.assertIsNone(includes.find("include[@name='%s']/menucontrol" % name))
		for filename in ('View_50_List.xml', 'View_525_Bingie_Episodes.xml', 'View_527_Bingie_Seasons.xml'):
			root = ET.parse(ROOT / 'xml' / filename).getroot()
			self.assertFalse(any((action.text or '').strip() == '9000' for action in root.findall('.//onleft')), filename)
		context_includes = ET.parse(ROOT / 'xml' / 'IncludesContextMenu.xml').getroot()
		self.assertIsNone(context_includes.find("include[@name='SideBladeModern']"))
		self.assertIsNone(context_includes.find("include[@name='SideBladeViewCommands']"))
		self.assertIsNotNone(context_includes.find("include[@name='DialogContextMenuModern']"))
		self.assertIsNotNone(context_includes.find("include[@name='SideBladeMenuButton']"))

	def test_power_menus_omit_reboot_and_poweroff(self):
		static_root = ET.parse(ROOT / 'xml' / 'IncludesStaticMenus.xml').getroot()
		static_submenu = static_root.find("include[@name='StaticSubmenu']")
		surfaces = {
			'side submenu': [item for item in static_submenu.findall('item') if item.findtext("property[@name='group']") == 'powermenu'],
			'static power menu': static_root.find("include[@name='StaticPowerMenu']").findall('item'),
			'login power menu': ET.parse(ROOT / 'xml' / 'DialogButtonMenu.xml').getroot().find(".//control[@type='list'][@id='3110']/content").findall('item'),
		}
		for name, items in surfaces.items():
			with self.subTest(name=name):
				actions = {action.text for item in items for action in item.findall('onclick')}
				self.assertNotIn('Reboot', actions)
				self.assertFalse({'ShutDown', 'PowerDown'} & actions)
				self.assertFalse(any(keyword in (item.findtext('icon') or '').lower() for item in items for keyword in ('reboot', 'shutdown')))
				self.assertIn('Quit()', actions)

	def test_submenu_focus_keeps_main_menu_open(self):
		root = ET.parse(ROOT / 'xml' / 'Includes.xml').getroot()
		expression = root.find("expression[@name='IsMainMenuOpened']")
		open_conditions = {condition.strip() for condition in expression.text.split('|')}
		self.assertIn('Control.HasFocus(900)', open_conditions)
		self.assertIn('Control.HasFocus(4444)', open_conditions)

	def test_menu_state_is_cleared_after_navigation(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesBingie.xml').getroot()
		window_props = root.find("include[@name='CustomBingieWinProps']")
		for event in ('onload', 'onunload'):
			actions = {action.text for action in window_props.findall(event)}
			self.assertIn('ClearProperty(ShowViewSubMenu,Home)', actions)

if __name__ == '__main__':
	unittest.main()
