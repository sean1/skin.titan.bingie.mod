import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InfoActionTimingTests(unittest.TestCase):
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

		self.assertEqual(len(target_actions), 11)
		self.assertTrue(all(',00:00,silent)' in action for action in target_actions))
		self.assertTrue(all(',00:01,' not in action for action in target_actions))


if __name__ == '__main__':
	unittest.main()
