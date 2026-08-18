import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
XML = ROOT / 'xml'


def parse(name):
	return ET.parse(XML / name).getroot()


def named_element(root, tag, name):
	for element in root.findall(tag):
		if element.get('name') == name:
			return element
	raise AssertionError(f'{tag} {name} not found')


class SharedXmlIncludeTests(unittest.TestCase):
	def test_settings_windows_are_thin_equivalent_wrappers(self):
		for filename in ('mainWindow.xml', 'service-LibreELEC-Settings-mainWindow.xml', 'service-OpenELEC-Settings-mainWindow.xml'):
			with self.subTest(filename=filename):
				root = parse(filename)
				self.assertEqual(root.tag, 'window')
				self.assertEqual(root.get('id'), '5534')
				self.assertEqual(root.findtext('defaultcontrol'), '1000')
				self.assertEqual(root.find('defaultcontrol').get('always'), 'true')
				controls = root.find('controls')
				self.assertEqual(len(controls), 1)
				self.assertEqual(controls[0].tag, 'include')
				self.assertEqual(controls[0].text, 'SettingsMainWindowControls')

		shared = named_element(parse('IncludesSettingsMainWindow.xml'), 'include', 'SettingsMainWindowControls')
		self.assertGreater(len(shared.findall('.//control')), 100)
		self.assertIsNotNone(shared.find(".//control[@id='1000']"))
		self.assertIsNotNone(shared.find(".//control[@id='1504']"))

	def test_upnext_wrappers_only_supply_intentional_variants(self):
		expected = {
			'script-upnext-upnext-simple.xml': {
				'over_minute_label': '$ADDON[service.upnext 30049]',
				'under_minute_label': '$ADDON[service.upnext 30037]',
				'under_minute_focus_texture': 'bingie/border/slimframefo.png',
			},
			'script-upnext-stillwatching-simple.xml': {
				'over_minute_label': '$ADDON[service.upnext 30010]',
				'under_minute_label': '$ADDON[service.upnext 30035]',
				'under_minute_focus_texture': 'bingie/border/default_button_fo_4.png',
				'show_still_watching_label': 'true',
			},
		}
		for filename, expected_params in expected.items():
			with self.subTest(filename=filename):
				root = parse(filename)
				self.assertEqual(root.findtext('defaultcontrol'), '20')
				self.assertEqual([node.text for node in root.findall('onload')], ['Dialog.Close(fullscreeninfo,true)', 'Dialog.Close(videoosd,true)'])
				include = root.find("./controls/include[@content='UpNextSimpleControls']")
				self.assertIsNotNone(include)
				self.assertEqual({param.get('name'): param.get('value') for param in include.findall('param')}, expected_params)

		shared = named_element(parse('IncludesUpNextSimple.xml'), 'include', 'UpNextSimpleControls')
		self.assertEqual(shared.find("./param[@name='show_still_watching_label']").get('default'), 'false')
		self.assertEqual(shared.find(".//control[@id='10']/label").text, '    $PARAM[over_minute_label]')
		self.assertEqual(shared.find(".//control[@id='11']/label").text, '    $PARAM[under_minute_label]')
		self.assertEqual(shared.find(".//control[@id='11']/texturefocus").text, '$PARAM[under_minute_focus_texture]')
		still_watching = next(control for control in shared.findall('.//control') if control.get('type') == 'label' and control.findtext('label') == '$ADDON[service.upnext 30024]')
		self.assertEqual(still_watching.findtext('visible'), '$PARAM[show_still_watching_label]')

	def test_detail_pages_share_focus_resolution_but_keep_local_fallbacks(self):
		variables = parse('IncludesVariables.xml')
		expected = {
			'PovInfoResolvedFanart': (
				'Control.HasFocus(560) + !String.IsEmpty(Container(560).ListItem.Property(fanart))',
				'Control.HasFocus(563) + !String.IsEmpty(Container(563).ListItem.Property(fanart))',
				'Window.IsActive(1123)', None,
			),
			'PovInfoResolvedPlot': (
				'Control.HasFocus(560) + !String.IsEmpty(Container(560).ListItem.Property(plot))',
				'Control.HasFocus(563) + !String.IsEmpty(Container(563).ListItem.Property(plot))',
				'Window.IsActive(1123)', None,
			),
		}
		for name, conditions in expected.items():
			variable = named_element(variables, 'variable', name)
			self.assertEqual([value.get('condition') for value in variable.findall('value')], list(conditions))

		fanart_values = [value.text for value in named_element(variables, 'variable', 'PovInfoResolvedFanart').findall('value')]
		self.assertEqual(fanart_values[-2:], ['$INFO[Window(Home).Property(PovInfoFanart)]', '$INFO[Window.Property(PovInfoFanart)]'])
		plot_values = [value.text for value in named_element(variables, 'variable', 'PovInfoResolvedPlot').findall('value')]
		self.assertEqual(plot_values[-2:], ['$INFO[Window(Home).Property(PovInfoPlot)]', '$VAR[BingieDialogInfoPlot]'])

		for filename in ('IncludesDialogVideoInfo.xml', 'IncludesPovInfo.xml'):
			with self.subTest(filename=filename):
				text = (XML / filename).read_text()
				self.assertIn('$VAR[PovInfoResolvedFanart]', text)
				self.assertIn('$VAR[PovInfoResolvedPlot]', text)
				self.assertNotIn('String.IsEmpty($VAR[Pov', text)

	def test_global_include_registry_loads_shared_files(self):
		files = {include.get('file') for include in parse('Includes.xml').findall('include')}
		self.assertIn('IncludesSettingsMainWindow.xml', files)
		self.assertIn('IncludesUpNextSimple.xml', files)

	def test_movie_show_cards_render_country_flags_or_language_badges(self):
		landscape = parse('IncludesViewsLayoutLandscape.xml')
		flag_include = named_element(landscape, 'include', 'LandscapeCardCountryFlag')
		flag = flag_include.find("control[@type='image']")
		self.assertEqual(flag.findtext('visible'), '$EXP[IsMovieOrTvShowListItem] + !String.IsEmpty(ListItem.Property(card_flag))')
		self.assertEqual(flag.findtext('texture'), '$INFO[ListItem.Property(card_flag)]')
		self.assertEqual(flag.findtext('left'), '16')
		language = named_element(landscape, 'include', 'LandscapeCardLanguageBadge').find("control[@type='group']")
		self.assertEqual(language.findtext('visible'), '$EXP[IsMovieOrTvShowListItem] + String.IsEmpty(ListItem.Property(card_flag)) + !String.IsEmpty(ListItem.Property(card_language))')
		self.assertIsNone(language.find("control[@type='image']"))
		self.assertEqual(language.find(".//control[@type='label']").findtext('label'), '$INFO[ListItem.Property(card_language)]')
		year_groups = named_element(landscape, 'include', 'LandscapeCardYearLabel').findall("control[@type='group']")
		self.assertEqual([group.findtext('left') for group in year_groups], ['16', '58'])
		self.assertIn('String.IsEmpty(ListItem.Property(card_flag))', year_groups[0].findtext('visible'))
		self.assertIn('String.IsEmpty(ListItem.Property(card_language))', year_groups[0].findtext('visible'))
		self.assertIn('!String.IsEmpty(ListItem.Property(card_language))', year_groups[1].findtext('visible'))
		dialog = parse('IncludesDialogVideoInfo.xml')
		self.assertEqual([(include.text or '').strip() for include in dialog.findall(".//include")].count('LandscapeCardYearLabel'), 1)
		self.assertEqual([(include.text or '').strip() for include in dialog.findall(".//include")].count('LandscapeCardCountryFlag'), 1)
		self.assertEqual([(include.text or '').strip() for include in dialog.findall(".//include")].count('LandscapeCardLanguageBadge'), 1)
		forwarded = [property_node.text for property_node in dialog.findall(".//property[@name='card_flag']")]
		self.assertEqual(forwarded, [
			'$INFO[Container(565).ListItemAbsolute($PARAM[index]).Property(card_flag)]',
			'$INFO[Container(566).ListItemAbsolute($PARAM[index]).Property(card_flag)]'
		])
		forwarded_languages = [property_node.text for property_node in dialog.findall(".//property[@name='card_language']")]
		self.assertEqual(forwarded_languages, [
			'$INFO[Container(565).ListItemAbsolute($PARAM[index]).Property(card_language)]',
			'$INFO[Container(566).ListItemAbsolute($PARAM[index]).Property(card_language)]'
		])


if __name__ == '__main__':
	unittest.main()
