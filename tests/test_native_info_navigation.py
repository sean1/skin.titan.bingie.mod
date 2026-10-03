import json
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

	def test_view_series_fits_resumable_actions_and_moves_shelves_only_for_the_extra_row(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesDialogVideoInfo.xml').getroot()
		buttons = next(control for control in root.iter('control') if control.get('id') == '8000')
		ids = [control.get('id') for control in buttons.findall('control')]
		self.assertEqual(ids.count('56'), 1)
		self.assertEqual(ids[ids.index('52') + 1], '56')
		self.assertEqual(buttons.findtext('orientation'), 'vertical')
		self.assertEqual(buttons.findtext('onup'), 'noop')
		self.assertIn('550', [node.text for node in buttons.findall('ondown')])
		defaults = next(node for node in root.findall('include') if node.get('name') == 'Bingie_InfoDialog_Button_Default_Defs')
		row_height, gap = int(defaults.findtext('height')), int(buttons.findtext('itemgap'))
		self.assertGreaterEqual(int(buttons.findtext('height')), 3 * row_height + 2 * gap)
		shelves = next(control for control in root.iter('control') if control.get('type') == 'grouplist' and control.findtext('top') == '650')
		shift = next(node for node in shelves.findall('animation') if node.get('condition') == 'Control.IsVisible(56) + Control.IsVisible(90)')
		self.assertEqual(shift.get('end'), '0,%s' % (row_height + gap))
		self.assertEqual(shift.get('time'), '0')
		self.assertIsNone(next(control for control in buttons.findall('control') if control.get('id') == '56').find('ondown'))

	def test_view_series_preserves_both_native_resume_and_start_over_paths(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesDialogVideoInfo.xml').getroot()
		buttons = {control.get('id'): control for control in root.iter('control') if control.get('id') in ('90', '52')}
		resume = [(node.get('condition'), node.text) for node in buttons['90'].findall('onclick')]
		start = [(node.get('condition'), node.text) for node in buttons['52'].findall('onclick')]
		self.assertIn(('!String.IsEmpty(ListItem.FileNameAndPath)', 'AlarmClock(PlayMovie,PlayMedia($ESCINFO[ListItem.FileNameAndPath],resume),00:00,silent)'), resume)
		self.assertIn(('String.IsEmpty(ListItem.FileNameAndPath)', 'AlarmClock(PlayMovie,PlayMedia($ESCINFO[ListItem.FolderPath],resume),00:00,silent)'), resume)
		self.assertIn(('!Control.IsVisible(5050) + !String.IsEmpty(ListItem.FileNameAndPath)', 'AlarmClock(PlayMovie,PlayMedia($ESCINFO[ListItem.FileNameAndPath],noresume),00:00,silent)'), start)
		self.assertIn(('!Control.IsVisible(5050) + String.IsEmpty(ListItem.FileNameAndPath) + !String.IsEmpty(ListItem.FolderPath)', 'AlarmClock(PlayMovie,PlayMedia($ESCINFO[ListItem.FolderPath],noresume),00:00,silent)'), start)

	def test_episodes_marks_native_info_for_return_before_closing_dialog(self):
		root = ET.parse(ROOT / 'xml' / 'IncludesDialogVideoInfo.xml').getroot()
		button = next(control for control in root.iter('control') if control.get('id') == '53')
		actions = [node.text for node in button.findall('onclick')]

		self.assertEqual(actions, [
			'SetProperty(BaseWindow,1,Home)',
			'Dialog.Close(movieinformation)',
			'AlarmClock(BrowseEpisodes,ActivateWindow(Videos,plugin://skin.titan.bingie.lite/?mode=build_season_list&tmdb_id=$INFO[ListItem.UniqueID(tmdb)],return),00:00,silent)'
		])

	def test_native_info_return_condition_is_unchanged(self):
		root = ET.parse(ROOT / 'xml' / 'MyVideoNav.xml').getroot()
		action = 'AlarmClock(loadinfo,Action(Info),00:00,silent)'
		onunload = next(node for node in root.findall('onunload') if node.text == action)

		self.assertEqual(onunload.get('condition'), '!String.IsEmpty(Window(Home).Property(BaseWindow)) + String.IsEmpty(Window(Home).Property(ListItem.TVShowID)) + !Player.HasVideo')

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

	def test_season_browser_is_a_unique_normal_window(self):
		window_path = ROOT / 'xml' / 'Custom_1124_PovSeasons.xml'
		root = ET.parse(window_path).getroot()

		self.assertEqual(root.tag, 'window')
		self.assertEqual(root.get('id'), '1124')
		self.assertIsNone(root.get('type'))
		self.assertEqual([node.text for node in root.find('controls').findall('include')], ['GlobalBackground', 'View_527_Seasons'])

		window_ids = []
		for path in (ROOT / 'xml').glob('Custom_*.xml'):
			window_id = ET.parse(path).getroot().get('id')
			if window_id: window_ids.append(window_id)
		self.assertEqual(window_ids.count('1124'), 1)

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

	def test_custom_season_navigation_has_no_recovery_shims(self):
		for filename in ('IncludesPovInfo.xml', 'Custom_1123_PovInfo.xml', 'MyVideoNav.xml', 'View_527_Bingie_Seasons.xml'):
			contents = (ROOT / 'xml' / filename).read_text()
			self.assertNotIn('PovInfoSeasonBrowserReturn', contents)
			self.assertNotIn('ReplaceWindow(1123)', contents)


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

	def test_parent_series_snapshot_uses_show_title_and_preserves_regular_titles(self):
		for source_type, target_type, expected_title in (('episode', 'tvshow', 'Series'), ('season', 'tvshow', 'Series'), ('tvshow', 'tvshow', 'The Pilot'), ('movie', 'movie', 'The Pilot')):
			with self.subTest(source_type=source_type, target_type=target_type):
				self.labels['DBTYPE'] = source_type
				snapshot = self.dialogs._selected_media_snapshot(target_type, '202')
				self.assertEqual(snapshot['PovInfoTitle'], expected_title)
				self.assertEqual(snapshot['PovInfoType'], target_type)

	def test_parent_identity_and_title_survive_native_close_and_failed_hydration(self):
		self.dialogs.show_media_info({'mediatype': 'tvshow', 'tmdb_id': '202'})
		self.assertEqual(self.commands[:2], ['Dialog.Close(movieinformation)', 'ActivateWindow(1123)'])
		self.assertEqual(len(self.commands), 3)
		self.assertEqual(self.properties['PovInfoType'], 'tvshow')
		self.assertEqual(self.properties['PovInfoPendingTmdb'], '202')
		self.assertEqual(self.properties['PovInfoTitle'], 'Series')
		self.dialogs.refresh_info_state.assert_called_once_with('tvshow', '202', 'Series')
		self.assertEqual(json.loads(self.properties[self.dialogs.POV_PAGE_HISTORY_PROPERTY]), [{'page': 'native_info'}])
		url = self.commands[-1][len('RunPlugin('):-1]
		params = {name: values[0] for name, values in parse_qs(urlsplit(url).query).items()}
		self.assertEqual({name: params[name] for name in ('mode', 'mediatype', 'tmdb_id')}, {'mode': 'hydrate_media_info', 'mediatype': 'tvshow', 'tmdb_id': '202'})
		self.assertEqual(set(params), {'mode', 'mediatype', 'tmdb_id', 'request'})
		self.dialogs.get_media_metadata = Mock(side_effect=RuntimeError('metadata unavailable'))
		self.dialogs.hydrate_media_info(params)
		self.assertEqual(self.properties['PovInfoTitle'], 'Series')
		self.assertEqual(self.properties['PovInfoTmdb'], '202')
		self.assertEqual(len(self.commands), 3)

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
