import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MainMenuTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.root = ET.parse(ROOT / 'xml' / 'IncludesStaticMenus.xml').getroot()

	def test_static_main_menu_is_lean_and_content_first(self):
		menu = self.root.find("include[@name='StaticMainMenu']")
		self.assertEqual([item.get('id') for item in menu.findall('item')], ['1', '2', '3'])
		self.assertEqual([item.findtext('label2') for item in menu.findall('item')], ['Home', 'Movies', 'TV shows'])

	def test_home_widgets_exclude_popular_rows(self):
		menu = self.root.find("include[@name='StaticMainMenu']")
		home = next(item for item in menu.findall('item') if item.findtext('label2') == 'Home')
		self.assertEqual([prop.text for prop in home.findall('property') if prop.get('name', '').startswith('widgetName')], [
			'Continue Watching Movies', 'Continue Watching TV', 'Trending Movies Today', 'Trending TV Shows Today'
		])
		rows = self.root.find("include[@name='StaticHomeWidgetRows']")
		self.assertEqual([include.find("param[@name='widgetName']").get('value') for include in rows.findall("include[@content='widget_header_multi']")], [
			'Trending Movies Today', 'Trending TV Shows Today', 'Continue Watching Movies', 'Continue Watching TV'
		])

	def test_home_widget_rows_show_five_items_each(self):
		rows = self.root.find("include[@name='StaticHomeWidgetRows']")
		widgets = rows.findall("include[@content='widget_base']")
		self.assertEqual([widget.find("param[@name='widgetLimit']").get('value') for widget in widgets], ['5'] * 4)

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
			pick_items = [item for item in items if item.findtext('label') == 'Pick My Night']
			self.assertEqual(len(pick_items), 1)
			pick_actions = [action.text for action in pick_items[0].findall('onclick')]
			expected_mediatype = 'movie' if group == 'movies' else 'tvshow'
			self.assertTrue(any(action.startswith('RunPlugin(') and 'mediatype=%s' % expected_mediatype in action for action in pick_actions))

	def test_movie_submenu_keeps_unique_feeds_and_browse_refine(self):
		submenu = self.root.find("include[@name='StaticSubmenu']")
		items = [item for item in submenu.findall('item') if item.findtext("property[@name='group']") == 'movies']
		self.assertEqual([item.get('id') for item in items], [str(value) for value in range(1, 5)])
		self.assertEqual([item.findtext('label') for item in items], [
			'Browse & Refine', 'Trending Movies This Week', 'Pick My Night', 'Search'
		])
		actions = {item.findtext('label'): [action.text for action in item.findall('onclick')] for item in items}
		self.assertTrue(any('tmdb_movies_popular' in action for action in actions['Browse & Refine']))
		self.assertNotIn('Top Rated Movies', actions)

		bingie_root = ET.parse(ROOT / 'xml' / 'IncludesBingie.xml').getroot()
		movie_centering = bingie_root.find(".//control[@type='list'][@id='4444']/animation[@condition='String.IsEqual(Container(900).ListItem.Property(submenuVisibility),movies)']")
		self.assertEqual(movie_centering.get('end'), '0,128')

	def test_tv_submenu_keeps_unique_feeds_and_browse_refine(self):
		submenu = self.root.find("include[@name='StaticSubmenu']")
		items = [item for item in submenu.findall('item') if item.findtext("property[@name='group']") == 'tvshows']
		self.assertEqual([item.get('id') for item in items], [str(value) for value in range(1, 6)])
		self.assertEqual([item.findtext('label') for item in items], [
			'Browse & Refine', 'Trending TV Shows This Week', 'New Series', 'Pick My Night', 'Search'
		])
		actions = {item.findtext('label'): [action.text for action in item.findall('onclick')] for item in items}
		self.assertTrue(any('tmdb_tv_popular' in action for action in actions['Browse & Refine']))
		self.assertTrue(any('tmdb_tv_new_series' in action for action in actions['New Series']))
		self.assertNotIn('By Original Network', actions)
		self.assertNotIn('Top Rated TV Shows', actions)

		bingie_root = ET.parse(ROOT / 'xml' / 'IncludesBingie.xml').getroot()
		tv_centering = bingie_root.find(".//control[@type='list'][@id='4444']/animation[@condition='String.IsEqual(Container(900).ListItem.Property(submenuVisibility),tvshows)']")
		self.assertEqual(tv_centering.get('end'), '0,185')

	def test_discover_is_absent_from_static_navigation(self):
		menu = self.root.find("include[@name='StaticMainMenu']")
		self.assertNotIn('Discover', [item.findtext('label2') for item in menu.findall('item')])
		submenu = self.root.find("include[@name='StaticSubmenu']")
		items = [item for item in submenu.findall('item') if item.findtext("property[@name='group']") == 'discover']
		self.assertEqual(items, [])

	def test_listing_refine_sideblade_replaces_legacy_options_sideblade(self):
		listing_root = ET.parse(ROOT / 'xml' / 'MyVideoNav.xml').getroot()
		refine_menu = listing_root.find(".//control[@type='grouplist'][@id='9000']")
		self.assertIsNotNone(refine_menu)
		self.assertIn('Container.Content(movies) | Container.Content(tvshows)', refine_menu.getparent().findtext('visible') if hasattr(refine_menu, 'getparent') else ''.join(listing_root.itertext()))
		buttons = refine_menu.findall("control[@type='button']")
		self.assertEqual([button.findtext('label').split(':', 1)[0] for button in buttons], [
			'Preset', 'Sort by', 'Order', 'Genres', 'Year', 'Minimum rating', 'Minimum votes', 'Language', 'Original network', 'MPAA rating', 'SHOW RESULTS', 'CLEAR ALL'
		])
		self.assertEqual([button.findtext('onclick') for button in buttons], [
			'RunPlugin(plugin://skin.titan.bingie.lite/?mode=refine.%s)' % mode
			for mode in ('preset', 'sort', 'order', 'genres', 'year', 'rating', 'votes', 'language', 'network', 'mpaa', 'apply', 'clear')
		])
		self.assertEqual(buttons[0].get('id'), '9112')
		for button in buttons[:10]:
			self.assertIn('Window(Home).Property(Refine.', button.findtext('label'))
		network_button = buttons[8]
		self.assertEqual(network_button.get('id'), '9111')
		self.assertEqual(network_button.findtext('visible'), 'Container.Content(tvshows)')
		mpaa_button = buttons[9]
		self.assertEqual(mpaa_button.get('id'), '9110')
		self.assertEqual(mpaa_button.findtext('visible'), 'Container.Content(movies)')
		self.assertEqual(buttons[-2].findtext('label'), 'SHOW RESULTS')
		self.assertEqual(buttons[-1].findtext('label'), 'CLEAR ALL')

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

	def test_main_menu_omits_profile_switcher(self):
		text = (ROOT / 'xml' / 'IncludesBingie.xml').read_text()
		for marker in ('id="40000"', 'Control.HasFocus(40000)', 'System.ProfileThumb', 'System.ProfileName', '$LOCALIZE[31839]'):
			self.assertNotIn(marker, text)

	def test_submenu_is_visible_without_a_right_key_press(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesBingie.xml').getroot()
		submenu = root.find(".//control[@type='list'][@id='4444']")
		visibility = (submenu.findtext('visible') or '').strip()
		self.assertEqual(visibility, '[Control.HasFocus(900) | Control.HasFocus(4444)] + Integer.IsGreater(Container(4445).NumItems,0)')
		static_submenu = self.root.find("include[@name='StaticSubmenu']")
		largest_group = max(sum(item.findtext("property[@name='group']") == group for item in static_submenu.findall('item')) for group in ('movies', 'tvshows'))
		layouts = [*submenu.findall('itemlayout'), *submenu.findall('focusedlayout')]
		row_height = max(int(layout.get('height')) for layout in layouts)
		item_gap = int(submenu.findtext('itemgap'))
		self.assertGreaterEqual(int(submenu.findtext('height')), largest_group * row_height + (largest_group - 1) * item_gap)
		sideblade = root.find("include[@name='SideBladeSubMenu']")
		group = sideblade.find("control[@type='group']")
		self.assertEqual(int(group.findtext('posx')) + int(submenu.findtext('posx')) + 10, 450)
		vertical_alignment_conditions = [animation.get('condition', '') for animation in group.findall('animation')]
		self.assertFalse(any('Container(900).NumItems' in condition or 'Container(900).Position' in condition for condition in vertical_alignment_conditions))
		main = root.find(".//control[@type='list'][@id='900']")
		main_row_height = int(main.find('itemlayout').get('height'))
		main_count_offset = next(int(animation.get('end').split(',')[1]) for animation in main.findall('animation') if 'Container(900).NumItems,3' in animation.get('condition', ''))
		main_top = int(main.findtext('top')) + main_count_offset
		main_items = self.root.find("include[@name='StaticMainMenu']").findall('item')
		base_submenu_top = int(group.findtext('posy')) + int(submenu.findtext('posy'))
		for submenu_group in ('movies', 'tvshows'):
			main_index = next(index for index, item in enumerate(main_items) if item.findtext("property[@name='submenuVisibility']") == submenu_group)
			item_count = sum(item.findtext("property[@name='group']") == submenu_group for item in static_submenu.findall('item'))
			content_height = item_count * row_height + (item_count - 1) * item_gap
			offset = next(int(animation.get('end').split(',')[1]) for animation in submenu.findall('animation') if animation.get('condition', '').endswith(',%s)' % submenu_group))
			self.assertLessEqual(abs(2 * (base_submenu_top + offset) + content_height - (2 * (main_top + main_index * main_row_height) + main_row_height)), 1)

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
