import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
XML = ROOT / 'xml'


def parse(name):
	return ET.parse(XML / name).getroot()


def named_include(root, name):
	include = root.find("./include[@name='%s']" % name)
	if include is None: raise AssertionError('include %s not found' % name)
	return include


def wrapper_params(filename, name):
	wrapper = named_include(parse(filename), name)
	children = list(wrapper)
	if len(children) != 1 or children[0].tag != 'include': raise AssertionError('%s must contain one shared include call' % name)
	call = children[0]
	return call.get('content'), {param.get('name'): param.get('value') for param in call.findall('param')}


def element_signature(element):
	return element.tag, tuple(sorted(element.attrib.items())), (element.text or '').strip(), tuple(element_signature(child) for child in element)


class ViewDetailIncludeTests(unittest.TestCase):
	def test_shared_detail_body_owns_common_layout(self):
		shared = named_include(parse('IncludesViewDetails.xml'), 'BingieEpisodeSeasonDetailCard')
		self.assertEqual(
			{param.get('name'): param.get('default') for param in shared.findall('param')},
			{'label': None, 'focus_condition': None, 'content_visibility': None, 'scroll': 'false'}
		)
		definition = shared.find('definition')
		expected = ET.fromstring('''
			<definition>
				<control type="group">
					<animation effect="fade" start="100" end="60" time="150" condition="$PARAM[focus_condition]">Conditional</animation>
					<left>495</left><height>episodes_thumb_height</height><width>600</width>
					<control type="label">
						<top>35</top><width>100%</width><aligny>center</aligny><height>35</height><align>left</align><font>Reg30</font><textcolor>ffffffff</textcolor>
						<label>[B]$PARAM[label][/B]</label><scroll>$PARAM[scroll]</scroll>
					</control>
					<control type="textbox">
						<visible>$PARAM[content_visibility]</visible><width>100%</width><top>81</top><height max="105">auto</height><align>left</align><font>Reg26</font>
						<textcolor>b3ffffff</textcolor><label>$VAR[ViewsPlotWithOutline]</label>
					</control>
					<control type="label">
						<visible>$PARAM[content_visibility]</visible><width>100%</width><bottom>41</bottom><height>35</height><align>left</align><font>Reg26</font>
						<textcolor>73ffffff</textcolor><label>$INFO[ListItem.Property(pov_lite_first_aired),, • ]$INFO[ListItem.Duration(mins),(,m)]</label>
					</control>
				</control>
			</definition>
		''')
		self.assertEqual(element_signature(definition), element_signature(expected))

	def test_view_wrappers_preserve_distinct_contracts(self):
		expected = {
			('View_525_Bingie_Episodes.xml', 'View_525_Details_Defs'): {
				'label': '$VAR[View525MainLabel]', 'focus_condition': '!Control.HasFocus(525) + !Control.HasFocus(60)',
				'content_visibility': 'Container(525).Content(episodes) + String.IsEqual(ListItem.DBTYPE,episode)', 'scroll': 'false'
			},
			('View_525_Bingie_Episodes.xml', 'View_525_Details_Defs_Focus'): {
				'label': '$VAR[View525MainLabel]', 'focus_condition': '!Control.HasFocus(525) + !Control.HasFocus(60)',
				'content_visibility': 'Container(525).Content(episodes) + String.IsEqual(ListItem.DBTYPE,episode)', 'scroll': 'true'
			},
			('View_527_Bingie_Seasons.xml', 'View_527_Details_Defs'): {
				'label': '$VAR[View527MainLabel]', 'focus_condition': '!Control.HasFocus(5027) + !Control.HasFocus(60)',
				'content_visibility': 'Container(5027).Content(episodes) | String.IsEqual(ListItem.DBTYPE,episode)', 'scroll': 'false'
			},
			('View_527_Bingie_Seasons.xml', 'View_527_Details_Defs_Focus'): {
				'label': '$VAR[View527MainLabel]', 'focus_condition': '!Control.HasFocus(5027) + !Control.HasFocus(60)',
				'content_visibility': 'Container(5027).Content(episodes) | String.IsEqual(ListItem.DBTYPE,episode)', 'scroll': 'true'
			},
		}
		for (filename, name), params in expected.items():
			with self.subTest(filename=filename, name=name):
				content, actual = wrapper_params(filename, name)
				self.assertEqual(content, 'BingieEpisodeSeasonDetailCard')
				self.assertEqual(actual, params)

	def test_global_registry_loads_shared_detail_before_callers(self):
		files = [include.get('file') for include in parse('Includes.xml').findall('include') if include.get('file')]
		self.assertLess(files.index('IncludesViewDetails.xml'), files.index('View_525_Bingie_Episodes.xml'))
		self.assertLess(files.index('IncludesViewDetails.xml'), files.index('View_527_Bingie_Seasons.xml'))


if __name__ == '__main__':
	unittest.main()
