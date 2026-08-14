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
		self.assertEqual([item.get('id') for item in menu.findall('item')], ['1', '2', '3', '4'])
		self.assertEqual([item.findtext('label2') for item in menu.findall('item')], ['Home', 'Movies', 'TV shows', 'Discover'])

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

	def test_discover_options_are_owned_by_discover_submenu(self):
		menu = self.root.find("include[@name='StaticMainMenu']")
		discover = next(item for item in menu.findall('item') if item.findtext('label2') == 'Discover')
		self.assertEqual(discover.findtext("property[@name='submenuVisibility']"), 'discover')
		self.assertEqual(discover.findtext("property[@name='hasSubmenu']"), 'True')
		submenu = self.root.find("include[@name='StaticSubmenu']")
		items = [item for item in submenu.findall('item') if item.findtext("property[@name='group']") == 'discover']
		self.assertEqual([item.findtext('label') for item in items], ['Pick My Night', 'Build a Movie Mix', 'Build a TV Mix'])
		self.assertTrue(all(item.findtext("property[@name='mainmenuid']") == discover.get('id') for item in items))
		pick_actions = [action.text for action in items[0].findall('onclick')]
		self.assertIn('SetFocus(1510)', pick_actions)
		self.assertTrue(any(action.startswith('RunPlugin(') for action in pick_actions))
		self.assertTrue(all(item.findtext('onclick').startswith('ActivateWindow(Videos,') for item in items[1:]))

	def test_video_sideblade_searches_run_prompt_as_actions(self):
		root = ET.parse(ROOT / 'xml' / 'MyVideoNav.xml').getroot()
		for control_id in ('388', '389'):
			button = root.find(".//control[@type='button'][@id='%s']" % control_id)
			self.assertTrue(button.findtext('onclick').startswith('RunPlugin(plugin://skin.titan.bingie.lite/?mode=get_search_term'))

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
		main_count_offset = next(int(animation.get('end').split(',')[1]) for animation in main.findall('animation') if 'Container(900).NumItems,4' in animation.get('condition', ''))
		main_top = int(main.findtext('top')) + main_count_offset
		main_items = self.root.find("include[@name='StaticMainMenu']").findall('item')
		base_submenu_top = int(group.findtext('posy')) + int(submenu.findtext('posy'))
		for submenu_group in ('movies', 'tvshows', 'discover'):
			main_index = next(index for index, item in enumerate(main_items) if item.findtext("property[@name='submenuVisibility']") == submenu_group)
			item_count = sum(item.findtext("property[@name='group']") == submenu_group for item in static_submenu.findall('item'))
			content_height = item_count * row_height + (item_count - 1) * item_gap
			offset = next(int(animation.get('end').split(',')[1]) for animation in submenu.findall('animation') if animation.get('condition', '').endswith(',%s)' % submenu_group))
			self.assertEqual(2 * (base_submenu_top + offset) + content_height, 2 * (main_top + main_index * main_row_height) + main_row_height)

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
			self.assertIn('ClearProperty(submenu,Home)', actions)

if __name__ == '__main__':
	unittest.main()
