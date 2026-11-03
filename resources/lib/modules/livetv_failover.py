"""Recover selected live match streams without exposing account credentials."""
import json
import math
import uuid
from http.client import HTTPException
from queue import Empty, SimpleQueue
from threading import Thread
from time import monotonic, time
from urllib.error import HTTPError
from urllib.parse import quote

from modules.livetv_vpn_relay import open_url, register

from modules import kodi_utils as ku
try:
	from modules import livetv_feed_speed
except ImportError:
	livetv_feed_speed = None

PROPERTY = 'BingieLiveTVFailover'
PLAYBACK_FAILED = 'BingieLiveTVPlaybackFailed'
LIVE_OPENING = 'BingieLiveTVOpening'
SWITCH_REQUEST = 'BingieLiveTVSwitchRequest'
CAN_SWITCH = 'BingieLiveTVCanSwitch'
FEED_CHOICES = 'BingieLiveTVFeedChoices'
OPENING = 'BingieTV'
START_TIMEOUT = 15.0
STALL_TIMEOUT = 8.0
PROBE_DELAY = 2.0
PROBE_TIMEOUT = 3.0
MAX_ALTERNATIVES = 3


def _current_evidence(data):
	if 'expires' not in data: return True
	value = data['expires']
	try: return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and time() < value
	except OverflowError: return False


def _playback_error_dialog():
	try:
		heading = ku.xbmc.getLocalizedString(16026).strip()
		return bool(heading and ku.get_visibility('Window.IsActive(12002)') and ku.get_infolabel('Control.GetLabel(1)').strip() == heading)
	except Exception:
		return False


def _probe_failure(url):
	"""Return true only for an explicit server failure; uncertainty is not a failure."""
	try:
		with open_url(url, headers={'User-Agent': 'BINGIE Live TV'}, timeout=PROBE_TIMEOUT): return False
	except HTTPError as exc:
		try:
			if exc.headers and exc.headers.get('X-Bingie-Relay-Error') == '1': return False
			return exc.code in (404, 410, 458) or 500 <= exc.code <= 599
		finally: exc.close()
	except Exception:
		return False


