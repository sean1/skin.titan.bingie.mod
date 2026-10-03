import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InfoActionTimingTests(unittest.TestCase):

	def test_native_movie_info_cancels_trailer_requests_on_close(self):
		window = ET.parse(ROOT / 'xml' / 'DialogVideoInfo.xml').getroot()
		actions = [(node.get('condition'), node.text) for node in window.findall('onunload') if 'BingieTrailerPreview' in (node.text or '')]
		self.assertEqual(actions, [
			('String.IsEqual(Window.Property(PovInfoType),movie)', 'SetProperty(BingieTrailerPreviewCancel,true,Home)'),
			('String.IsEqual(Window.Property(PovInfoType),movie)', 'ClearProperty(BingieTrailerPreviewRequest,Home)'),
		])



if __name__ == '__main__':
	unittest.main()
