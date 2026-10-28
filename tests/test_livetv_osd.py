import ast
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from tests.test_livetv import load_livetv

ROOT = Path(__file__).resolve().parents[1]


class LiveTVOSDTests(unittest.TestCase):
	def test_switch_button_requires_playback_and_eligible_alternative(self):
		root = ET.parse(ROOT / 'xml/IncludesOSD.xml').getroot()
		layout = root.find("./include[@name='OSDButtons_Bingie_Layout']")
		button = layout.find(".//control[@id='216']")
		self.assertIsNotNone(button)
		visibility = button.findtext('visible')
		self.assertIn('Player.HasVideo', visibility)
		self.assertIn('String.IsEqual(Window(Home).Property(BingieLiveTVCanSwitch),true)', visibility)
		action = button.findtext('onclick')
		self.assertTrue(action.startswith('RunPlugin(') and action.endswith(')'))
		params = parse_qs(urlsplit(action[len('RunPlugin('):-1]).query)
		self.assertEqual(params.get('mode'), ['livetv.next'])
		routes = ast.parse((ROOT / 'resources/lib/routing.py').read_text())
		mapping = next(node.value for node in routes.body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'POV_ROUTES' for t in node.targets))
		self.assertIn('livetv.next', [node.value for node in mapping.keys if isinstance(node, ast.Constant)])
		self.assertIn('Try another feed', root.find("./variable[@name='osd_button_description']/value[@condition='Control.HasFocus(216)']").text)

	def test_root_action_is_non_playable(self):
		live = load_livetv()
		with patch.object(live, '_load', return_value={'server': 'x', 'username': 'u', 'password': 'p'}), patch.object(live, '_catalog', return_value=[]), patch.object(live, '_item') as item, patch.object(live, '_finish'):
			live.browse({})
		self.assertIn(('Try another feed', {'mode': 'livetv.next', 'isFolder': 'false'}, False, None, False), [call.args for call in item.call_args_list])


if __name__ == '__main__': unittest.main()
