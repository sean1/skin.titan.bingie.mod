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

	def test_visible_detail_shelves_use_300_millisecond_scrolls(self):
		targets = {
			'IncludesPovActor.xml': (610, 620, 630),
			'IncludesPovInfo.xml': (550, 563, 560),
			'IncludesDialogVideoInfo.xml': (550, 563, 560),
		}
		for filename, control_ids in targets.items():
			root = ET.parse(ROOT / 'xml' / filename).getroot()
			for control_id in control_ids:
				with self.subTest(filename=filename, control_id=control_id):
					panels = [control for control in root.iter('control') if control.get('type') == 'panel' and control.get('id') == str(control_id)]
					self.assertEqual(len(panels), 1)
					scrolltime = panels[0].find('scrolltime')
					self.assertEqual((panels[0].findtext('orientation'), scrolltime.get('tween'), scrolltime.text), ('horizontal', 'quadratic', '300'))

		root = ET.parse(ROOT / 'xml' / 'IncludesDialogVideoInfo.xml').getroot()
		providers = [control for control in root.iter('control') if control.get('type') == 'panel' and control.get('id') == '50']
		self.assertEqual(len(providers), 1)
		provider = providers[0]
		scrolltime = provider.find('scrolltime')
		self.assertEqual(
			(provider.findtext('left'), provider.findtext('top'), provider.findtext('width'), provider.findtext('height'), provider.findtext('orientation'), scrolltime.get('tween'), scrolltime.text),
			('-2000', '-2000', '1', '1', 'horizontal', 'quadratic', '400')
		)


if __name__ == '__main__':
	unittest.main()
