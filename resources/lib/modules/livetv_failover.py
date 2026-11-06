"""Switch live match streams on request without exposing account credentials."""
import json
import math
import re
import uuid
from http.client import HTTPException
from threading import Timer
from time import monotonic, time
from urllib.parse import parse_qs, quote, urlsplit

from modules.livetv_vpn_relay import cancel, default_descriptor_path, initial_response_failed, register

from modules import kodi_utils as ku, livetv_feed_health

PROPERTY = 'BingieLiveTVFailover'
SWITCH_REQUEST = 'BingieLiveTVSwitchRequest'
STOP_REQUEST = 'BingieLiveTVStopRequest'
STOP_ACK = 'BingieLiveTVStopAck'
CAN_SWITCH = 'BingieLiveTVCanSwitch'
LOADING_SWITCH = 'BingieLiveTVLoadingSwitch'
FEED_CHOICES = 'BingieLiveTVFeedChoices'
OPENING = 'BingieTV'
STATUS = 'BingieLiveTVStatus'
PICKER_ACTIVE = 'BingieLiveTVSwitchPicker'
PICKER_BACK = 'BingieLiveTVSwitchPickerBack'
SESSION_OPEN_TIMEOUT = 30.0
OPENING_STOP_GRACE = 0.35


def _current_evidence(data):
	if 'expires' not in data: return True
	value = data['expires']
	try: return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and time() < value
	except OverflowError: return False


def request_manual_switch():
	"""Choose a feed for the active session; the service revalidates the request."""
	try:
		data = json.loads(ku.get_property(FEED_CHOICES) or '{}')
		token, current, raw_feeds = data.get('token'), data.get('current'), data.get('feeds')
		if not isinstance(token, str) or not token or not isinstance(current, str) or not isinstance(raw_feeds, list) or not ku.get_property(CAN_SWITCH) or ku.get_property(STOP_REQUEST) or not _current_evidence(data): raise ValueError('No session')
		feeds = [feed for feed in raw_feeds if isinstance(feed, dict) and _current_evidence(feed)]
		current_feed = next((feed for feed in feeds if feed.get('id') == current), None)
		if not current_feed or len(feeds) < 2: raise ValueError('No session')
		ku.clear_property(PICKER_BACK)
		picker = uuid.uuid4().hex
		ku.set_property(PICKER_ACTIVE, picker)
		confirmed = False
		try:
			outcomes = livetv_feed_health.recent(data.get('accounts'))
			labels = [' · '.join(part for part in (str(feed['name']) + (' (current)' if feed['id'] == current else ''), outcomes.get(feed['id'])) if part) for feed in feeds]
			choice = ku.dialog.select('Switch feed', labels, preselect=feeds.index(current_feed))
			if choice < 0: return False
			if choice >= len(feeds): raise ValueError('Invalid choice')
			selected = feeds[choice]['id']
			if selected == current: return False
			if json.loads(ku.get_property(FEED_CHOICES) or '{}') != data or not ku.get_property(CAN_SWITCH) or ku.get_property(STOP_REQUEST) or not _current_evidence(data) or not _current_evidence(current_feed) or not _current_evidence(feeds[choice]): return False
			if ku.get_property(PICKER_BACK) == picker:
				ku.set_property(STOP_REQUEST, 'true')
				return False
			confirmed = True
			ku.set_property(SWITCH_REQUEST, json.dumps({'token': token, 'current': current, 'id': selected, 'picker': picker}))
			if ku.get_property(PICKER_BACK) == picker: ku.set_property(STOP_REQUEST, 'true')
			return True
		finally:
			ku.clear_property(PICKER_ACTIVE)
			if not confirmed: ku.clear_property(PICKER_BACK)

	except (TypeError, ValueError, AttributeError, KeyError):
		ku.clear_property(PICKER_ACTIVE)
		ku.clear_property(PICKER_BACK)
		ku.dialog.notification('Live TV', 'No matching live stream is available to switch.')
		return False


