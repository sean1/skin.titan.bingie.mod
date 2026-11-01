import ast
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from tests.test_livetv import load_livetv

ROOT = Path(__file__).resolve().parents[1]


class LiveTVNavigationTests(unittest.TestCase):
	def test_routes_keep_channel_search_and_remove_guide_screens(self):
		tree = ast.parse((ROOT / 'resources/lib/routing.py').read_text(encoding='utf-8'))
		routes = next(node.value for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'POV_ROUTES' for target in node.targets))
		keys = {key.value for key in routes.keys if isinstance(key, ast.Constant)}
		self.assertTrue({'livetv.browse', 'livetv.channels', 'livetv.refine', 'livetv.search', 'livetv.search_results', 'livetv.guide_settings'} <= keys)
		self.assertTrue({'livetv.guide', 'livetv.guide_channels', 'livetv.schedule'}.isdisjoint(keys))

	def test_sports_menu_keeps_browse_and_search_without_guide(self):
		tree = ET.parse(ROOT / 'xml/MyVideoNav.xml')
		buttons = {control.findtext('label'): ' '.join(element.text or '' for element in control.findall('onclick')) for control in tree.iter('control') if control.find('label') is not None}
		self.assertIn('mode=livetv.refine', buttons['Browse channels'])
		self.assertIn('mode=livetv.search', buttons['Search Live TV'])
		self.assertNotIn('TV guide', buttons)
		self.assertFalse(any('mode=livetv.guide)' in action or 'mode=livetv.guide_channels' in action or 'mode=livetv.schedule' in action for action in buttons.values()))

	def test_browse_offers_channels_and_search_without_guide(self):
		live = load_livetv()
		with patch.object(live, '_load', return_value={'server': 'test'}), patch.object(live, '_catalog', return_value=[{'category_id': '7', 'category_name': 'Sports'}]), patch.object(live, '_item') as item, patch.object(live, '_finish'):
			live.browse({})
		modes = {call.args[1]['mode'] for call in item.call_args_list}
		self.assertTrue({'livetv.search', 'livetv.channels'} <= modes)
		self.assertTrue({'livetv.guide', 'livetv.guide_channels', 'livetv.schedule'}.isdisjoint(modes))


if __name__ == '__main__': unittest.main()
