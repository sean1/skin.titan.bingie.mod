import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
XML = ROOT / 'xml'


class DiscoverHubXmlTests(unittest.TestCase):
	def test_discover_hub_contains_options_and_popular_rows(self):
		root = ET.parse(XML / 'IncludesHubs.xml').getroot()
		discover = next(include for include in root.findall('include') if include.get('name') == 'bingie_items_discover')
		self.assertEqual(
			[(include.get('content'), (include.text or '').strip()) for include in discover.findall('include')],
			[(None, 'Empty_Hub_Alt_Buttons'), ('bingie_pov_hub_item', ''), ('bingie_pov_hub_item', '')],
		)
		rows = discover.findall("include[@content='bingie_pov_hub_item']")
		row_params = [{param.get('name'): param.get('value') for param in row.findall('param')} for row in rows]
		self.assertEqual(row_params, [
			{
				'widgetid': '1510',
				'pollEmpty': 'true',
				'widgetStyle': 'widget_layout_default',
				'label': 'Popular Movies',
				'path': 'plugin://skin.titan.bingie.lite/?mode=build_movie_list&action=tmdb_movies_popular&name=Popular+Movies&limit=10',
			},
			{
				'widgetid': '1520',
				'pollEmpty': 'true',
				'widgetStyle': 'widget_layout_default',
				'label': 'Popular TV Shows',
				'path': 'plugin://skin.titan.bingie.lite/?mode=build_tvshow_list&action=tmdb_tv_popular&name=Popular+TV+Shows&limit=10',
			},
		])

	def test_discover_popular_lists_load_without_staging(self):
		window_text = (XML / 'Custom_1113_Discover_Hub.xml').read_text()
		self.assertNotIn('BingieHubWidgetStage', window_text)
		self.assertNotIn('BingieHubFirstLoadDone', window_text)
		window = ET.parse(XML / 'Custom_1113_Discover_Hub.xml').getroot()
		self.assertEqual((window.findtext('defaultcontrol'), window.find('defaultcontrol').get('always')), ('1510', 'true'))


if __name__ == '__main__':
	unittest.main()
