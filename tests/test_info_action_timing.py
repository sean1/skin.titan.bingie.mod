import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InfoActionTimingTests(unittest.TestCase):
	def test_custom_tv_info_keeps_trailer_autoplay_after_one_second(self):
		window = ET.parse(ROOT / 'xml' / 'Custom_1123_PovInfo.xml').getroot()
		autoplay = [node for node in window.findall('onload') if 'PovInfoTrailerPreview' in (node.text or '')]
		self.assertEqual(len(autoplay), 1)
		self.assertEqual(
			(autoplay[0].get('condition'), (autoplay[0].text or '').strip()),
			(
				'String.IsEqual(Window(Home).Property(PovInfoType),tvshow) + [$EXP[PovInfoHasValidMedia] | !String.IsEmpty(Window(Home).Property(PovInfoPendingTmdb))]',
				'AlarmClock(PovInfoTrailerPreview,SetProperty(BingieTrailerPreviewRequest,true,Home),00:00:01,silent)'
			)
		)
		cancellations = [(node.get('condition'), (node.text or '').strip()) for node in window.findall('onunload') if 'PovInfoTrailerPreview' in (node.text or '')]
		self.assertEqual(cancellations, [('System.HasAlarm(PovInfoTrailerPreview)', 'CancelAlarm(PovInfoTrailerPreview,silent)')])

	def test_movie_detail_buttons_request_trailer_without_closing_the_page(self):
		for filename, visibility in (
			('IncludesPovInfo.xml', 'String.IsEqual(Window(Home).Property(PovInfoType),movie)'),
			('IncludesDialogVideoInfo.xml', 'String.IsEqual(ListItem.DBTYPE,movie) + !String.IsEmpty(ListItem.UniqueID(tmdb))'),
		):
			with self.subTest(filename=filename):
				page = ET.parse(ROOT / 'xml' / filename).getroot()
				buttons = next(control for control in page.iter('control') if control.get('id') == '8000')
				trailer = next(control for control in buttons.findall('control') if control.get('id') == '55')
				self.assertEqual(trailer.findtext('visible'), visibility)
				self.assertEqual(trailer.findtext('label'), '$LOCALIZE[20410]')
				self.assertEqual([node.text for node in trailer.findall('onclick')], ['SetProperty(BingieTrailerPreviewRequest,true,Home)'])

	def test_native_movie_info_cancels_trailer_requests_on_close(self):
		window = ET.parse(ROOT / 'xml' / 'DialogVideoInfo.xml').getroot()
		actions = [(node.get('condition'), node.text) for node in window.findall('onunload') if 'BingieTrailerPreview' in (node.text or '')]
		self.assertEqual(actions, [
			('String.IsEqual(Window.Property(PovInfoType),movie)', 'SetProperty(BingieTrailerPreviewCancel,true,Home)'),
			('String.IsEqual(Window.Property(PovInfoType),movie)', 'ClearProperty(BingieTrailerPreviewRequest,Home)'),
		])

	def test_related_shelf_alarms_use_listitem_source_conditions(self):
		root = ET.parse(ROOT / 'xml' / 'DialogVideoInfo.xml').getroot()
		alarms = [(node.get('condition'), (node.text or '').strip()) for node in root.findall('onload') if (node.text or '').strip().startswith('AlarmClock(PovDialog')]
		self.assertEqual(alarms, [
			(
				'[String.IsEqual(ListItem.DBTYPE,movie) | String.IsEqual(ListItem.DBTYPE,tvshow)] + !String.IsEmpty(ListItem.UniqueID(tmdb))',
				'AlarmClock(PovDialogMoreLikeThisShelf,SetProperty(PovInfoMoreLikeThisReady,1,12003),00:00:01,silent)'
			),
			(
				'String.IsEqual(ListItem.DBTYPE,movie) + !String.IsEmpty(ListItem.UniqueID(tmdb))',
				'AlarmClock(PovDialogCollectionShelf,SetProperty(PovInfoCollectionReady,1,12003),00:00:02,silent)'
			)
		])
		cancellations = [(node.get('condition'), (node.text or '').strip()) for node in root.findall('onunload') if (node.text or '').strip().startswith('CancelAlarm(PovDialog')]
		self.assertEqual(cancellations, [
			('System.HasAlarm(PovDialogMoreLikeThisShelf)', 'CancelAlarm(PovDialogMoreLikeThisShelf,silent)'),
			('System.HasAlarm(PovDialogCollectionShelf)', 'CancelAlarm(PovDialogCollectionShelf,silent)')
		])
		cleared_properties = [(node.text or '').strip() for node in root.findall('onunload') if (node.text or '').strip().startswith('ClearProperty(PovInfo')]
		self.assertEqual(cleared_properties, [
			'ClearProperty(PovInfoTmdb,12003)', 'ClearProperty(PovInfoType,12003)', 'ClearProperty(PovInfoCollectionId,12003)',
			'ClearProperty(PovInfoMoreLikeThisReady,12003)', 'ClearProperty(PovInfoCollectionReady,12003)', 'ClearProperty(PovInfoFanart,12003)'
		])

	def test_post_close_info_actions_use_zero_delay_alarms(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesDialogVideoInfo.xml').getroot()
		target_names = ('AlarmClock(PlayShow,', 'AlarmClock(PlaySeason,', 'AlarmClock(PlayMovie,', 'AlarmClock(BrowseEpisodes,')
		target_actions = []
		for control in root.iter('control'):
			actions = [(onclick.text or '').strip() for onclick in control.findall('onclick')]
			targets = [action for action in actions if action.startswith(target_names)]
			if not targets: continue
			self.assertIn('Dialog.Close(movieinformation)', actions)
			close_index = actions.index('Dialog.Close(movieinformation)')
			self.assertTrue(all(close_index < actions.index(action) for action in targets))
			target_actions.extend(targets)

		self.assertEqual(len(target_actions), 10)
		self.assertTrue(all(',00:00,silent)' in action for action in target_actions))
		self.assertTrue(all(',00:01,' not in action for action in target_actions))


if __name__ == '__main__':
	unittest.main()
