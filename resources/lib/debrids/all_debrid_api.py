import requests
from modules import kodi_utils


get_setting = kodi_utils.get_setting
base_url = 'https://api.alldebrid.com/'
timeout = 10.0
cache_check_chunk_size = 25
session = requests.Session()
session.custom_errors = requests.exceptions.ConnectionError, requests.exceptions.Timeout
session.mount('https://api.alldebrid.com', requests.adapters.HTTPAdapter(max_retries=1))


class AllDebridAPI:
	icon = 'premium.png'
	defaults_to_cloud = True

	def __init__(self):
		self.token = get_setting('ad.token')

	def _request(self, method, path, params=None, data=None):
		url = base_url + path
		headers = {'Authorization': 'Bearer %s' % self.token} if self.token else {}
		try: response = session.request(method, url, headers=headers, params=params, data=data, timeout=timeout)
		except session.custom_errors:
			kodi_utils.notification('%s timeout' % __name__)
			return None
		try: result = response.json()
		except Exception:
			kodi_utils.logger(__name__, '%s\n%s' % (response.reason, response.url))
			return None
		if not response.ok or result.get('status') != 'success':
			error = result.get('error') or {}
			kodi_utils.logger(__name__, '%s: %s\n%s' % (error.get('code', response.reason), error.get('message', ''), response.url))
			return None
		return result.get('data', {})

	def _get(self, path, params=None):
		return self._request('get', path, params=params)

	def _post(self, path, data=None):
		return self._request('post', path, data=data)

	def days_remaining(self):
		from datetime import datetime
		try:
			expires = datetime.fromtimestamp(int(self.account_info()['user']['premiumUntil']))
			return (expires.date() - datetime.today().date()).days
		except: return None

	def account_info(self):
		return self._get('v4/user')

	def torrent_info(self, transfer_id=None):
		params = {'id': transfer_id} if transfer_id else None
		result = self._post('v4.1/magnet/status', params)
		if result is None: return None
		magnets = result.get('magnets', [])
		return magnets[0] if transfer_id and magnets else magnets

	def torrent_files(self, transfer_id):
		result = self._post('v4/magnet/files', {'id[]': transfer_id}) or {}
		magnets = result.get('magnets', [])
		return magnets[0].get('files', []) if magnets else []

	def delete_torrent(self, transfer_id):
		return self._post('v4/magnet/delete', {'id': transfer_id}) is not None

	def unrestrict_link(self, link):
		result = self._post('v4/link/unlock', {'link': link}) or {}
		url = result.get('link')
		if url and url.lower().endswith(('.rar', '.zip')): raise Exception('link error\n%s' % url)
		return url

	def _upload_magnets(self, magnets):
		result = self._post('v4/magnet/upload', {'magnets[]': magnets})
		if result is None: return None
		return result.get('magnets', [])

	def create_transfer(self, magnet):
		result = self._upload_magnets([magnet])
		return result[0].get('id', '') if result else ''

	def _existing_transfer_ids(self):
		magnets = self.torrent_info()
		if magnets is None: return None
		return {str(item['id']) for item in magnets if item.get('id') is not None}

	def check_cache(self, hashes):
		"""Check a bounded batch while preserving every pre-existing cloud transfer."""
		existing_ids = self._existing_transfer_ids()
		if existing_ids is None: return {}
		hashes = hashes[:cache_check_chunk_size]
		original_hashes = {str(item).lower(): item for item in hashes}
		checked = {}
		results = self._upload_magnets(hashes)
		if results is None: return checked
		created_ids = []
		try:
			for item in results:
				transfer_id = item.get('id')
				if transfer_id is not None and str(transfer_id) not in existing_ids: created_ids.append(transfer_id)
				info_hash = str(item.get('hash') or item.get('magnet') or '').lower()
				if info_hash in original_hashes: checked[original_hashes[info_hash]] = bool(item.get('ready'))
		finally:
			for transfer_id in created_ids: self.delete_torrent(transfer_id)
		return checked

	@staticmethod
	def flatten_magnet_files(files_list):
		files = []
		def flatten(items, parents=()):
			for item in items:
				if not isinstance(item, dict): continue
				if 'e' in item: flatten(item['e'], (*parents, item.get('n', '')))
				else:
					filename = '/'.join((*parents, item.get('n', ''))).lstrip('/')
					files.append({'filename': filename, 'size': item.get('s', 0), 'link': item.get('l')})
		flatten(files_list)
		return files

	def parse_magnet_pack(self, magnet_url, info_hash, errors=False):
		from modules.source_utils import supported_video_extensions
		transfer_id = ''
		try:
			existing_ids = self._existing_transfer_ids()
			if existing_ids is None: raise Exception('alldebrid cloud status unavailable')
			results = self._upload_magnets([magnet_url])
			if not results or not results[0].get('id'): raise Exception('alldebrid null magnet')
			result = results[0]
			lookup_id = result['id']
			if str(lookup_id) not in existing_ids: transfer_id = lookup_id
			if not result.get('ready'):
				for _ in range(3):
					kodi_utils.sleep(500)
					status = self.torrent_info(lookup_id) or {}
					if status.get('statusCode') == 4: break
				else: raise Exception('alldebrid uncached magnet')
			extensions = tuple(supported_video_extensions())
			return [
				{**item, 'torrent_id': transfer_id}
				for item in self.flatten_magnet_files(self.torrent_files(lookup_id))
				if item.get('link') and item['filename'].lower().endswith(extensions)
			]
		except Exception:
			if transfer_id: self.delete_torrent(transfer_id)
			if errors: raise

	def clear_cache(self):
		from caches.debrid_cache import DebridCache
		try: return DebridCache().clear_debrid_results('ad')
		except: return False
