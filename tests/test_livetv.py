import json
import os
import tempfile
import types
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from tests.module_isolation import load_module

ROOT = Path(__file__).resolve().parents[1]


def load_livetv():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.translate_path = lambda path: path
	kodi_utils.dialog = Mock()
	modules = types.ModuleType('modules')
	modules.__path__ = [str(ROOT / 'resources/lib/modules')]
	modules.kodi_utils = kodi_utils
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils, 'xbmc': Mock(), 'xbmcgui': Mock(), 'xbmcplugin': Mock()}
	return load_module('test_livetv_module', ROOT / 'resources/lib/menus/livetv.py', stubs)


class LiveTVTests(unittest.TestCase):
	def setUp(self):
		self.live = load_livetv()
		(ROOT / 'tmp').mkdir(exist_ok=True)
		self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
		self.addCleanup(self.temp.cleanup)
		self.live.ku.profile_path = self.temp.name
		self.live.ku.addon_path = self.temp.name
		self.account = {'server': 'http://example.test:8080', 'username': 'my user', 'password': 'p/a&ss'}

	def test_account_is_private_and_round_trips(self):
		self.live._save(self.account)
		self.assertEqual(self.live._load(), self.account)
		self.assertEqual(os.stat(self.live._credentials_path()).st_mode & 0o777, 0o600)


	def test_channel_items_do_not_contain_account_credentials(self):
		self.live._save(self.account)
		with patch.object(self.live, '_request', return_value=[{'name': 'Soccer', 'stream_id': 42}]), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish'):
			self.live.channels({'category': '12'})
		self.assertEqual(item.call_args.args[:3], ('Soccer', {'mode': 'livetv.play', 'id': '42'}, False))


	def test_provider_error_does_not_expose_exception_or_credentials(self):
		self.live._save(self.account)
		with patch.object(self.live, '_request', side_effect=OSError('secret error')), patch.object(self.live, '_item'), patch.object(self.live, '_finish'):
			self.live.browse({})
		message = str(self.live.ku.dialog.ok.call_args)
		self.assertNotIn('secret error', message)
		self.assertNotIn(self.account['password'], message)

	def test_browse_and_play_against_local_xtream_server(self):
		requests = []
		class Handler(BaseHTTPRequestHandler):
			def do_GET(self):
				path = urlsplit(self.path)
				query = parse_qs(path.query)
				requests.append((path.path, query))
				if path.path != '/player_api.php' or query.get('username') != ['user'] or query.get('password') != ['pass']:
					self.send_error(403)
					return
				if query.get('action') == ['get_live_categories']:
					result = [{'category_id': '7', 'category_name': 'Sports'}]
				elif query.get('action') == ['get_live_streams'] and query.get('category_id') == ['7']:
					result = [{'stream_id': 21, 'name': 'Soccer'}]
				else:
					self.send_error(400)
					return
				self.send_response(200)
				self.end_headers()
				self.wfile.write(json.dumps(result).encode())
			def log_message(self, format, *args): pass
		server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
		thread = threading.Thread(target=server.serve_forever, daemon=True)
		thread.start()
		self.addCleanup(server.server_close)
		self.addCleanup(thread.join)
		self.addCleanup(server.shutdown)
		account = {'server': 'http://127.0.0.1:%s' % server.server_port, 'username': 'user', 'password': 'pass'}
		self.live._save(account)
		with patch.object(self.live, '_handle', return_value=1), patch.object(self.live, '_item') as item, patch.object(self.live, '_finish') as finish:
			self.live.browse({})
			self.assertEqual(item.call_args_list[-1].args, ('Sports', {'mode': 'livetv.channels', 'category': '7'}))
			item.reset_mock()
			self.live.channels({'category': '7'})
			self.assertEqual(item.call_args.args[:3], ('Soccer', {'mode': 'livetv.play', 'id': '21'}, False))
			self.live.play({'id': '21'})
			self.live.xbmcgui.ListItem.assert_called_with(path=account['server'] + '/live/user/pass/21.ts')
			self.live.xbmcgui.ListItem.return_value.setMimeType.assert_called_once_with('video/mp2t')
			self.live.xbmcgui.ListItem.return_value.setContentLookup.assert_called_once_with(False)
			self.assertEqual(finish.call_count, 2)
		self.assertEqual([request[1]['action'][0] for request in requests], ['get_live_categories', 'get_live_streams'])


if __name__ == '__main__': unittest.main()
