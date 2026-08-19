import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
HUBS = ROOT / 'xml' / 'IncludesHubs.xml'


class MediaHubXmlTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.root = ET.parse(HUBS).getroot()
		cls.variables = {
			variable.get('name'): [value.text for value in variable.findall('value')]
			for variable in cls.root.findall('variable')
		}

	def _hub_paths(self, include_name):
		hub = self.root.find("include[@name='%s']" % include_name)
		paths = []
		for row in hub.findall("include[@content='bingie_pov_hub_item']"):
			label = row.find("param[@name='label']").get('value')
			path = row.find("param[@name='path']").get('value')
			if path.startswith('$VAR['): paths.extend((label, value) for value in self.variables[path[5:-1]])
			else: paths.append((label, path))
		return paths

	def test_movie_and_tv_hub_rows_request_five_items_and_next_card(self):
		for include_name, expected_count in (('bingie_items_movies', 6), ('bingie_items_tvshows', 7)):
			with self.subTest(include_name=include_name):
				paths = self._hub_paths(include_name)
				self.assertEqual(len(paths), expected_count)
				for label, path in paths:
					query = parse_qs(urlsplit(path).query)
					self.assertEqual(query.get('name'), [label])
					self.assertEqual(query.get('limit'), ['5'])
					self.assertEqual(query.get('hub_next'), ['true'])

	def test_shared_hub_container_does_not_clip_sixth_next_card(self):
		hub_item = self.root.find("include[@name='bingie_pov_hub_item']")
		self.assertGreaterEqual(int(hub_item.find('.//content').get('limit')), 6)


if __name__ == '__main__':
	unittest.main()
