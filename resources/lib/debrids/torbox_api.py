import requests
from modules import kodi_utils


get_setting = kodi_utils.get_setting
base_url = 'https://api.torbox.app/v1/api/'
timeout = 10.0
session = requests.Session()
session.custom_errors = requests.exceptions.ConnectionError, requests.exceptions.Timeout
session.mount('https://api.torbox.app', requests.adapters.HTTPAdapter(max_retries=1))


class TorBoxAPI:
	icon = 'premium.png'
	defaults_to_cloud = True

	def __init__(self):
		self.token = get_setting('tb.token')

	def _request(self, method, path, params=None, json=None, data=None, files=None, raw=False):
		headers = {'Authorization': 'Bearer %s' % self.token} if self.token else {}
		try: response = session.request(method, base_url + path, headers=headers, params=params, json=json, data=data, files=files, timeout=timeout)
		except session.custom_errors:
			kodi_utils.notification('%s timeout' % __name__)
			return None
		try: result = response.json()
		except Exception:
			kodi_utils.logger(__name__, '%s: %s' % (response.reason, path))
			return None
		if not response.ok or result.get('success') is not True:
			kodi_utils.logger(__name__, '%s: %s' % (result.get('error') or response.reason, result.get('detail', '')))
			return None
		return result if raw else result.get('data')

	def _get(self, path, params=None):
		return self._request('get', path, params=params)

	def _post(self, path, params=None, json=None, data=None, files=None, raw=False):
		return self._request('post', path, params=params, json=json, data=data, files=files, raw=raw)

	def days_remaining(self):
		from datetime import datetime
		try:
			expires = datetime.fromisoformat(self.account_info()['premium_expires_at'].replace('Z', '+00:00'))
			return (expires.astimezone().date() - datetime.today().date()).days
		except: return None

	def account_info(self):
		return self._get('user/me', {'settings': 'false'})

	def torrent_info(self, transfer_id=None):
		params = {'bypass_cache': 'true'}
		if transfer_id is not None: params['id'] = transfer_id
		return self._get('torrents/mylist', params)

	def delete_torrent(self, transfer_id):
		if transfer_id in ('', None): return False
		data = {'torrent_id': int(transfer_id), 'operation': 'delete'}
		return self._post('torrents/controltorrent', json=data, raw=True) is not None

	def unrestrict_link(self, link):
		try: transfer_id, file_id = str(link).split(',', 1)
		except: return None
		params = {'token': self.token, 'torrent_id': transfer_id, 'file_id': file_id}
		url = self._get('torrents/requestdl', params)
		if url and url.lower().endswith(('.rar', '.zip')): raise Exception('link error')
		return url

	def check_cache(self, hashes):
		original_hashes = {str(item).lower(): item for item in hashes}
		result = self._post('torrents/checkcached', params={'format': 'object', 'list_files': 'false'}, json={'hashes': list(original_hashes)})
		if result is None: return {}
		cached = {str(item).lower() for item in result} if isinstance(result, dict) else {str(item.get('hash', '')).lower() for item in result}
		return {original: normalized in cached for normalized, original in original_hashes.items()}

	def create_transfer(self, magnet, cached_only=False):
		fields = {'magnet': (None, magnet), 'seed': (None, '3'), 'allow_zip': (None, 'false')}
		if cached_only: fields['add_only_if_cached'] = (None, 'true')
		result = self._post('torrents/createtorrent', files=fields) or {}
		return result.get('torrent_id', '')

	def _existing_transfer(self, info_hash):
		result = self.torrent_info()
		if result is None: return None, False
		info_hash = str(info_hash).lower()
		return next((item for item in result if str(item.get('hash', '')).lower() == info_hash), None), True

	def parse_magnet_pack(self, magnet_url, info_hash, errors=False):
		from modules.source_utils import supported_video_extensions
		transfer_id = ''
		try:
			transfer, status_available = self._existing_transfer(info_hash)
			if not status_available: raise Exception('torbox cloud status unavailable')
			if transfer is None:
				transfer_id = self.create_transfer(magnet_url, cached_only=True)
				if transfer_id in ('', None): raise Exception('torbox null torrent')
				for _ in range(4):
					transfer = self.torrent_info(transfer_id)
					if transfer and transfer.get('download_present') and transfer.get('files'): break
					kodi_utils.sleep(500)
				else: raise Exception('torbox cached torrent unavailable')
			elif not transfer.get('download_present') or not transfer.get('files'): raise Exception('torbox torrent unavailable')
			extensions = tuple(supported_video_extensions())
			return [
				{
					'link': '%s,%s' % (transfer['id'], item['id']), 'size': item.get('size', 0), 'torrent_id': transfer_id,
					'filename': item.get('short_name') or item.get('name', '')
				}
				for item in transfer['files']
				if (item.get('short_name') or item.get('name', '')).lower().endswith(extensions)
			]
		except Exception:
			if transfer_id not in ('', None): self.delete_torrent(transfer_id)
			if errors: raise

	def clear_cache(self):
		from caches.debrid_cache import DebridCache
		try: return DebridCache().clear_debrid_results('tb')
		except: return False