class LiveTVFailover(ku.xbmc_player):
	def __init__(self, clock=monotonic):
		ku.xbmc_player.__init__(self)
		self.clock = clock
		self.session = None
		self.ignored_token = ''
		self.event = None
		self.caching = False
		self.stop_pending = False
		self.stop_ack_queued = False
		self.stop_ack_token = ''
		self.stop_relay_url = ''
		self.stop_cancel_retry_at = 0.0
		self.stop_during_open = False
		self.stop_interrupt = None

	def _clear(self, keep_stop=False):
		if self.session:
			if ku.get_property(OPENING) in self.session.get('urls', {}).values(): ku.clear_property(OPENING)
			self.ignored_token = self.session['token']
			try:
				current = json.loads(ku.get_property(PROPERTY) or '{}')
				if current.get('token') == self.session['token']: ku.clear_property(PROPERTY)
			except (ValueError, TypeError, AttributeError): pass
		self.session = None
		self.event = None
		self.caching = False
		if not ku.get_property(PICKER_ACTIVE): ku.clear_property(PICKER_BACK)
		ku.clear_property(SWITCH_REQUEST)
		if not keep_stop: ku.clear_property(STOP_REQUEST)
		ku.clear_property(CAN_SWITCH)
		ku.clear_property(LOADING_SWITCH)
		ku.clear_property(FEED_CHOICES)
		if not keep_stop: ku.clear_property(STATUS)

	def close(self):
		if self.stop_interrupt:
			self.stop_interrupt.cancel()
			self.stop_interrupt.join()
		if self.stop_relay_url:
			try: cancel(self.stop_relay_url)
			except (OSError, HTTPException, ValueError, TypeError): pass
		self.stop_relay_url = ''
		ku.clear_property(STOP_ACK)
		self._clear()

	def _prune_expired(self):
		if not self.session:
			self._clear()
			return False
		feeds = [feed for feed in self.session['feeds'] if _current_evidence(feed)] if _current_evidence(self.session) else []
		ids = {feed['id'] for feed in feeds}
		if self.session['id'] not in ids:
			# Keep only the playback identity so Back can cancel an expired stream.
			self.session['feeds'] = []
			return True
		self.session['feeds'] = feeds
		return True

	def _read_session(self, now):
		try:
			data = json.loads(ku.get_property(PROPERTY) or '{}')
			if not isinstance(data, dict): return
			token, stream_id, alternatives = data.get('token'), data.get('id'), data.get('alternatives')
			if not isinstance(token, str) or not token or token == self.ignored_token: return
			if self.session and token == self.session['token']: return
			if not _current_evidence(data):
				self.session = {'token': token}
				self._clear()
				return
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
			from menus.livetv import _feed_accounts_fingerprint
			playback_url = data.get('playback_url')
			urls = {stream_id: playback_url} if isinstance(playback_url, str) and re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}/r/[A-Za-z0-9_-]{32,}/resource(?:\.[A-Za-z0-9]{1,8})?', playback_url) else {}
			requested_at = data.get('requested_at')
			opening_at = requested_at if isinstance(requested_at, (float, int)) and now - SESSION_OPEN_TIMEOUT <= requested_at <= now else now
			self.session = {'token': token, 'id': stream_id, 'feeds': feeds, 'bases': {value: base_for(value) for value in allowed}, 'accounts': _feed_accounts_fingerprint(),
				'urls': urls, 'active': False, 'switching': False, 'opening_at': opening_at, 'saw_opening': False}
			if 'expires' in data: self.session['expires'] = data['expires']
			self.event = None
			self._prune_expired()
		except (ValueError, TypeError, KeyError, AttributeError): return

	def _url(self, stream_id):
		urls = self.session.setdefault('urls', {})
		if stream_id not in urls:
			try:
				url = self.session['bases'][stream_id] + stream_id.rsplit(':', 1)[-1] + '.ts'
				urls[stream_id] = register(url)
			except (OSError, HTTPException, ValueError, TypeError, KeyError): return ''
		return urls[stream_id]

	def _playing_file(self):
		try: return self.getPlayingFile() if self.isPlaying() else ''
		except Exception: return ''

	def _owns_playback(self, playing, stream_id, urls):
		if not playing or playing in urls: return True
		try:
			parsed = urlsplit(playing)
			query = parse_qs(parsed.query)
			return parsed.scheme == 'plugin' and parsed.netloc == 'skin.titan.bingie.lite' and query.get('mode') == ['livetv.play'] and query.get('id') == [stream_id]
		except (ValueError, TypeError): return False

	def _queue_stop(self, relay_url='', opening=False):
		descriptor = default_descriptor_path() if opening and relay_url else None
		ku.clear_property(STOP_ACK)
		ku.set_property(STATUS, 'Stopping live feed…')
		self.stop_pending = True
		self.stop_ack_queued = False
		self.stop_ack_token = uuid.uuid4().hex
		self.stop_relay_url = relay_url
		self.stop_during_open = opening
		self.stop_cancel_retry_at = 0.0
		try: ku.xbmc.executebuiltin('PlayerControl(Stop)')
		except Exception:
			self.stop_pending = False
			self.stop_relay_url = ''
			return False
		if descriptor:
			# Kodi may wait for its opening thread while processing Stop. The
			# interrupt must run without calling back into Kodi's blocked GUI.
			self.stop_interrupt = Timer(OPENING_STOP_GRACE, self._interrupt_opening, args=(relay_url, descriptor))
			self.stop_interrupt.daemon = True
			try: self.stop_interrupt.start()
			except RuntimeError: self.stop_interrupt = None
		self._queue_stop_ack()
		return True

	def _interrupt_opening(self, url, descriptor):
		try: cancel(url, descriptor_path=descriptor)
		except (OSError, HTTPException, ValueError, TypeError): return
		self.stop_relay_url = ''

	def _queue_stop_ack(self):
		try:
			ku.xbmc.executebuiltin('SetProperty(%s,%s,Home)' % (STOP_ACK, self.stop_ack_token))
			self.stop_ack_queued = True
		except Exception: pass

	def _cancel_unadopted(self):
		try:
			data = json.loads(ku.get_property(PROPERTY) or '{}')
			token, stream_id, url = data.get('token'), data.get('id'), data.get('playback_url')
			if not isinstance(token, str) or not token or not isinstance(stream_id, str) or not stream_id or not isinstance(url, str) or not self._owns_playback(self._playing_file(), stream_id, (url,)): return
			parsed = urlsplit(url)
			if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.query or parsed.fragment or not re.fullmatch(r'/r/[A-Za-z0-9_-]{32,}/resource(?:\.[A-Za-z0-9]{1,8})?', parsed.path): return
			current = json.loads(ku.get_property(PROPERTY) or '{}')
			if current != data or not self._owns_playback(self._playing_file(), stream_id, (url,)): return
			if not self._queue_stop(url, opening=True): return False
			if json.loads(ku.get_property(PROPERTY) or '{}') == data: ku.clear_property(PROPERTY)
			return True
		except (OSError, HTTPException, ValueError, TypeError, AttributeError, KeyError): pass
		return False

	def onAVStarted(self, observed_callback=True):
		if not self.session or not self._prune_expired(): return
		session = self.session
		# Kodi can report the old video stream during a queued replacement.
		if session['switching'] and not observed_callback: return
		expected = session.get('urls', {}).get(session['id'])
		if session['active'] or not expected or self._playing_file() != expected or ku.get_property(STOP_REQUEST): return
		if ku.get_property(OPENING) == expected: ku.clear_property(OPENING)
		session['active'] = True
		session['switching'] = False
		session.pop('restore', None)
		session.pop('outgoing', None)
		livetv_feed_health.record(session['accounts'], session['id'], True)
		if session.pop('restoring', False):
			ku.dialog.notification('Live TV', 'Could not switch feed. Previous feed restored.')

	def onPlayBackError(self):
		if self.session: self.event = ('error', self.session['token'], self.session['id'], self.session['switching'])

	def onPlayBackStopped(self):
		if self.session: self.event = ('stop', self.session['token'], self.session['id'], self.session['switching'])

	onPlayBackEnded = onPlayBackStopped

	def _publish_choices(self):
		session = self.session
		if session and (not session['active'] or ku.get_property(SWITCH_REQUEST)):
			ku.set_property(LOADING_SWITCH, 'true')
			if session.get('restoring'): status = 'Reopening previous feed…'
			elif session['switching'] or ku.get_property(SWITCH_REQUEST): status = 'Switching feed…'
			else: status = 'Opening live feed…'
			ku.set_property(STATUS, status)
			if (ku.get_visibility('Window.IsActive(busydialog)') or ku.get_visibility('Window.IsActive(busydialognocancel)')) and ku.get_visibility('Control.IsVisible(9900)') and not ku.get_visibility('Control.HasFocus(9900)'): ku.xbmc.executebuiltin('SetFocus(9900)')
		else:
			ku.clear_property(LOADING_SWITCH)
			ku.clear_property(STATUS)
		if session and session['active'] and len(session['feeds']) > 1:
			choices = {'token': session['token'], 'current': session['id'], 'feeds': session['feeds'], 'accounts': session['accounts']}
			if 'expires' in session: choices['expires'] = session['expires']
			ku.set_property(FEED_CHOICES, json.dumps(choices))
			ku.set_property(CAN_SWITCH, 'true')
		else:
			ku.clear_property(CAN_SWITCH)
			ku.clear_property(FEED_CHOICES)

	def _switch(self, selected):
		if not self._prune_expired(): return False
		session = self.session
		try:
			from menus.livetv import _feed_accounts_fingerprint
			if _feed_accounts_fingerprint() != session['accounts']:
				self._clear()
				return False
		except Exception:
			self._clear()
			return False
		if selected == session['id'] or selected not in [feed['id'] for feed in session['feeds']]: return False
		previous_id, previous_url = session['id'], self._url(session['id'])
		outgoing = self._playing_file() or previous_url
		if ku.get_property(STOP_REQUEST):
			return False
		session.setdefault('urls', {}).pop(selected, None)
		url = self._url(selected)
		if not url:
			ku.dialog.notification('Live TV', 'Live TV VPN unavailable. Check the VPN connection and try again.')
			return False
		if url == previous_url:
			session['urls'].pop(selected, None)
			if not ku.get_property(STOP_REQUEST): ku.dialog.notification('Live TV', 'The selected feed is already playing.')
			return False
		if ku.get_property(STOP_REQUEST):
			try: cancel(url)
			except (OSError, HTTPException, ValueError, TypeError): pass
			session['urls'].pop(selected, None)
			return False
		try:
			if not previous_url: raise ValueError('Missing outgoing relay capability')
			cancel(previous_url)
		except (OSError, HTTPException, ValueError, TypeError):
			try: cancel(url)
			except (OSError, HTTPException, ValueError, TypeError): pass
			session['urls'].pop(selected, None)
			if not ku.get_property(STOP_REQUEST): ku.dialog.notification('Live TV', 'Could not switch feed.')
			return False
		session['urls'].pop(previous_id, None)
		session['restore'] = previous_id
		session['outgoing'] = outgoing
		if self._open_feed(selected, url): return True
		return self._failed_open(record=False)

	def _open_feed(self, feed_id, url):
		session = self.session
		if not self._session_is_current(session): return False
		session.update(id=feed_id, active=False, switching=True, opening_at=self.clock(), saw_opening=False)
		self.event = None
		if ku.get_property(STOP_REQUEST): return False
		self._publish_choices()
		ku.set_property(OPENING, url)
		try:
			item = ku.xbmcgui.ListItem(path=url)
			item.setMimeType('video/mp2t')
			item.setContentLookup(False)
			self.play(url, item)
		except Exception: return False
		return True

	def _failed_open(self, record=True, dismiss_error=False):
		session = self.session
		if ku.get_property(STOP_REQUEST) or not self._session_is_current(session): return False
		feed_id = session['id']
		if record: livetv_feed_health.record(session['accounts'], feed_id, False)
		url = session.get('urls', {}).pop(feed_id, '')
		if url:
			if ku.get_property(OPENING) == url: ku.clear_property(OPENING)
			try: cancel(url)
			except (OSError, HTTPException, ValueError, TypeError): pass
		if ku.get_property(STOP_REQUEST) or not self._session_is_current(session): return False
		if dismiss_error and self._playback_error_visible(): ku.xbmc.executebuiltin('Dialog.Close(okdialog,true)')
		previous = session.pop('restore', None)
		if previous and _current_evidence(session) and any(feed['id'] == previous and _current_evidence(feed) for feed in session['feeds']):
			from menus.livetv import _feed_accounts_fingerprint
			if _feed_accounts_fingerprint() == session['accounts']:
				previous_url = self._url(previous)
				if previous_url:
					session['restoring'] = True
					ku.dialog.notification('Live TV', 'Could not switch feed. Reopening the previous feed.')
					if self._open_feed(previous, previous_url): return True
					if ku.get_property(STOP_REQUEST) or not self._session_is_current(session): return False
					try: cancel(previous_url)
					except (OSError, HTTPException, ValueError, TypeError): pass
		if session['switching']:
			ku.dialog.notification('Live TV', 'Could not switch feed or reopen the previous feed.')
		self._clear()
		return False

	def _playback_error_visible(self):
		return ku.get_visibility('Window.IsActive(okdialog) + String.IsEqual(Control.GetLabel(1),$LOCALIZE[16026])')

	def _session_is_current(self, session):
		try: current = json.loads(ku.get_property(PROPERTY) or '{}')
		except (ValueError, TypeError): return False
		return isinstance(current, dict) and current.get('token') == session['token']

	def tick(self):
		picker_back = ku.get_property(PICKER_BACK)
		if picker_back and self.session:
			try: pending = json.loads(ku.get_property(SWITCH_REQUEST) or '{}')
			except (TypeError, ValueError): pending = {}
			queued = isinstance(pending, dict) and pending.get('token') == self.session['token'] and pending.get('current') == self.session['id'] and pending.get('picker') == picker_back
			opening = self.session.get('picker') == picker_back
			if queued or opening:
				ku.clear_property(PICKER_BACK)
				ku.set_property(STOP_REQUEST, 'true')
		if self.stop_pending:
			if self.stop_interrupt:
				if self.stop_interrupt.is_alive(): return True
				self.stop_interrupt = None
			acknowledged = ku.get_property(STOP_ACK) == self.stop_ack_token
			if self.stop_relay_url and self.clock() >= self.stop_cancel_retry_at and acknowledged:
				try:
					cancel(self.stop_relay_url)
					self.stop_relay_url = ''
				except ValueError:
					# The descriptor now belongs to a different relay.
					self.stop_relay_url = ''
				except (OSError, HTTPException, TypeError):
					self.stop_cancel_retry_at = self.clock() + 1.0
			if self.stop_during_open and not self._playing_file() and self._playback_error_visible():
				ku.xbmc.executebuiltin('Dialog.Close(okdialog,true)')
			if acknowledged and not self.stop_relay_url:
				self.stop_pending = False
				self.stop_ack_queued = False
				ku.clear_property(STOP_ACK)
				ku.clear_property(STOP_REQUEST)
				ku.clear_property(STATUS)
				self.stop_ack_token = ''
			elif not self.stop_ack_queued: self._queue_stop_ack()
			return self.stop_pending
		if not self.session and ku.get_property(STOP_REQUEST):
			if not self._cancel_unadopted(): ku.clear_property(STOP_REQUEST)
			return False
		self._read_session(self.clock())
		if not self.session:
			ku.clear_property(STOP_REQUEST)
			return False
		session = self.session
		if ku.get_property(STOP_REQUEST):
			url = session.get('urls', {}).get(session['id']) or self._url(session['id'])
			try: current = json.loads(ku.get_property(PROPERTY) or '{}')
			except (ValueError, TypeError): current = {}
			playing = self._playing_file()
			urls = tuple(session.get('urls', {}).values()) + (session.get('outgoing'),)
			stop = isinstance(current, dict) and current.get('token') == session['token'] and self._owns_playback(playing, session['id'], urls)
			queued = self._queue_stop(url, opening=not session['active']) if stop else False
			if url and not queued:
				try: cancel(url)
				except (OSError, HTTPException, ValueError, TypeError): pass
			self._clear(keep_stop=queued)
			return queued
		if not self._prune_expired(): return False
		if not self._session_is_current(session):
			self._clear()
			return False
		playing, expected = self._playing_file(), self._url(session['id'])
		if not expected:
			self._clear()
			return False
		event, self.event = self.event, None
		own_event = event and event[1:3] == (session['token'], session['id'])
		if not session['active']:
			busy = ku.get_visibility('Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel)')
			if playing == expected: session['saw_opening'] = True
			if not playing or playing == session.get('outgoing'):
				failed_dialog = self._playback_error_visible()
				elapsed = self.clock() - session['opening_at']
				failed_response = initial_response_failed(expected, session['opening_at']) and not busy
				# A stop callback may refer to the outgoing player while Kodi queues the replacement.
				ended = own_event and not busy and session['saw_opening'] and elapsed >= 1.0
				if failed_response or failed_dialog or ended or (not busy and elapsed >= SESSION_OPEN_TIMEOUT):
					return self._failed_open(dismiss_error=bool(session['switching'] and failed_dialog))
		elif own_event:
			# Kodi also reports a stop for the outgoing player during a requested switch.
			if not event[3] and event[0] == 'stop' and playing == expected:
				# A delayed outgoing callback can arrive after the new feed starts.
				# Discard queued switches until Kodi confirms whether playback stopped.
				ku.clear_property(SWITCH_REQUEST)
				return True
			if not event[3] and (event[0] == 'stop' or not playing):
				self._clear()
				return False
		if playing and playing != expected and (session['active'] or (session.get('outgoing') and playing != session['outgoing'])):
			self._clear()
			return False
		if not playing and session['active']:
			self._clear()
			return False
		if playing == expected and not session['active'] and not session['switching'] and ku.get_visibility('Window.IsActive(fullscreenvideo) + Player.HasVideo'):
			try:
				if self.isPlayingVideo(): self.onAVStarted(False)
			except Exception: pass
		self._publish_choices()
		caching = bool((session['active'] and ku.get_visibility('Player.Caching')) or ku.get_property(SWITCH_REQUEST) or ku.get_property(LOADING_SWITCH))
		can_focus = ku.get_visibility('Window.IsActive(fullscreenvideo)') and not ku.get_visibility('Window.IsVisible(videoosd)')
		if caching and not self.caching and can_focus: ku.xbmc.executebuiltin('SetFocus(9902)')
		self.caching = bool(caching and (self.caching or can_focus))
		manual = ku.get_property(SWITCH_REQUEST)
		if manual:
			try: request = json.loads(manual)
			except (ValueError, TypeError): request = {}
			valid = isinstance(request, dict) and request.get('token') == session['token'] and request.get('current') == session['id'] and isinstance(request.get('picker'), str) and bool(re.fullmatch(r'[0-9a-f]{32}', request['picker'])) and session['active'] and playing == expected
			if valid: ku.set_property(LOADING_SWITCH, 'true')
			ku.clear_property(SWITCH_REQUEST)
			if valid:
				session['picker'] = request.get('picker')
				switched = self._switch(request.get('id'))
				if not switched and self.session and not ku.get_property(STOP_REQUEST):
					if ku.get_property(PICKER_BACK) == session.get('picker'): ku.clear_property(PICKER_BACK)
					session.pop('picker', None)
					self._publish_choices()
				return switched
			self._publish_choices()
		return True
