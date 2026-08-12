import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
XML = ROOT / 'xml'


class DiscoverHubXmlTests(unittest.TestCase):
	def test_discover_hub_contains_options_and_recommendation_rows(self):
		root = ET.parse(XML / 'IncludesHubs.xml').getroot()
		discover = next(include for include in root.findall('include') if include.get('name') == 'bingie_items_discover')
		self.assertEqual(
			[(include.get('content'), (include.text or '').strip()) for include in discover.findall('include')],
			[(None, 'Empty_Hub_Alt_Buttons'), ('bingie_pov_hub_action_item', ''), ('bingie_pov_hub_item', ''), ('bingie_pov_hub_item', '')],
		)
		action_params = {param.get('name'): param.get('value') for param in discover.find("include[@content='bingie_pov_hub_action_item']").findall('param')}
		self.assertEqual(action_params, {
			'widgetid': '1510',
			'label': 'Discover Options',
			'path': 'plugin://skin.titan.bingie.lite/?mode=navigator.discover_hub_actions&name=32451',
		})
		action_include = next(include for include in root.findall('include') if include.get('name') == 'bingie_pov_hub_action_item')
		action_list = action_include.find("control[@type='fixedlist']")
		self.assertEqual(action_list.find('visible').get('allowhiddenfocus'), 'true')
		rows = discover.findall("include[@content='bingie_pov_hub_item']")
		row_params = [{param.get('name'): param.get('value') for param in row.findall('param')} for row in rows]
		self.assertEqual(row_params, [
			{
				'widgetid': '1520',
				'pollEmpty': 'true',
				'widgetStyle': 'widget_layout_default',
				'label': 'Recommended for You • Movies',
				'path': '$VAR[BingieDiscoverBecauseMoviesPath]',
			},
			{
				'widgetid': '1530',
				'pollEmpty': 'true',
				'widgetStyle': 'widget_layout_default',
				'label': 'Recommended for You • TV Shows',
				'path': '$VAR[BingieDiscoverBecauseTVPath]',
			},
		])

	def test_discover_recommendations_load_without_staging(self):
		hubs = ET.parse(XML / 'IncludesHubs.xml').getroot()
		for name in ('BingieDiscoverBecauseMoviesPath', 'BingieDiscoverBecauseTVPath'):
			with self.subTest(name=name):
				variable = next(variable for variable in hubs.findall('variable') if variable.get('name') == name)
				values = variable.findall('value')
				self.assertEqual(len(values), 1)
				self.assertIsNone(values[0].get('condition'))
				self.assertTrue(values[0].text.startswith('plugin://skin.titan.bingie.lite/'))

		window_text = (XML / 'Custom_1113_Discover_Hub.xml').read_text()
		self.assertNotIn('BingieHubWidgetStage', window_text)
		self.assertNotIn('BingieHubFirstLoadDone', window_text)
		window = ET.parse(XML / 'Custom_1113_Discover_Hub.xml').getroot()
		self.assertEqual((window.findtext('defaultcontrol'), window.find('defaultcontrol').get('always')), ('1510', 'true'))


if __name__ == '__main__':
	unittest.main()
