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

	def test_circular_home_and_hub_wrap_fades_keep_layout_mask_and_reveal_quickly(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesBingie.xml').getroot()
		for include_name in ('Fixed_Focus_Navigation', 'Fixed_Focus_Navigation1'):
			with self.subTest(include_name=include_name):
				includes = [include for include in root.findall('include') if include.get('name') == include_name]
				self.assertEqual(len(includes), 1)
				wrap_fades = []
				for animation in includes[0].findall('animation'):
					condition = animation.get('condition') or ''
					if animation.get('effect') == 'fade' and animation.get('start') == '0' and animation.get('end') == '100' and all(marker in condition for marker in ('Container(77777).NumItems', 'PrevWidgetPos', 'CurrentWidgetPos')):
						wrap_fades.append(animation)
				self.assertEqual(len(wrap_fades), 2)
				self.assertEqual([(animation.get('delay'), animation.get('time'), animation.get('reversible')) for animation in wrap_fades], [('300', '150', 'false'), ('300', '150', 'false')])

	def test_native_info_entrance_keeps_stagger_and_uses_300_millisecond_reveals(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesAnimations.xml').getroot()
		for include_name, delay in (('Animation_Right_Delay', '300'), ('Animation_Right_Delay_2', '200')):
			with self.subTest(include_name=include_name):
				includes = [include for include in root.findall('include') if include.get('name') == include_name]
				self.assertEqual(len(includes), 1)
				window_open = includes[0].find("animation[@type='WindowOpen']")
				self.assertIsNotNone(window_open)
				effects = window_open.findall('effect')
				self.assertEqual([(effect.get('type'), effect.get('time'), effect.get('delay')) for effect in effects], [('fade', '300', delay), ('slide', '300', delay)])
				window_close = includes[0].find("animation[@type='WindowClose']")
				self.assertEqual([(effect.get('type'), effect.get('time'), effect.get('delay')) for effect in window_close.findall('effect')], [('fade', '300', None), ('slide', '300', None)])

	def test_home_content_reveal_keeps_mask_and_uses_300_milliseconds(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesAnimations.xml').getroot()
		includes = [include for include in root.findall('include') if include.get('name') == 'Animation_Right_Home']
		self.assertEqual(len(includes), 1)
		visible = includes[0].find("animation[@type='Visible']")
		self.assertIsNotNone(visible)
		self.assertEqual([(effect.get('type'), effect.get('time'), effect.get('delay')) for effect in visible.findall('effect')], [('fade', '300', '200'), ('slide', '300', '200')])

	def test_episode_view_entrance_keeps_mask_and_uses_300_millisecond_reveal(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesAnimations.xml').getroot()
		includes = [include for include in root.findall('include') if include.get('name') == 'Animation_Right_Bingie']
		self.assertEqual(len(includes), 1)
		window_open = includes[0].find("animation[@type='WindowOpen']")
		self.assertEqual([(effect.get('type'), effect.get('time'), effect.get('delay')) for effect in window_open.findall('effect')], [('fade', '300', '300'), ('slide', '300', '300')])
		window_close = includes[0].find("animation[@type='WindowClose']")
		self.assertEqual([(effect.get('type'), effect.get('time'), effect.get('delay')) for effect in window_close.findall('effect')], [('fade', '300', None), ('slide', '300', None)])

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
