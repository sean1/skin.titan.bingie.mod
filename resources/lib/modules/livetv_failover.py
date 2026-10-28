"""Recover selected live match streams without exposing account credentials."""
import json
from queue import Empty, SimpleQueue
from threading import Thread
from time import monotonic
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from modules import kodi_utils as ku

PROPERTY = 'BingieLiveTVFailover'
SWITCH_REQUEST = 'BingieLiveTVSwitchRequest'
CAN_SWITCH = 'BingieLiveTVCanSwitch'
START_TIMEOUT = 15.0
STALL_TIMEOUT = 8.0
PROBE_DELAY = 2.0
PROBE_TIMEOUT = 3.0
MAX_ALTERNATIVES = 3


def _playback_error_dialog():
	try:
		heading = ku.xbmc.getLocalizedString(16026).strip()
		return bool(heading and ku.get_visibility('Window.IsActive(12002)') and ku.get_infolabel('Control.GetLabel(1)').strip() == heading)
	except Exception:
		return False


def _probe_failure(url):
	"""Return true only for an explicit server failure; uncertainty is not a failure."""
	try:
		with urlopen(Request(url, headers={'User-Agent': 'BINGIE Live TV'}), timeout=PROBE_TIMEOUT): return False
	except HTTPError as exc:
		try: return exc.code in (404, 410) or 500 <= exc.code <= 599
		finally: exc.close()
	except Exception:
		return False


def request_manual_switch():
	"""Request a switch for the session already shown in Live TV, never a new one."""
	try:
		data = json.loads(ku.get_property(PROPERTY) or '{}')
		token = data.get('token')
		if not isinstance(token, str) or not token or not data.get('alternatives') or not ku.get_property(CAN_SWITCH): raise ValueError('No session')
		ku.set_property(SWITCH_REQUEST, token)
		return True
	except (TypeError, ValueError, AttributeError):
		ku.dialog.notification('Live TV', 'No matching live stream is available to switch.')
		return False


