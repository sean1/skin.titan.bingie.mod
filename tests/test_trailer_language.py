import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from tests.module_isolation import load_module, temporary_modules


ROOT = Path(__file__).resolve().parents[1]


def load_metadata():
	tmdb_api = types.ModuleType('indexers.tmdb_api')
	tmdb_api.tmdb_image_base = 'https://image.test/%s%s'
	tmdb_api.resized_tmdb_image = lambda image, resolution: image
	indexers = types.ModuleType('indexers')
	indexers.__path__ = []
	indexers.tmdb_api = tmdb_api
	meta_cache = types.ModuleType('caches.meta_cache')
	meta_cache.MetaCache = object
	caches = types.ModuleType('caches')
	caches.__path__ = []
	modules = types.ModuleType('modules')
	modules.__path__ = []
	utils = types.ModuleType('modules.utils')
	utils.LIST_WORKERS = 5
	utils.jsondate_to_datetime = lambda value: value
	utils.subtract_dates = lambda first, second: 0
	utils.TaskPool = object
	stubs = {
		'indexers': indexers, 'indexers.tmdb_api': tmdb_api, 'caches': caches, 'caches.meta_cache': meta_cache,
		'modules': modules, 'modules.utils': utils
	}
	return load_module('test_trailer_language_metadata', ROOT / 'resources/lib/indexers/metadata.py', stubs)


def load_trailers():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.profile_path = '/tmp/'
	kodi_utils.translate_path = lambda path: path
	kodi_utils.set_property = lambda *args: None
	kodi_utils.clear_property = lambda *args: None
	kodi_utils.delete_file = lambda *args: None
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	return load_module('test_trailer_language_manifest', ROOT / 'resources/lib/modules/trailers.py', {'modules': modules, 'modules.kodi_utils': kodi_utils})


class TrailerLanguageTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.metadata = load_metadata()
		cls.trailers = load_trailers()

	def test_english_trailer_wins_over_higher_ranked_german_trailer(self):
		videos = [
			{'site': 'YouTube', 'type': 'Trailer', 'key': 'german', 'name': 'Official Trailer', 'official': True, 'size': 2160, 'iso_639_1': 'de'},
			{'site': 'YouTube', 'type': 'Trailer', 'key': 'english', 'name': 'Trailer', 'official': False, 'size': 720, 'iso_639_1': 'en'},
		]
		trailer_module = types.ModuleType('modules.trailers')
		trailer_module.plugin_url = lambda video_id: 'trailer://%s' % video_id
		modules = types.ModuleType('modules')
		modules.__path__ = []
		modules.trailers = trailer_module

		with temporary_modules({'modules': modules, 'modules.trailers': trailer_module}):
			self.assertEqual(self.metadata.select_trailer(videos), 'trailer://english')

	def test_foreign_only_trailer_list_is_rejected(self):
		videos = [{'site': 'YouTube', 'type': 'Trailer', 'key': 'german', 'name': 'Official Trailer', 'iso_639_1': 'de'}]
		self.assertEqual(self.metadata.select_trailer(videos), '')

	def test_manifest_keeps_only_normal_english_audio(self):
		manifest = '''#EXTM3U
#EXT-X-VERSION:7
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Deutsch",LANGUAGE="de",DEFAULT=YES,AUTOSELECT=YES,URI="audio/de.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="English descriptive",LANGUAGE="en",DEFAULT=NO,AUTOSELECT=YES,URI="audio/en-description.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="English",LANGUAGE="en-US",DEFAULT=NO,AUTOSELECT=YES,URI="audio/en.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=2000000,CODECS="avc1.64001f,mp4a.40.2",RESOLUTION=1280x720,AUDIO="audio"
video/720.m3u8
'''

		limited = self.trailers._limited_hls_manifest(manifest, 'https://video.test/master.m3u8')

		self.assertIn('LANGUAGE="en-US"', limited)
		self.assertIn('https://video.test/audio/en.m3u8', limited)
		self.assertNotIn('LANGUAGE="de"', limited)
		self.assertNotIn('en-description.m3u8', limited)

	def test_manifest_rejects_explicitly_foreign_audio(self):
		manifest = '''#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Deutsch",LANGUAGE="de",DEFAULT=YES,AUTOSELECT=YES,URI="de.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=2000000,CODECS="avc1.64001f,mp4a.40.2",RESOLUTION=1280x720,AUDIO="audio"
720.m3u8
'''

		with self.assertRaisesRegex(RuntimeError, 'English trailer audio track'):
			self.trailers._limited_hls_manifest(manifest, 'https://video.test/master.m3u8')

	def test_manifest_preserves_single_unlabelled_audio_track(self):
		manifest = '''#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Default",DEFAULT=YES,AUTOSELECT=YES,URI="audio.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=2000000,CODECS="avc1.64001f,mp4a.40.2",RESOLUTION=1280x720,AUDIO="audio"
720.m3u8
'''

		limited = self.trailers._limited_hls_manifest(manifest, 'https://video.test/master.m3u8')
		self.assertIn('https://video.test/audio.m3u8', limited)

	def test_manifest_server_uses_one_plain_python_request_thread(self):
		with TemporaryDirectory() as directory:
			manifest_file = Path(directory) / 'preview.m3u8'
			manifest_file.write_bytes(b'#EXTM3U\n')
			old_manifest_file = self.trailers.TRAILER_MANIFEST_FILE
			self.trailers.TRAILER_MANIFEST_FILE = str(manifest_file)
			server_thread = self.trailers.start_manifest_server()
			try:
				server, _ = server_thread
				with urlopen('http://127.0.0.1:%d/trailer_preview.m3u8' % server.server_port, timeout=2) as response:
					self.assertEqual(response.read(), b'#EXTM3U\n')
				self.assertEqual(type(server).__name__, 'HTTPServer')
			finally:
				self.trailers.stop_manifest_server(server_thread)
				self.trailers.TRAILER_MANIFEST_FILE = old_manifest_file


if __name__ == '__main__':
	unittest.main()
