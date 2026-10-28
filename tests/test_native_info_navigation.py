import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlencode

from tests.test_dialog_navigation import load_dialogs
from tests.test_mylist_actions import command_params, load_mylist_controller


ROOT = Path(__file__).resolve().parents[1]


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
