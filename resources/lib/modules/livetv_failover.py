"""Switch live match streams on request without exposing account credentials."""
import json
import math
import re
from http.client import HTTPException
from time import monotonic, time
from urllib.parse import parse_qs, quote, urlsplit

from modules.livetv_vpn_relay import cancel, register

from modules import kodi_utils as ku

PROPERTY = 'BingieLiveTVFailover'
SWITCH_REQUEST = 'BingieLiveTVSwitchRequest'
STOP_REQUEST = 'BingieLiveTVStopRequest'
CAN_SWITCH = 'BingieLiveTVCanSwitch'
LOADING_SWITCH = 'BingieLiveTVLoadingSwitch'
FEED_CHOICES = 'BingieLiveTVFeedChoices'
OPENING = 'BingieTV'
SESSION_OPEN_TIMEOUT = 30.0


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
		choice = ku.dialog.select('Switch feed', [str(feed['name']) + (' (current)' if feed['id'] == current else '') for feed in feeds])
		if choice < 0: return False
		if choice >= len(feeds): raise ValueError('Invalid choice')
		selected = feeds[choice]['id']
		if selected == current: return False
		if json.loads(ku.get_property(FEED_CHOICES) or '{}') != data or not ku.get_property(CAN_SWITCH) or ku.get_property(STOP_REQUEST) or not _current_evidence(data) or not _current_evidence(current_feed) or not _current_evidence(feeds[choice]): return False
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
		self.caching = False
		self.stop_pending = False
		self.stop_ack_queued = False

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
		ku.clear_property(SWITCH_REQUEST)
		if not keep_stop: ku.clear_property(STOP_REQUEST)
		ku.clear_property(CAN_SWITCH)
		ku.clear_property(LOADING_SWITCH)
		ku.clear_property(FEED_CHOICES)

	def close(self):
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
			self.session = {'token': token, 'id': stream_id, 'feeds': feeds, 'bases': {value: base_for(value) for value in allowed}, 'accounts': _feed_accounts_fingerprint(), 'active': False, 'switching': False, 'opening_at': now}
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

	def _queue_stop(self):
		try: ku.xbmc.executebuiltin('PlayerControl(Stop)')
		except Exception: return False
		self.stop_pending = True
		self.stop_ack_queued = False
		self._queue_stop_ack()
		return True

	def _queue_stop_ack(self):
		try:
			ku.xbmc.executebuiltin('ClearProperty(%s,Home)' % STOP_REQUEST)
			self.stop_ack_queued = True
		except Exception: pass

	def _cancel_unadopted(self):
		try:
			data = json.loads(ku.get_property(PROPERTY) or '{}')
			token, stream_id, url = data.get('token'), data.get('id'), data.get('playback_url')
			if not isinstance(token, str) or not token or not isinstance(stream_id, str) or not stream_id or not isinstance(url, str) or not self._owns_playback(self._playing_file(), stream_id, (url,)): return
			parsed = urlsplit(url)
			if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.query or parsed.fragment or not re.fullmatch(r'/r/[A-Za-z0-9_-]{32,}/resource(?:\.[A-Za-z0-9]{1,8})?', parsed.path): return
			try: cancel(url)
			except (OSError, HTTPException): pass
			current = json.loads(ku.get_property(PROPERTY) or '{}')
			if current != data or not self._owns_playback(self._playing_file(), stream_id, (url,)): return
			if not self._queue_stop(): return False
			if json.loads(ku.get_property(PROPERTY) or '{}') == data: ku.clear_property(PROPERTY)
			return True
		except (OSError, HTTPException, ValueError, TypeError, AttributeError, KeyError): pass
		return False

	def onAVStarted(self, observed_callback=True):
		if not self.session or not self._prune_expired(): return
		expected = self._url(self.session['id'])
		if expected and self._playing_file() == expected:
			if ku.get_property(OPENING) == expected: ku.clear_property(OPENING)
			self.session['active'] = True
			self.session['switching'] = False

	def onPlayBackError(self):
		if self.session: self.event = ('error', self.session['token'], self.session['id'], self.session['switching'])

	def onPlayBackStopped(self):
		if self.session: self.event = ('stop', self.session['token'], self.session['id'], self.session['switching'])

	onPlayBackEnded = onPlayBackStopped

	def _publish_choices(self):
		session = self.session
		if session and not session['active']:
			newly_loading = not ku.get_property(LOADING_SWITCH)
			ku.set_property(LOADING_SWITCH, 'true')
			if newly_loading and (ku.get_visibility('Window.IsActive(busydialog)') or ku.get_visibility('Window.IsActive(busydialognocancel)')): ku.xbmc.executebuiltin('SetFocus(9900)')
		else: ku.clear_property(LOADING_SWITCH)
		if session and session['active'] and len(session['feeds']) > 1:
			choices = {'token': session['token'], 'current': session['id'], 'feeds': session['feeds']}
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
			self._clear()
			return False
		session.setdefault('urls', {}).pop(selected, None)
		url = self._url(selected)
		if not url:
			ku.dialog.notification('Live TV', 'Live TV VPN unavailable. Check the VPN connection and try again.')
			return False
		if ku.get_property(STOP_REQUEST):
			self._clear()
			return False
		session.update(id=selected, active=False, switching=True, outgoing=outgoing, opening_at=self.clock())
		self.event = None
		self._publish_choices()
		ku.set_property(OPENING, url)
		try:
			item = ku.xbmcgui.ListItem(path=url)
			item.setMimeType('video/mp2t')
			item.setContentLookup(False)
			self.play(url, item)
		except Exception:
			if ku.get_property(OPENING) == url: ku.clear_property(OPENING)
			session.update(id=previous_id, active=self._playing_file() == previous_url, switching=False)
			self._publish_choices()
			ku.dialog.notification('Live TV', 'Could not switch feed.')
			return False
		return True

	def tick(self):
		if self.stop_pending:
			if not ku.get_property(STOP_REQUEST):
				self.stop_pending = False
				self.stop_ack_queued = False
			elif not self.stop_ack_queued: self._queue_stop_ack()
			return False
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
			if url:
				try: cancel(url)
				except (OSError, HTTPException, ValueError, TypeError): pass
			try: current = json.loads(ku.get_property(PROPERTY) or '{}')
			except (ValueError, TypeError): current = {}
			playing = self._playing_file()
			urls = tuple(session.get('urls', {}).values()) + (session.get('outgoing'),)
			stop = isinstance(current, dict) and current.get('token') == session['token'] and self._owns_playback(playing, session['id'], urls)
			queued = self._queue_stop() if stop else False
			self._clear(keep_stop=queued)
			return False
		if not self._prune_expired(): return False
		try: current = json.loads(ku.get_property(PROPERTY) or '{}')
		except (ValueError, TypeError): current = {}
		if not isinstance(current, dict) or current.get('token') != session['token']:
			self._clear()
			return False
		playing, expected = self._playing_file(), self._url(session['id'])
		if not session['active'] and self.clock() - session['opening_at'] >= SESSION_OPEN_TIMEOUT and not (ku.get_visibility('Window.IsVisible(busydialog)') or ku.get_visibility('Window.IsVisible(busydialognocancel)')):
			self._clear()
			return False
		if not expected:
			self._clear()
			return False
		event, self.event = self.event, None
		if event and event[1:3] == (session['token'], session['id']):
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
		if playing == expected and not session['active']:
			try:
				if self.isPlayingVideo(): self.onAVStarted(False)
			except Exception: pass
		self._publish_choices()
		caching = bool(session['active'] and ku.get_visibility('Player.Caching'))
		can_focus = ku.get_visibility('Window.IsActive(fullscreenvideo)') and not ku.get_visibility('Window.IsVisible(videoosd)')
		if caching and not self.caching and can_focus: ku.xbmc.executebuiltin('SetFocus(9902)')
		self.caching = bool(caching and (self.caching or can_focus))
		manual = ku.get_property(SWITCH_REQUEST)
		if manual:
			ku.clear_property(SWITCH_REQUEST)
			try: request = json.loads(manual)
			except (ValueError, TypeError): request = {}
			if isinstance(request, dict) and request.get('token') == session['token'] and request.get('current') == session['id'] and session['active'] and playing == expected:
				return self._switch(request.get('id'))
		return True
