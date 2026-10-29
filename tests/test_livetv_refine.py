import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.module_isolation import load_module
from tests.test_livetv import load_livetv

ROOT = Path(__file__).resolve().parents[1]


class LiveTVRefineTests(unittest.TestCase):
	def setUp(self):
		self.live = load_livetv()
		menus = types.ModuleType('menus')
		menus.livetv = self.live
		modules = types.ModuleType('modules')
		modules.kodi_utils = self.live.ku
		self.live.ku.set_property = Mock()
		self.live.ku.get_property = Mock(return_value='')
		self.refine = load_module('test_livetv_refine_module', ROOT / 'resources/lib/menus/livetv_refine.py', {'menus': menus, 'menus.livetv': self.live, 'modules': modules, 'xbmc': Mock()})
		self.account = {'server': 'https://example.test', 'username': 'user', 'password': 'private'}

	def test_category_and_name_filter_return_playable_items(self):
		streams = [{'stream_id': 42, 'name': 'VN| VTV1 HD', 'stream_icon': 'https://example.test/vtv1.png'}, {'stream_id': 43, 'name': 'VN| VTV2 HD'}, {'stream_id': 'bad', 'name': 'VTV1'}]
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=streams) as catalog, patch.object(self.live, '_item') as item, patch.object(self.live, '_finish') as finish:
			self.refine.dispatch({'category': '353', 'category_name': 'Vietnam', 'name': 'vTv1'})
		catalog.assert_called_once_with(self.account, 'get_live_streams', category_id='353')
		item.assert_called_once_with('VN| VTV1 HD', {'mode': 'livetv.play', 'id': '42'}, False, 'https://example.test/vtv1.png')
		finish.assert_called_once_with()

	def test_pages_preserve_filters_and_return_distinct_channels(self):
		streams = [{'stream_id': index + 1, 'name': 'VTV %03d' % index} for index in range(102)]
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=streams), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.refine.dispatch({'category': '353', 'category_name': 'Vietnam', 'name': 'vtv', 'page': '0'})
			first = item.call_args_list
			self.assertEqual(len(first), 101)
			self.assertEqual(first[-1].args[0], 'Next page')
			self.assertEqual(first[-1].args[1]['category'], '353')
			self.assertEqual(first[-1].args[1]['name'], 'vtv')
			item.reset_mock()
			self.refine.dispatch({'category': '353', 'category_name': 'Vietnam', 'name': 'vtv', 'page': '1'})
			second = item.call_args_list
			self.assertEqual(len(second), 3)
			self.assertEqual(second[0].args[0], 'Previous page')
			self.assertEqual(second[1].args[0], 'VTV 100')
			self.assertEqual(second[2].args[0], 'VTV 101')

	def test_missing_account_and_provider_error_finish_unsuccessfully(self):
		with patch.object(self.live, '_load', return_value=None), patch.object(self.live, '_error') as error, patch.object(self.live, '_finish') as finish:
			self.refine.dispatch({})
			error.assert_called_once()
			finish.assert_called_once_with(False)
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', side_effect=OSError('private')), patch.object(self.live, '_error') as error, patch.object(self.live, '_finish') as finish:
			self.refine.dispatch({})
			error.assert_called_once_with('Could not load Live TV channels.')
			finish.assert_called_once_with(False)

	def test_no_matches_offers_clear_refinements(self):
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=[{'stream_id': 1, 'name': 'VTV1'}]), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish') as finish:
			self.refine.dispatch({'name': 'not a channel'})
		item.assert_called_once_with('No channels found — clear refinements', {'mode': 'livetv.refine'})
		finish.assert_called_once_with()

	def test_background_directory_read_does_not_change_current_view(self):
		self.refine.xbmc.getCondVisibility.return_value = False
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=[{'stream_id': 1, 'name': 'VTV1'}]), patch.object(self.live, '_item'), patch.object(self.live, '_finish'):
			self.refine.dispatch({})
		self.refine.xbmc.executebuiltin.assert_not_called()

	def test_existing_landscape_view_is_not_reset(self):
		self.refine.xbmc.getCondVisibility.return_value = False
		with patch.object(self.live, '_load', return_value=self.account), patch.object(self.live, '_catalog', return_value=[{'stream_id': 1, 'name': 'VTV1'}]), patch.object(self.live, '_item'), patch.object(self.live, '_finish'):
			self.refine.dispatch({})
		self.refine.xbmc.getCondVisibility.assert_called_once_with('Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.refine) + !Control.IsVisible(523)')
		self.refine.xbmc.executebuiltin.assert_not_called()
if __name__ == '__main__': unittest.main()
