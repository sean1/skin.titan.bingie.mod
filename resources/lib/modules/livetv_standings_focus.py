"""Load the selected soccer league's standings without blocking navigation."""
import os
from queue import Empty, SimpleQueue
from threading import Thread
from time import monotonic

from modules import kodi_utils as ku
from modules import livetv_sportapi


_PREFIX = 'LiveTV.Standings.'
_MAX_ROWS = 20
_SETTLE_SECONDS = 0.4
_RETRY_SECONDS = 60
_REFRESH_SECONDS = 30 * 60
_SOCCER_WINDOW = 'Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.soccer)'


class FocusedStandings:
	def __init__(self):
		self.identity = ''
		self.league = ''
		self.changed_at = 0.0
		self.last_attempt = 0.0
		self.published = ''
		self.published_at = 0.0
		self.rows = []
		self.match_key = ''
		self.worker = None
		self.results = SimpleQueue()
		self._clear()

	def _focused(self):
		if not ku.get_visibility(_SOCCER_WINDOW): return '', ''
		key = ku.get_infolabel('Container(523).ListItem.Property(LiveTV.StandingsKey)').strip()
		if not key.isascii() or not key.isdecimal() or len(key) > 12 or int(key) <= 0: return '', ''
		league = ku.get_infolabel('Container(523).ListItem.Property(LiveTV.League)').strip()
		return key, league

	def _clear(self):
		# Clear the key first, so the skin hides a partially updated table.
		ku.clear_property(_PREFIX + 'Key')
		ku.clear_property(_PREFIX + 'MatchKey')
		ku.clear_property(_PREFIX + 'League')
		for index in range(1, _MAX_ROWS + 1):
			for field in ('Position', 'Team', 'Played', 'Points', 'Highlight'): ku.clear_property('%sRow%d.%s' % (_PREFIX, index, field))
		self.published = ''
		self.published_at = 0.0
		self.rows = []
		self.match_key = ''

	def _highlight(self):
		match_key = ku.get_infolabel('Container(523).ListItem.Property(LiveTV.StandingsMatchKey)').strip()
		if match_key == self.match_key: return
		ku.clear_property(_PREFIX + 'MatchKey')
		for index in range(1, _MAX_ROWS + 1): ku.clear_property('%sRow%d.Highlight' % (_PREFIX, index))
		self.match_key = match_key
		parts = match_key.split(':')
		if len(parts) != 2 or any(not part.isascii() or not part.isdecimal() or not 0 < int(part) <= 100000000 for part in parts) or parts[0] == parts[1]: return
		wanted = {int(part) for part in parts}
		ids = [row.get('team_id') for row in self.rows]
		for index, team_id in enumerate(ids, 1):
			if team_id in wanted and ids.count(team_id) == 1: ku.set_property('%sRow%d.Highlight' % (_PREFIX, index), 'true')
		ku.set_property(_PREFIX + 'MatchKey', match_key)

	def close(self):
		self.identity = ''
		self._clear()

	def pause(self):
		if self.identity or self.published:
			self.identity = ''
			self._clear()

	def _load(self, identity):
		try:
			profile = ku.translate_path(ku.profile_path)
			bundled = os.path.join(ku.translate_path(ku.addon_path), 'resources', 'private', livetv_sportapi.KEY_FILE)
			rows, _ = livetv_sportapi.get_standings(profile, int(identity), bundled=bundled)
			self.results.put((identity, rows))
		except Exception:
			self.results.put((identity, []))

	def tick(self):
		now = monotonic()
		identity, league = self._focused()
		if identity != self.identity:
			self.identity, self.league = identity, league
			self.changed_at, self.last_attempt = now, 0.0
			self._clear()
		elif identity:
			self.league = league
		while True:
			try: completed, rows = self.results.get_nowait()
			except Empty: break
			if completed != self.identity or self._focused()[0] != completed: continue
			if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) or not isinstance(row.get('position'), int) or isinstance(row.get('position'), bool) for row in rows):
				self._clear()
				continue
			groups = {str(row.get('group') or '') for row in rows}
			positions = [row.get('position') for row in rows]
			if len(groups) > 1 or len(set(positions)) != len(positions):
				self._clear()
				continue
			rows = sorted(rows, key=lambda row: row['position'])
			self._clear()
			self.rows = rows[:_MAX_ROWS]
			group = next(iter(groups), '')
			heading = self.league + (' · ' + group if group else '') + (' · Top 20' if len(rows) > _MAX_ROWS else '')
			ku.set_property(_PREFIX + 'League', heading)
			for index, row in enumerate(rows[:_MAX_ROWS], 1):
				for field in ('position', 'team', 'played', 'points'):
					ku.set_property('%sRow%d.%s' % (_PREFIX, index, field.title()), str(row.get(field, '')))
			self.published = completed
			self.published_at = now
			ku.set_property(_PREFIX + 'Key', completed)
		if self.published == identity and identity: self._highlight()
		if self.worker is not None and not self.worker.is_alive(): self.worker = None
		if not identity or (self.published == identity and now - self.published_at < _REFRESH_SECONDS): return False
		if self.worker is None and now - self.changed_at >= _SETTLE_SECONDS and (not self.last_attempt or now - self.last_attempt >= _RETRY_SECONDS):
			self.last_attempt = now
			self.worker = Thread(target=self._load, args=(identity,), daemon=True)
			self.worker.start()
		return self.worker is not None or now - self.changed_at < _SETTLE_SECONDS