class LiveTVFailover(ku.xbmc_player):
	def __init__(self, clock=monotonic):
		ku.xbmc_player.__init__(self)
		self.clock = clock
		self.session = None
		self.ignored_token = ''
		self.event = None
		self.probe_results = SimpleQueue()

	def _clear(self):
		if self.session:
			self.ignored_token = self.session['token']
			try:
				current = json.loads(ku.get_property(PROPERTY) or '{}')
				if current.get('token') == self.session['token']: ku.clear_property(PROPERTY)
			except (ValueError, TypeError, AttributeError): pass
		self.session = None
		self.event = None
		ku.clear_property(SWITCH_REQUEST)
		ku.clear_property(CAN_SWITCH)

	def close(self):
		self._clear()

	def _read_session(self, now):
		try:
			data = json.loads(ku.get_property(PROPERTY) or '{}')
			if not isinstance(data, dict): return
			token, stream_id, alternatives = data.get('token'), data.get('id'), data.get('alternatives')
			if not isinstance(token, str) or not token or token == self.ignored_token: return
			if self.session and token == self.session['token']: return
			if not isinstance(stream_id, str) or not stream_id.isdecimal() or not isinstance(alternatives, list): return
			ids = []
			for value in alternatives:
				if isinstance(value, str) and value.isdecimal() and value != stream_id and value not in ids: ids.append(value)
			if not ids:
				self.session = {'token': token}
				self._clear()
				return
			from menus.livetv import _load
			account = _load()
			if not account: return
			base = '%s/live/%s/%s/' % (account['server'], quote(account['username'], safe=''), quote(account['password'], safe=''))
			self.session = {'token': token, 'id': stream_id, 'alternatives': ids[:MAX_ALTERNATIVES], 'base': base, 'started_at': now,
				'active': False, 'last_time': None, 'last_progress': now, 'paused': False, 'switching': False, 'foreign_seen': False, 'observed': False, 'probe_started': False, 'probe_allowed': True}
			self.event = None
		except (ValueError, TypeError, KeyError, AttributeError): return

	def _url(self, stream_id):
		return self.session['base'] + stream_id + '.ts'

	def _playing_file(self):
		try: return self.getPlayingFile() if self.isPlaying() else ''
		except Exception: return ''

	def onAVStarted(self):
		if not self.session: return
		if self._playing_file() == self._url(self.session['id']):
			self.session['active'] = True
			self.session['observed'] = True
			self.session['switching'] = False
			self.session['last_time'] = None
			self.session['last_progress'] = self.clock()
			self.event = None
			if self.session['alternatives']: ku.set_property(CAN_SWITCH, 'true')

	def onPlayBackError(self):
		# A delayed callback from the previous feed cannot safely identify the new one.
		if self.session and not self.session['switching'] and self.clock() - self.session['started_at'] >= 2.0:
			self.event = ('error', self.session['token'], self.session['id'])

	def onPlayBackStopped(self):
		if self.session and not self.session['switching']:
			self.event = ('stop', self.session['token'], self.session['id'])

	onPlayBackEnded = onPlayBackStopped

	def onPlayBackPaused(self):
		if self.session: self.session['paused'] = True

	def onPlayBackResumed(self):
		if self.session:
			self.session['paused'] = False
			self.session['last_time'] = None
			self.session['last_progress'] = self.clock()

	def onPlayBackSeek(self, *args):
		if self.session:
			self.session['last_time'] = None
			self.session['last_progress'] = self.clock()

	def _next(self, now):
		ku.clear_property(CAN_SWITCH)
		if not self.session['alternatives']:
			ku.dialog.notification('Live TV', 'No more matching streams are available.')
			self._clear()
			return False
		previous_url = self._url(self.session['id'])
		self.session['id'] = self.session['alternatives'].pop(0)
		self.session.update(started_at=now, active=False, last_time=None, last_progress=now, paused=False, switching=True, outgoing=previous_url, foreign_seen=False, observed=False, probe_started=False, probe_allowed=False)
		self.event = None
		ku.dialog.notification('Live TV', 'Stream interrupted. Trying another feed for this match.')
		try: self.play(self._url(self.session['id']))
		except Exception:
			self.event = ('error', self.session['token'], self.session['id'])
		finally:
			self.session['switching'] = False
		return True

	def _probe(self, token, stream_id, url):
		self.probe_results.put((token, stream_id, _probe_failure(url)))

	def tick(self):
		now = self.clock()
		self._read_session(now)
		if not self.session: return False
		session = self.session
		try: current = json.loads(ku.get_property(PROPERTY) or '{}')
		except (ValueError, TypeError): current = {}
		if not isinstance(current, dict) or current.get('token') != session['token']:
			self._clear()
			return False
		playing = self._playing_file()
		expected = self._url(session['id'])
		if session['active'] and playing == expected and session['alternatives']:
			ku.set_property(CAN_SWITCH, 'true')
		else:
			ku.clear_property(CAN_SWITCH)
		if playing and playing != expected:
			if not session['active'] and playing == session.get('outgoing'):
				if now - session['started_at'] < 3.0: return True
				self._clear()
				return False
			session['foreign_seen'] = True
			# Kodi may still be replacing the previous player while resolving a new item.
			if now - session['started_at'] >= 3.0:
				self._clear()
				return False
			return True
		if not playing and session['foreign_seen'] and not session['active']:
			self._clear()
			return False
		manual = ku.get_property(SWITCH_REQUEST)
		if manual:
			ku.clear_property(SWITCH_REQUEST)
			if manual == session['token'] and session['active'] and playing == expected:
				return self._next(now)
		event, self.event = self.event, None
		if event and event[1:] == (session['token'], session['id']):
			if event[0] == 'stop' and playing != expected:
				self._clear()
				return False
			if event[0] == 'error' and (session['observed'] or playing == expected): return self._next(now)
		if not session['active']:
			if not session['probe_allowed'] and not ku.get_visibility('Window.IsActive(12002)'):
				session['probe_allowed'] = True
			while True:
				try: token, stream_id, failed = self.probe_results.get_nowait()
				except Empty: break
				if failed and (token, stream_id) == (session['token'], session['id']) and not playing and _playback_error_dialog():
					ku.xbmc.executebuiltin('Dialog.Close(okdialog)')
					return self._next(now)
			if playing == expected:
				session['foreign_seen'] = False
				session['observed'] = True
				# Recover if AV started before the service adopted the session.
				try:
					if self.isPlayingVideo(): self.onAVStarted()
				except Exception: pass
			elif not playing and session['probe_allowed'] and not session['probe_started'] and now - session['started_at'] >= PROBE_DELAY and _playback_error_dialog():
				session['probe_started'] = True
				Thread(target=self._probe, args=(session['token'], session['id'], expected), daemon=True).start()
			if not session['active'] and now - session['started_at'] >= START_TIMEOUT:
				# If this service never observed playback, a stop may have been missed.
				if not session['observed']:
					self._clear()
					return False
				return self._next(now)
			return True
		if not playing:
			# A generic stop or end is not proof of failure; respect manual stops.
			self._clear()
			return False
		try: paused = session['paused'] or self.getPlaySpeed() == 0
		except Exception: paused = session['paused']
		if paused:
			session['last_time'] = None
			session['last_progress'] = now
			return True
		try: media_time = float(self.getTime())
		except (Exception,):
			session['last_time'] = None
			session['last_progress'] = now
			return True
		previous = session['last_time']
		session['last_time'] = media_time
		if previous is None or abs(media_time - previous) >= 0.2: session['last_progress'] = now
		elif now - session['last_progress'] >= STALL_TIMEOUT: return self._next(now)
		return True
