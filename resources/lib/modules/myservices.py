import time
import requests
from threading import Thread, Timer
from windows import create_window
from modules import kodi_utils, cache
# logger = kodi_utils.logger

quote, clear_cache = requests.utils.quote, cache.clear_cache
get_setting, set_setting, sleep = kodi_utils.get_setting, kodi_utils.set_setting, kodi_utils.sleep
notification, confirm_dialog = kodi_utils.notification, kodi_utils.confirm_dialog
user_agent = 'BINGIE Lite/%s' % kodi_utils.get_addoninfo('version')
qr_str = 'https://api.qrserver.com/v1/create-qr-code/?size=256x256&qzone=1%s'
meta_keys = 'title year poster fanart clearlogo'
code_str, nav2_str, await_str = 'PIN CODE: [B]%s[/B]', 'LOCATION: [B]%s[/B]', 'REMAINING: [B]%02d:%02d[/B]'
auth_str, noauth_str = 'Authorized: Select to Remove', 'Unauthorized: Select to Add'
timeout = 10.05

def _make_progress_dialog(**kwargs):
	progress_dialog = create_window(('windows.progress', 'ProgressMedia'), 'progress_media.xml', **kwargs)
	Thread(target=progress_dialog.run).start()
	return progress_dialog

def authorize():
	def _builder():
		for api in services:
			item = kodi_utils.make_listitem()
			item.setLabel('[B]%s[/B]' % api.__name__.upper())
			item.setLabel2(auth_str if api().token else noauth_str)
			item.setArt({'icon': '%s%s' % (icon_path, api.icon)})
			yield item
	services, icon_path = (RealDebrid, AllDebrid), kodi_utils.media_path()
	service = kodi_utils.dialog.select('My Services', list(_builder()), useDetails=True)
	if service < 0: return
	try: success = services[service]().set()
	except: kodi_utils.logger('myservices error', f"\n{__import__('traceback').format_exc()}")
	else: return success
	return notification(32574)

class RepeatTimer(Timer):
	def run(self):
		while not self.finished.wait(self.interval):
			self.function(*self.args, **self.kwargs)

class RealDebrid:
	icon = 'realdebrid.png'
	def __init__(self):
		self.secret = get_setting('rd.secret')
		self.token = get_setting('rd.token')
		self.client_id = 'X245A4XAIBGVM'

	def base_url(self, path):
		return 'https://app.real-debrid.com/%s' % path

	def poll_auth(self, data):
		params = {'client_id': self.client_id, 'code': data['code']}
		response = requests.get(self.base_url('oauth/v2/device/credentials'), params=params, timeout=timeout)
		if not response.ok: return
		data.update(response.json())
		self.secret = data['client_secret']

	def set(self):
		if self.token:
			if not confirm_dialog(): return
			set_setting('rd.username', '')
			set_setting('rd.client_id', '')
			set_setting('rd.token', '')
			set_setting('rd.refresh', '')
			set_setting('rd.secret', '')
			clear_cache('rd_cloud', silent=True)
			return notification('Removed %s Authorization' % self.__class__.__name__)

		params = {'client_id': self.client_id, 'new_credentials': 'yes'}
		response = requests.get(self.base_url('oauth/v2/device/code'), params=params, timeout=timeout)
		result = response.json()
		data = {'code': result['device_code'], 'grant_type': 'http://oauth.net/grant_type/device/1.0'}
		expires_in, expires_at = result['expires_in'], result['expires_in'] + time.monotonic()
		try: qr_icon = qr_str % '&data=%s' % quote(result['direct_verification_url'])
		except: qr_icon = ''
		meta = {**dict.fromkeys(meta_keys.split(), ''), 'poster': qr_icon}
		detail = code_str % result['user_code'], nav2_str % result['verification_url']
		progress_dialog = _make_progress_dialog(meta=meta)
		timer = RepeatTimer(result['interval'], self.poll_auth, args=(data,))
		timer.start()
		for i in range(1, expires_in + 1):
			if self.secret or progress_dialog.iscanceled(): break
			lines = await_str % divmod(expires_at - time.monotonic(), 60), *detail
			progress = 100 - int(100 * i / expires_in)
			progress_dialog.update('[CR]'.join(lines), progress)
			sleep(1000)
		timer.cancel()
		progress_dialog.close()
		if progress_dialog.iscanceled(): return False
		if not self.secret: return notification(32574)
		response = requests.post(self.base_url('oauth/v2/token'), data=data, timeout=timeout)
		data.update(response.json())
		sleep(500)
		headers = {'Authorization': 'Bearer %s' % data['access_token']}
		response = requests.get(self.base_url('rest/1.0/user'), headers=headers, timeout=timeout)
		username = response.json()['username']
		client_id, secret = data['client_id'], data['client_secret']
		token, refresh = data['access_token'], data['refresh_token']
		set_setting('rd.username', str(username))
		set_setting('rd.client_id', client_id)
		set_setting('rd.token', token)
		set_setting('rd.refresh', refresh)
		set_setting('rd.secret', secret)
		notification('Set %s Authorization' % self.__class__.__name__)
		return True

class AllDebrid:
	icon = 'premium.png'
	def __init__(self):
		self.token = get_setting('ad.token')

	def base_url(self, path):
		return 'https://api.alldebrid.com/%s' % path

	def poll_auth(self, data):
		response = requests.post(self.base_url('v4/pin/check'), data={'pin': data['pin'], 'check': data['check']}, timeout=timeout)
		if not response.ok: return
		result = response.json().get('data', {})
		if result.get('activated'): self.token = result.get('apikey', '')

	def set(self):
		if self.token:
			if not confirm_dialog(): return
			set_setting('ad.account_id', '')
			set_setting('ad.token', '')
			from debrids.all_debrid_api import AllDebridAPI
			AllDebridAPI().clear_cache()
			return notification('Removed %s Authorization' % self.__class__.__name__)

		response = requests.get(self.base_url('v4.1/pin/get'), timeout=timeout)
		response.raise_for_status()
		result = response.json()['data']
		expires_in, expires_at = result['expires_in'], result['expires_in'] + time.monotonic()
		try: qr_icon = qr_str % '&data=%s' % quote(result['user_url'])
		except: qr_icon = ''
		meta = {**dict.fromkeys(meta_keys.split(), ''), 'poster': qr_icon}
		detail = code_str % result['pin'], nav2_str % result['base_url']
		progress_dialog = _make_progress_dialog(meta=meta)
		timer = RepeatTimer(5, self.poll_auth, args=(result,))
		timer.start()
		for i in range(1, expires_in + 1):
			if self.token or progress_dialog.iscanceled(): break
			lines = await_str % divmod(expires_at - time.monotonic(), 60), *detail
			progress = 100 - int(100 * i / expires_in)
			progress_dialog.update('[CR]'.join(lines), progress)
			sleep(1000)
		timer.cancel()
		progress_dialog.close()
		if progress_dialog.iscanceled(): return False
		if not self.token: return notification(32574)
		headers = {'Authorization': 'Bearer %s' % self.token}
		response = requests.get(self.base_url('v4/user'), headers=headers, timeout=timeout)
		response.raise_for_status()
		username = response.json()['data']['user']['username']
		set_setting('ad.account_id', str(username))
		set_setting('ad.token', self.token)
		notification('Set %s Authorization' % self.__class__.__name__)
		return True