def request_manual_switch():
	"""Choose a feed for the active session; the service revalidates the request."""
	try:
		data = json.loads(ku.get_property(FEED_CHOICES) or '{}')
		token, current, raw_feeds = data.get('token'), data.get('current'), data.get('feeds')
		if not isinstance(token, str) or not token or not isinstance(current, str) or not isinstance(raw_feeds, list) or not ku.get_property(CAN_SWITCH) or not _current_evidence(data): raise ValueError('No session')
		feeds = [feed for feed in raw_feeds if isinstance(feed, dict) and _current_evidence(feed)]
		current_feed = next((feed for feed in feeds if feed.get('id') == current), None)
		if not current_feed or len(feeds) < 2: raise ValueError('No session')
		choice = ku.dialog.select('Switch feed', [str(feed['name']) + (' (current)' if feed['id'] == current else '') for feed in feeds])
		if choice < 0: return False
		if choice >= len(feeds): raise ValueError('Invalid choice')
		selected = feeds[choice]['id']
		if selected == current: return False
		if json.loads(ku.get_property(FEED_CHOICES) or '{}') != data or not ku.get_property(CAN_SWITCH) or not _current_evidence(data) or not _current_evidence(current_feed) or not _current_evidence(feeds[choice]): return False
		ku.set_property(SWITCH_REQUEST, json.dumps({'token': token, 'current': current, 'id': selected}))
		return True
	except (TypeError, ValueError, AttributeError, KeyError):
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
		self.manual_probe_results = SimpleQueue()

	def _clear(self):
		if self.session:
			if ku.get_property(OPENING) in self.session.get('urls', {}).values(): ku.clear_property(OPENING)
			self.ignored_token = self.session['token']
			try:
				current = json.loads(ku.get_property(PROPERTY) or '{}')
				if current.get('token') == self.session['token']: ku.clear_property(PROPERTY)
			except (ValueError, TypeError, AttributeError): pass
		self.session = None
		self.event = None
		ku.clear_property(PLAYBACK_FAILED)
		ku.clear_property(LIVE_OPENING)
		ku.clear_property(SWITCH_REQUEST)
		ku.clear_property(CAN_SWITCH)
		ku.clear_property(FEED_CHOICES)

	def close(self):
		self._clear()

	def _prune_expired(self):
		if not self.session or not _current_evidence(self.session):
			self._clear()
			return False
		feeds = [feed for feed in self.session['feeds'] if _current_evidence(feed)]
		ids = {feed['id'] for feed in feeds}
		if self.session['id'] not in ids:
			self._clear()
			return False
		self.session['feeds'] = feeds
		self.session['alternatives'] = [value for value in self.session['alternatives'] if value in ids]
		return True

	def _read_session(self, now):
		try:
			data = json.loads(ku.get_property(PROPERTY) or '{}')
			if not isinstance(data, dict): return
			token, stream_id, alternatives = data.get('token'), data.get('id'), data.get('alternatives')
			if not isinstance(token, str) or not token or token == self.ignored_token: return
			if not _current_evidence(data):
				self.session = {'token': token}
				self._clear()
				return
			if self.session and token == self.session['token']: return
			def valid(value):
				if not isinstance(value, str): return False
				if value.isdecimal(): return True
				parts = value.split(':')
				return len(parts) == 2 and bool(parts[0]) and parts[1].isdecimal()
			if not valid(stream_id) or not isinstance(alternatives, list): return
			raw_feeds = data.get('feeds', []) if isinstance(data.get('feeds', []), list) else []
			blocked = {feed.get('id') for feed in raw_feeds if isinstance(feed, dict) and valid(feed.get('id')) and not _current_evidence(feed)}
			if stream_id in blocked:
				self.session = {'token': token}
				self._clear()
				return
			ids = []
			for value in alternatives:
				if valid(value) and value != stream_id and value not in ids and value not in blocked: ids.append(value)
			from menus.livetv import _load
			account = _load()
			if not account and ':' not in stream_id: return
			def base_for(value):
				if ':' in value:
					from menus.livetv import _account_for_feed
					entry = _account_for_feed(value.split(':', 1)[0])
				else: entry = account
				if not entry: return None
				return '%s/live/%s/%s/' % (entry['server'], quote(entry['username'], safe=''), quote(entry['password'], safe=''))
			base = base_for(stream_id)
			if not base: return
			ids = [value for value in ids if base_for(value)]
			allowed = [stream_id] + ids
			labels, deadlines = {}, {}
			for feed in raw_feeds:
				if not isinstance(feed, dict): continue
				feed_id, name = feed.get('id'), feed.get('name')
				if not valid(feed_id) or feed_id in blocked or not base_for(feed_id) or not isinstance(name, str): continue
				if feed_id not in allowed: allowed.append(feed_id)
				labels[feed_id] = name or 'Feed'
				if 'expires' in feed: deadlines[feed_id] = min(feed['expires'], deadlines.get(feed_id, feed['expires']))
			feeds = [dict({'id': value, 'name': labels.get(value, 'Feed ' + str(index + 1))}, **({'expires': deadlines[value]} if value in deadlines else {})) for index, value in enumerate(allowed)]
			def speed_key(value):
				if not livetv_feed_speed: return ''
				if ':' in value:
					from menus.livetv import _account_for_feed
					entry = _account_for_feed(value.split(':', 1)[0])
				else: entry = account
				return livetv_feed_speed.key(value, entry)
			keys = {value: speed_key(value) for value in allowed}
			manual_only = data.get('manual_only') is True
			self.session = {'token': token, 'id': stream_id, 'alternatives': [] if manual_only else ids[:MAX_ALTERNATIVES], 'feeds': feeds, 'base': base, 'bases': {value: base_for(value) for value in allowed}, 'started_at': data.get('started_at', now),
				'speed_keys': keys, 'timing_valid': isinstance(data.get('started_at'), (int, float)) and 0 <= now - data['started_at'] <= START_TIMEOUT, 'active': False, 'last_time': None, 'last_progress': now, 'paused': False, 'switching': False, 'foreign_seen': False, 'observed': False, 'probe_started': False, 'probe_allowed': True}
			self.session['manual_only'] = manual_only
			if 'expires' in data: self.session['expires'] = data['expires']
			self.event = None
			self._prune_expired()
		except (ValueError, TypeError, KeyError, AttributeError): return

	def _url(self, stream_id):
		urls = self.session.setdefault('urls', {})
		if stream_id not in urls:
			try:
				url = self.session.get('bases', {}).get(stream_id, self.session['base']) + stream_id.rsplit(':', 1)[-1] + '.ts'
				urls[stream_id] = register(url)
			except (OSError, HTTPException, ValueError, TypeError, KeyError): return ''
		return urls[stream_id]

	def _playing_file(self):
		try: return self.getPlayingFile() if self.isPlaying() else ''
		except Exception: return ''

	def onAVStarted(self, observed_callback=True):
		if not self.session: return
		if not self._prune_expired(): return
		expected = self._url(self.session['id'])
		if expected and self._playing_file() == expected:
			ku.clear_property(LIVE_OPENING)
			if ku.get_property(OPENING) == expected: ku.clear_property(OPENING)
			if not self.session['active'] and self.session.get('timing_valid') and livetv_feed_speed:
				elapsed = self.clock() - self.session['started_at']
				if 0 <= elapsed <= START_TIMEOUT: livetv_feed_speed.record(self.session['speed_keys'].get(self.session['id'], ''), elapsed)
			self.session['active'] = True
			self.session['observed'] = True
			self.session['switching'] = False
			self.session['last_time'] = None
			self.session['last_progress'] = self.clock()
			self.event = None
			if len(self.session['feeds']) > 1: ku.set_property(CAN_SWITCH, 'true')

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

	def _next(self, now, selected=None):
		if not self._prune_expired(): return False
		if selected is not None and selected not in [feed['id'] for feed in self.session['feeds']]: return False
		ku.clear_property(PLAYBACK_FAILED)
		if selected is None and self.session and livetv_feed_speed: livetv_feed_speed.record(self.session.get('speed_keys', {}).get(self.session['id'], ''), None)
		ku.clear_property(CAN_SWITCH)
		ku.clear_property(FEED_CHOICES)
		if selected is None and not self.session['alternatives']:
			ku.dialog.notification('Live TV', 'No more matching streams are available.')
			self._clear()
			return False
		previous_url = self._url(self.session['id'])
		self.session['id'] = selected if selected is not None else self.session['alternatives'].pop(0)
		if selected is not None: self.session['alternatives'] = [value for value in self.session['alternatives'] if value != selected]
		self.session.update(started_at=now, active=False, last_time=None, last_progress=now, paused=False, switching=True, outgoing=previous_url, foreign_seen=False, observed=False, probe_started=False, probe_allowed=False, failure_dialog=False, stop_pending=None, timing_valid=True)
		self.event = None
		# Capabilities belong to one relay instance and can expire or disappear on restart.
		self.session.setdefault('urls', {}).pop(self.session['id'], None)
		url = self._url(self.session['id'])
		if not url:
			ku.dialog.notification('Live TV', 'Live TV VPN unavailable. Check the VPN connection and try again.')
			self._clear()
			return False
		ku.dialog.notification('Live TV', 'Switching feed.' if selected is not None else 'Stream interrupted. Trying another feed for this match.')
		ku.set_property(OPENING, url)
		ku.set_property(LIVE_OPENING, 'true')
		try: self.play(url)
		except Exception:
			self.event = ('error', self.session['token'], self.session['id'])
		finally:
			self.session['switching'] = False
		return True

	def _probe(self, token, stream_id, url):
		self.probe_results.put((token, stream_id, _probe_failure(url)))

	def _probe_manual(self, token, current, selected, nonce, url):
		valid = False
		try:
			with open_url(url, headers={'User-Agent': 'BINGIE Live TV', 'Accept-Encoding': 'identity'}, timeout=PROBE_TIMEOUT) as response:
				status = getattr(response, 'status', 200)
				content_type = str(response.headers.get('Content-Type', '')).lower()
				if 200 <= status < 300 and not any(value in content_type for value in ('text/html', 'application/json', 'text/plain')):
					first = response.read(1)
					valid = bool(first) and first not in (b'<', b'{')
		except Exception: pass
		self.manual_probe_results.put((token, current, selected, nonce, valid))

	def _start_manual_probe(self, session, selected):
		nonce = uuid.uuid4().hex
		session['manual_selected'], session['manual_nonce'] = selected, nonce
		url = session['bases'][selected] + selected.rsplit(':', 1)[-1] + '.ts'
		Thread(target=self._probe_manual, args=(session['token'], session['id'], selected, nonce, url), daemon=True).start()
		ku.dialog.notification('Live TV', 'Checking feed.')

	def tick(self):
		now = self.clock()
		self._read_session(now)
		if not self.session: return False
		if not self._prune_expired(): return False
		session = self.session
		try: current = json.loads(ku.get_property(PROPERTY) or '{}')
		except (ValueError, TypeError): current = {}
		if not isinstance(current, dict) or current.get('token') != session['token']:
			self._clear()
			return False
		if not session['active'] and not session.get('outgoing') and session['id'] == current.get('id') and isinstance(current.get('started_at'), (int, float)):
			session['started_at'] = current['started_at']
			session['timing_valid'] = 0 <= now - current['started_at'] <= START_TIMEOUT
		playing = self._playing_file()
		expected = self._url(session['id'])
		if not expected:
			ku.dialog.notification('Live TV', 'Live TV VPN unavailable. Check the VPN connection and try again.')
			self._clear()
			return False
		if session['active'] and playing == expected and len(session['feeds']) > 1:
			ku.set_property(CAN_SWITCH, 'true')
			choices = {'token': session['token'], 'current': session['id'], 'feeds': session['feeds']}
			if 'expires' in session: choices['expires'] = session['expires']
			ku.set_property(FEED_CHOICES, json.dumps(choices))
		else:
			ku.clear_property(CAN_SWITCH)
			ku.clear_property(FEED_CHOICES)
		if playing and playing != expected:
			if not session['active'] and playing == session.get('outgoing'):
				if now - session['started_at'] < START_TIMEOUT: return True
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
			try: request = json.loads(manual)
			except (ValueError, TypeError): request = {}
			if isinstance(request, dict) and request.get('token') == session['token'] and request.get('current') == session['id'] and session['active'] and playing == expected:
				selected = request.get('id')
				if selected != session['id'] and selected in [feed['id'] for feed in session['feeds']]:
					if not session['manual_only']: return self._next(now, selected)
					if session.get('manual_nonce'): session['manual_queued'] = selected
					else: self._start_manual_probe(session, selected)
		while True:
			try: token, current_id, selected, nonce, valid = self.manual_probe_results.get_nowait()
			except Empty: break
			if (token, current_id, selected, nonce) != (session['token'], session['id'], session.get('manual_selected'), session.get('manual_nonce')): continue
			session.pop('manual_selected', None)
			session.pop('manual_nonce', None)
			queued = session.pop('manual_queued', None)
			if queued and queued != selected:
				if session['active'] and playing == expected and queued in [feed['id'] for feed in session['feeds']] and _current_evidence(session): self._start_manual_probe(session, queued)
				continue
			if not session['active'] or playing != expected or selected not in [feed['id'] for feed in session['feeds']] or not _current_evidence(session): continue
			if valid: return self._next(now, selected)
			ku.dialog.notification('Live TV', 'This feed did not respond. Choose another feed.')
		if not session['active'] and not playing and not session.get('failure_dialog') and (ku.get_property(PLAYBACK_FAILED) or _playback_error_dialog()):
			session['failure_dialog'] = True
			ku.xbmc.executebuiltin('Dialog.Close(okdialog)')
			ku.dialog.notification('Live TV', 'Playback failed. Checking for another feed.' if session['alternatives'] else 'Playback failed. Please try again.')
			if not session['alternatives']:
				self._clear()
				return False
		event, self.event = self.event, None
		if event and event[1:] == (session['token'], session['id']):
			if event[0] == 'stop' and playing != expected and not session.get('failure_dialog'):
				if session['active'] or session['observed']:
					self._clear()
					return False
				session['stop_pending'] = now
			if event[0] == 'error' and (session['observed'] or playing == expected): return self._next(now)
		if not session['active']:
			if session.get('stop_pending') is not None and not playing and not session.get('failure_dialog') and now - session['stop_pending'] >= PROBE_DELAY:
				self._clear()
				return False
			if not session['probe_allowed'] and not ku.get_visibility('Window.IsActive(12002)'):
				session['probe_allowed'] = True
			while True:
				try: token, stream_id, failed = self.probe_results.get_nowait()
				except Empty: break
				if (token, stream_id) == (session['token'], session['id']) and not playing and session.get('failure_dialog'):
					if failed: return self._next(now)
					ku.dialog.notification('Live TV', 'Playback failed. Please try again.')
					self._clear()
					return False
			if playing == expected:
				session.pop('stop_pending', None)
				session['foreign_seen'] = False
				session['observed'] = True
				# Recover if AV started before the service adopted the session.
				try:
					if self.isPlayingVideo(): self.onAVStarted(False)
				except Exception: pass
			elif not playing and session['probe_allowed'] and not session['probe_started'] and now - session['started_at'] >= PROBE_DELAY and session.get('failure_dialog'):
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
