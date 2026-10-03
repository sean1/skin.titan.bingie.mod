import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlencode, urlsplit

from tests.test_dialog_navigation import load_dialogs
from tests.test_mylist_actions import command_params, load_mylist_controller


ROOT = Path(__file__).resolve().parents[1]


class NativeInfoNavigationTests(unittest.TestCase):
	def test_view_series_requires_a_known_parent_on_a_bingie_episode(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesDialogVideoInfo.xml').getroot()
		button = next(control for control in root.iter('control') if control.get('id') == '56')
		self.assertEqual(button.findtext('label'), 'View Series')
		self.assertEqual(button.findtext('visible').split(' + '), [
			'String.IsEqual(ListItem.DBTYPE,episode)',
			'String.StartsWith(ListItem.FileNameAndPath,plugin://skin.titan.bingie.lite/)',
			'Integer.IsGreater(ListItem.UniqueID(tmdb),0)',
		])

	def test_view_series_has_one_parent_info_action_without_episode_or_playback_arguments(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesDialogVideoInfo.xml').getroot()
		button = next(control for control in root.iter('control') if control.get('id') == '56')
		actions = [node.text for node in button.findall('onclick')]
		self.assertEqual(actions, ['RunPlugin(plugin://skin.titan.bingie.lite/?mode=show_media_info&mediatype=tvshow&tmdb_id=$INFO[ListItem.UniqueID(tmdb)])'])
		url = actions[0][len('RunPlugin('):-1].replace('$INFO[ListItem.UniqueID(tmdb)]', '202')
		self.assertEqual(parse_qs(urlsplit(url).query), {'mode': ['show_media_info'], 'mediatype': ['tvshow'], 'tmdb_id': ['202']})

	def test_view_series_preserves_both_native_resume_and_start_over_paths(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesDialogVideoInfo.xml').getroot()
		buttons = {control.get('id'): control for control in root.iter('control') if control.get('id') in ('90', '52')}
		resume = [(node.get('condition'), node.text) for node in buttons['90'].findall('onclick')]
		start = [(node.get('condition'), node.text) for node in buttons['52'].findall('onclick')]
		self.assertIn(('!String.IsEmpty(ListItem.FileNameAndPath)', 'AlarmClock(PlayMovie,PlayMedia($ESCINFO[ListItem.FileNameAndPath],resume),00:00,silent)'), resume)
		self.assertIn(('String.IsEmpty(ListItem.FileNameAndPath)', 'AlarmClock(PlayMovie,PlayMedia($ESCINFO[ListItem.FolderPath],resume),00:00,silent)'), resume)
		self.assertIn(('!Control.IsVisible(5050) + !String.IsEmpty(ListItem.FileNameAndPath)', 'AlarmClock(PlayMovie,PlayMedia($ESCINFO[ListItem.FileNameAndPath],noresume),00:00,silent)'), start)
		self.assertIn(('!Control.IsVisible(5050) + String.IsEmpty(ListItem.FileNameAndPath) + !String.IsEmpty(ListItem.FolderPath)', 'AlarmClock(PlayMovie,PlayMedia($ESCINFO[ListItem.FolderPath],noresume),00:00,silent)'), start)

	def test_custom_info_opens_dedicated_season_window(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesPovInfo.xml').getroot()
		button = next(control for control in root.iter('control') if control.get('id') == '53')
		actions = [(node.get('condition'), node.text) for node in button.findall('onclick')]

		resolved = '!String.IsEmpty(Window(Home).Property(PovInfoTmdb))'
		pending = 'String.IsEmpty(Window(Home).Property(PovInfoTmdb)) + !String.IsEmpty(Window(Home).Property(PovInfoPendingTmdb))'
		self.assertEqual(actions, [
			(resolved, 'ActivateWindow(1124,return)'),
			(pending, 'ActivateWindow(1124,return)'),
		])

	def test_dedicated_season_window_owns_season_content_and_native_back(self):
		root = ET.parse(ROOT / 'xml' / 'View_527_Bingie_Seasons.xml').getroot()
		season_list = next(control for control in root.iter('control') if control.get('id') == '527')
		content_include = next(node for node in season_list.findall('include') if node.text == 'PovSeasonBrowserContent')
		self.assertEqual(content_include.get('condition'), 'Window.IsActive(1124)')

		for control_id in ('527', '5027', '60'):
			control = next(control for control in root.iter('control') if control.get('id') == control_id)
			self.assertIn(('Window.IsActive(1124)', 'PreviousMenu'), [(node.get('condition'), node.text) for node in control.findall('onback')])

		content = next(include for include in root.findall('include') if include.get('name') == 'PovSeasonBrowserContent').find('content')
		self.assertEqual(content.get('target'), 'videos')
		self.assertEqual(content.get('browse'), 'never')
		self.assertEqual(content.text, '$VAR[PovSeasonBrowserPath]')

		episode_list = next(control for control in root.iter('control') if control.get('id') == '5027')
		self.assertEqual(episode_list.find('content').text, '$INFO[Container(527).ListItem.FolderPath]')


class EpisodeParentInfoBridgeTests(unittest.TestCase):
	def setUp(self):
		self.dialogs = load_dialogs()
		self.labels = {'UniqueID(tmdb)': '202', 'DBTYPE': 'episode', 'Title': 'The Pilot', 'TVShowTitle': 'Series', 'Label': 'The Pilot'}
		self.properties, self.commands = {}, []
		self.native_active = True
		self.dialogs.get_property = lambda key: self.properties.get(key, '')
		self.dialogs.set_property = self.properties.__setitem__
		self.dialogs.clear_property = lambda key: self.properties.pop(key, None)
		self.dialogs.kodi_utils.get_infolabel = lambda name: self.labels.get(name.removeprefix('Container.ListItem.').removeprefix('ListItem.'), '')
		self.dialogs.kodi_utils.get_visibility = lambda condition: condition == 'Window.IsActive(DialogVideoInfo.xml)' and self.native_active
		self.dialogs._stop_owned_trailer_preview = Mock()
		self.dialogs.refresh_info_state = Mock()
		self.dialogs.kodi_utils.logger = Mock()
		self.dialogs.build_url = lambda params: 'plugin://skin.titan.bingie.lite/?' + urlencode(params)
		def execute(command):
			self.commands.append(command)
			if command == 'Dialog.Close(movieinformation)':
				self.native_active = False
				self.labels.clear()
		self.dialogs.execute_builtin = execute

	def test_series_saved_after_failed_hydration_keeps_parent_id_and_series_title(self):
		with tempfile.TemporaryDirectory() as temp_dir:
			controller, cache, kodi_utils, _properties, _stubs = load_mylist_controller(Path(temp_dir) / 'watched.db')
			kodi_utils.get_property = lambda key: self.properties.get(key, '')
			kodi_utils.set_property = Mock(side_effect=self.properties.__setitem__)
			self.dialogs.refresh_info_state = controller.refresh_info_state
			self.dialogs.show_media_info({'mediatype': 'tvshow', 'tmdb_id': '202'})
			self.dialogs.get_media_metadata = Mock(side_effect=RuntimeError('metadata unavailable'))
			self.dialogs.hydrate_media_info({'mediatype': 'tvshow', 'tmdb_id': '202', 'request': self.properties[self.dialogs.POV_INFO_HYDRATION_PROPERTY]})
			params = command_params(self.properties['PovInfoMyListCommand'])
			self.assertEqual((params['action'], params['mediatype'], params['tmdb_id'], params['title']), ('add', 'tvshow', '202', 'Series'))
			self.assertTrue(controller.from_info(params))
			self.assertEqual([(item['media_id'], item['title']) for item in cache.MyList().items('tvshow')[0]], [('202', 'Series')])
			self.assertEqual(cache.MyList().items('movie'), ([], 1))


if __name__ == '__main__':
	unittest.main()
