import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from time import monotonic_ns
from urllib.parse import urljoin, urlparse

from modules import kodi_utils


YOUTUBE_API_KEY = 'AIzaSyB-63vPrdThhKuerbB2N_l7Kwwcxj6yUAc'
YOUTUBE_CLIENT_ID = '5'
YOUTUBE_CLIENT_NAME = 'IOS'
YOUTUBE_CLIENT_VERSION = '20.20.7'
YOUTUBE_PLAYER_URL = 'https://www.youtube.com/youtubei/v1/player'
YOUTUBE_VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{6,20}$')
TRAILER_RESOLVED_PROPERTY = 'BingieTrailerResolved'
TRAILER_MANIFEST_URL_PROPERTY = 'BingieTrailerManifestUrl'
TRAILER_RESOLUTION = (1280, 720)
TRAILER_MANIFEST_FILE = kodi_utils.profile_path + 'trailer_preview.m3u8'
HLS_ATTRIBUTE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
HTTP_SESSION = None


def _http_session():
	global HTTP_SESSION
	if HTTP_SESSION is None:
		import requests
		HTTP_SESSION = requests.Session()
	return HTTP_SESSION


def plugin_url(video_id):
	return kodi_utils.build_url({'mode': 'play_trailer', 'video_id': video_id})


def play(params):
	video_id = str(params.get('video_id') or '')
	handle = int(kodi_utils.argv1())
	try:
		_, listitem = prepare_video(video_id)
		return kodi_utils.set_resolvedurl(handle, listitem)
	except Exception as exc:
		listitem = kodi_utils.make_listitem()
		kodi_utils.clear_property(TRAILER_RESOLVED_PROPERTY)
		kodi_utils.logger('BINGIE trailer playback', str(exc))
		kodi_utils.notification('Trailer unavailable', 2500)
		return kodi_utils.xbmcplugin.setResolvedUrl(handle, False, listitem)


def prepare(url):
	return commit_prepared(prepare_data(url))


def prepare_data(url, session=None):
	parsed = urlparse(url)
	if parsed.scheme != 'plugin' or parsed.netloc != kodi_utils.current_addon_id: return url, ''
	params = kodi_utils.parsed_query(url)
	if params.get('mode') != 'play_trailer': return url, ''
	return _prepare_video_data(str(params.get('video_id') or ''), session)


def prepare_data_isolated(url):
	import requests
	session = requests.Session()
	try: return prepare_data(url, session)
	finally: session.close()


def commit_prepared(prepared):
	stream_url, manifest = prepared
	if not manifest: return stream_url, None
	stream_url = _commit_hls_manifest(manifest)
	listitem = kodi_utils.make_listitem()
	listitem.setPath(stream_url)
	listitem.setMimeType('application/vnd.apple.mpegurl')
	listitem.setContentLookup(False)
	listitem.setProperty('IsPlayable', 'true')
	kodi_utils.set_property(TRAILER_RESOLVED_PROPERTY, stream_url)
	return stream_url, listitem


def prepare_video(video_id):
	return commit_prepared(_prepare_video_data(video_id))


def _prepare_video_data(video_id, session=None):
	session = session or _http_session()
	master_url = resolve(video_id, session)
	response = session.get(master_url, timeout=15)
	response.raise_for_status()
	return '', _limited_hls_manifest(response.text, master_url)


def limit_hls_resolution(master_url):
	response = _http_session().get(master_url, timeout=15)
	response.raise_for_status()
	manifest = _limited_hls_manifest(response.text, master_url)
	return _commit_hls_manifest(manifest)


def _commit_hls_manifest(manifest):
	kodi_utils.make_directorys(kodi_utils.profile_path)
	manifest_file = kodi_utils.open_file(TRAILER_MANIFEST_FILE, 'w')
	try: manifest_file.write(manifest)
	finally: manifest_file.close()
	manifest_url = kodi_utils.get_property(TRAILER_MANIFEST_URL_PROPERTY)
	if not manifest_url: raise RuntimeError('BINGIE trailer manifest service is unavailable')
	return '%s?%d' % (manifest_url, monotonic_ns())


def _limited_hls_manifest(manifest, master_url):
	lines = [line.strip() for line in manifest.splitlines() if line.strip()]
	variants = []
	for index, line in enumerate(lines):
		if not line.startswith('#EXT-X-STREAM-INF:'): continue
		attributes = _hls_attributes(line)
		try: width, height = (int(value) for value in attributes.get('RESOLUTION', '').split('x', 1))
		except (TypeError, ValueError): continue
		variant_url = next((urljoin(master_url, candidate) for candidate in lines[index + 1:] if not candidate.startswith('#')), '')
		if variant_url and (width, height) == TRAILER_RESOLUTION:
			variants.append((height, width, 'avc1' in attributes.get('CODECS', '').lower(), int(attributes.get('BANDWIDTH') or 0), line, variant_url, attributes))
	if not variants: raise RuntimeError('YouTube did not return a 1280x720 trailer stream')
	_, _, _, _, stream_line, stream_url, stream_attributes = max(variants, key=lambda variant: variant[:4])
	audio_group = stream_attributes.get('AUDIO', '').strip('"')
	audio_entries = [
		(line, _hls_attributes(line)) for line in lines
		if line.startswith('#EXT-X-MEDIA:') and _hls_attributes(line).get('TYPE') == 'AUDIO' and _hls_attributes(line).get('GROUP-ID', '').strip('"') == audio_group
	]
	audio_lines = [_absolute_hls_uri(line, master_url) for line in _english_audio_lines(audio_entries)]
	header_lines = [line for line in lines if line.startswith('#EXT-X-VERSION:') or line == '#EXT-X-INDEPENDENT-SEGMENTS']
	return '\n'.join(['#EXTM3U', *header_lines, *audio_lines, stream_line, stream_url, ''])


