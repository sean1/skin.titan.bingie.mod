import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowAnimationTimingTests(unittest.TestCase):
	def test_bingie_window_open_fade_uses_300_milliseconds(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesAnimations.xml').getroot()
		includes = [include for include in root.findall('include') if include.get('name') == 'BingieViews_WindowOpen_Fade']
		self.assertEqual(len(includes), 1)
		effects = includes[0].findall("./animation[@type='WindowOpen']/effect[@type='fade']")
		self.assertEqual([(effect.get('start'), effect.get('end'), effect.get('time')) for effect in effects], [('0', '100', '300')])


if __name__ == '__main__':
	unittest.main()