def _hls_attributes(line):
	return dict(HLS_ATTRIBUTE.findall(line.partition(':')[2]))


def _english_audio_lines(audio_entries):
	if not audio_entries: return ()
	english = [(line, attributes) for line, attributes in audio_entries if _english_hls_audio(attributes)]
	if english:
		line, _ = max(english, key=lambda item: _hls_audio_rank(item[1]))
		return (line,)
	if not any(attributes.get('LANGUAGE', '').strip('"') for _, attributes in audio_entries):
		line, _ = max(audio_entries, key=lambda item: _hls_audio_rank(item[1]))
		return (line,)
	raise RuntimeError('YouTube did not return an English trailer audio track')


def _english_hls_audio(attributes):
	language = attributes.get('LANGUAGE', '').strip('"').strip().lower().replace('_', '-')
	name = attributes.get('NAME', '').strip('"').strip().lower()
	return language == 'en' or language.startswith('en-') or (not language and 'english' in name)


def _hls_audio_rank(attributes):
	name = attributes.get('NAME', '').strip('"').strip().lower()
	normal = not any(marker in name for marker in ('audio description', 'descriptive', 'commentary'))
	return normal, attributes.get('DEFAULT') == 'YES', attributes.get('AUTOSELECT') == 'YES'


def _absolute_hls_uri(line, master_url):
	match = re.search(r'URI="([^"]+)"', line)
	if not match: return line
	return '%s%s%s' % (line[:match.start(1)], urljoin(master_url, match.group(1)), line[match.end(1):])


def start_manifest_server():
	server = HTTPServer(('127.0.0.1', 0), _ManifestHandler)
	server.manifest_file = kodi_utils.translate_path(TRAILER_MANIFEST_FILE)
	thread = Thread(target=server.serve_forever, name='BINGIE trailer manifest', daemon=True)
	thread.start()
	kodi_utils.set_property(TRAILER_MANIFEST_URL_PROPERTY, 'http://127.0.0.1:%d/trailer_preview.m3u8' % server.server_port)
	return server, thread


def stop_manifest_server(server_thread):
	kodi_utils.clear_property(TRAILER_MANIFEST_URL_PROPERTY)
	if server_thread is None: return
	server, thread = server_thread
	server.shutdown()
	server.server_close()
	thread.join()
	kodi_utils.delete_file(TRAILER_MANIFEST_FILE)


class _ManifestHandler(BaseHTTPRequestHandler):
	def do_GET(self):
		if urlparse(self.path).path != '/trailer_preview.m3u8': return self.send_error(404)
		try:
			with open(self.server.manifest_file, 'rb') as manifest_file: manifest = manifest_file.read()
		except OSError: return self.send_error(503)
		if not manifest: return self.send_error(503)
		self.send_response(200)
		self.send_header('Content-Type', 'application/vnd.apple.mpegurl')
		self.send_header('Content-Length', str(len(manifest)))
		self.send_header('Cache-Control', 'no-store')
		self.end_headers()
		try: self.wfile.write(manifest)
		except OSError: pass

	def log_message(self, format, *args):
		pass


def resolve(video_id, session=None):
	if not YOUTUBE_VIDEO_ID.fullmatch(video_id): raise ValueError('Invalid YouTube video ID')
	session = session or _http_session()
	headers = {
		'Content-Type': 'application/json',
		'User-Agent': 'com.google.ios.youtube/20.20.7 (iPhone16,2; U; CPU iOS 18_5 like Mac OS X;)',
		'X-YouTube-Client-Name': YOUTUBE_CLIENT_ID,
		'X-YouTube-Client-Version': YOUTUBE_CLIENT_VERSION,
	}
	payload = {
		'videoId': video_id,
		'params': '2AMB',
		'contentCheckOk': True,
		'racyCheckOk': True,
		'context': {
			'client': {
				'clientName': YOUTUBE_CLIENT_NAME,
				'clientVersion': YOUTUBE_CLIENT_VERSION,
				'deviceModel': 'iPhone16,2',
				'hl': 'en',
				'osName': 'iPhone',
				'osVersion': '18.5.0.22F76',
				'timeZone': 'UTC',
				'utcOffsetMinutes': 0,
			},
		},
	}
	response = session.post(YOUTUBE_PLAYER_URL, params={'key': YOUTUBE_API_KEY}, headers=headers, json=payload, timeout=15)
	response.raise_for_status()
	data = response.json()
	playability = data.get('playabilityStatus') or {}
	if playability.get('status') != 'OK': raise RuntimeError(playability.get('reason') or 'YouTube rejected trailer playback')
	manifest_url = (data.get('streamingData') or {}).get('hlsManifestUrl')
	if not manifest_url: raise RuntimeError('YouTube did not return an HLS trailer stream')
	return manifest_url
