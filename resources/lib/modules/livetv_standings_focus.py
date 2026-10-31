"""Load the selected soccer league's standings without blocking navigation."""
import os
import re
import json
from datetime import date
from queue import Empty, SimpleQueue
from threading import Thread
from time import monotonic

from modules import kodi_utils as ku
from modules import livetv_sportapi


_PREFIX = 'LiveTV.Standings.'
_MAX_ROWS = 20
_F1_DRIVER_ROWS = 40
_F1_CONSTRUCTOR_ROWS = 20
_SETTLE_SECONDS = 0.4
_RETRY_SECONDS = 60
_REFRESH_SECONDS = 30 * 60
_SOCCER_WINDOW = 'Window.IsActive(Videos) + String.Contains(Container.FolderPath,mode=livetv.soccer)'


def _sports_identity(key):
	parts = key.split('|')
	if len(parts) != 3 or parts[0] not in ('nhl', 'nfl', 'ncaa', 'nba'): return None
	if any(not 2 <= len(team) <= 80 or not re.fullmatch(r"[\w .&'’\-]+", team, re.UNICODE) or not any(char.isalpha() for char in team) for team in parts[1:]): return None
	if parts[1].casefold() == parts[2].casefold(): return None
	return parts


def _tennis_identity(key):
	return key in ('tennis|atp', 'tennis|wta', 'tennis|atp|doubles', 'tennis|wta|doubles')


def _identity_sport(key):
	if key == 'f1': return 'f1'
	if _tennis_identity(key): return 'tennis'
	parts = _sports_identity(key)
	if parts: return {'nhl': 'hockey', 'nfl': 'football', 'ncaa': 'football', 'nba': 'basketball'}[parts[0]]
	return 'soccer' if key.isascii() and key.isdecimal() else ''


class FocusedStandings:
	def __init__(self):
		self.identity = ''
		self.league = ''
		self.changed_at = 0.0
		self.last_attempt = 0.0
		self.published = ''
		self.published_at = 0.0
		self.rows = []
		self.tennis_rows = []
		self.match_key = ''
		self.worker = None
		self.tennis_workers = {}
		self.results = SimpleQueue()
		self.refresh_token = getattr(ku, 'get_property', lambda key: '')(_PREFIX + 'Refresh')
		self.pending_refresh = ''
		self._clear()

	def _focused(self):
		if not ku.get_visibility(_SOCCER_WINDOW): return '', ''
		key = ku.get_infolabel('Container(523).ListItem.Property(LiveTV.StandingsKey)').strip()
		if not (key.isascii() and key.isdecimal() and len(key) <= 12 and int(key) > 0) and not _sports_identity(key) and not _tennis_identity(key) and key != 'f1': return '', ''
		league = ku.get_infolabel('Container(523).ListItem.Property(LiveTV.League)').strip()
		return key, league

	def _clear(self):
		# Clear the key first, so the skin hides a partially updated table.
		ku.clear_property(_PREFIX + 'Key')
		ku.clear_property(_PREFIX + 'MatchKey')
		ku.clear_property(_PREFIX + 'League')
		ku.clear_property(_PREFIX + 'Metric')
		ku.clear_property(_PREFIX + 'Discipline')
		ku.clear_property(_PREFIX + 'AsOf')
		ku.clear_property(_PREFIX + 'GroupCount')
		ku.clear_property(_PREFIX + 'Layout')
		ku.clear_property(_PREFIX + 'AllText')
		ku.clear_property(_PREFIX + 'F1.Overflow')
		ku.clear_property(_PREFIX + 'F1.ExceedsLayout')
		for index in range(1, max(_MAX_ROWS, _F1_DRIVER_ROWS) + 1):
			for field in ('Position', 'Team', 'Played', 'Points', 'Highlight'): ku.clear_property('%sRow%d.%s' % (_PREFIX, index, field))
		for group in range(1, 9):
			ku.clear_property('%sGroup%d.Name' % (_PREFIX, group))
			for row in range(1, max(18, _F1_CONSTRUCTOR_ROWS) + 1):
				for field in ('Position', 'Team', 'Points', 'Highlight'): ku.clear_property('%sGroup%d.Row%d.%s' % (_PREFIX, group, row, field))
		self.published = ''
		self.published_at = 0.0
		self.published_day = None
		self.published_partial = False
		self.rows = []
		self.tennis_rows = []
		self.match_key = ''

	def _remember(self, identity, now, partial=False):
		self.published = identity
		self.published_at = now - _REFRESH_SECONDS + _RETRY_SECONDS if partial else now
		self.published_day = date.today()
		self.published_partial = partial

	def _highlight(self):
		if self.identity == 'f1': return
		if _sports_identity(self.identity): return
		if _tennis_identity(self.identity):
			self._highlight_tennis()
			return
		match_key = ku.get_infolabel('Container(523).ListItem.Property(LiveTV.StandingsMatchKey)').strip()
		if match_key == self.match_key: return
		ku.clear_property(_PREFIX + 'MatchKey')
		for index in range(1, _MAX_ROWS + 1): ku.clear_property('%sRow%d.Highlight' % (_PREFIX, index))
		self.match_key = match_key
		parts = match_key.split(':')
		maximum = 999999999999 if _tennis_identity(self.identity) else 100000000
		if len(parts) != 2 or any(not part.isascii() or not part.isdecimal() or not 0 < int(part) <= maximum for part in parts) or parts[0] == parts[1]: return
		wanted = {int(part) for part in parts}
		ids = [row.get('team_id') for row in self.rows]
		for index, team_id in enumerate(ids, 1):
			if team_id in wanted and ids.count(team_id) == 1: ku.set_property('%sRow%d.Highlight' % (_PREFIX, index), 'true')
		ku.set_property(_PREFIX + 'MatchKey', match_key)

	def _highlight_tennis(self):
		from modules.livetv_tennis_rankings import name_key
		doubles = self.identity.endswith('|doubles')
		match_key = ku.get_infolabel('Container(523).ListItem.Property(LiveTV.Tennis.PlayerNames)').strip()
		if match_key == self.match_key: return
		self.match_key = match_key
		wanted = set()
		if len(match_key) <= 1024:
			try: names = json.loads(match_key)
			except (ValueError, TypeError): names = None
			if isinstance(names, list) and len(names) == (4 if doubles else 2):
				keys = [name_key(name) for name in names]
				if all(keys) and len(set(keys)) == len(keys): wanted = set(keys)
		matched = [row for row in self.tennis_rows if (row.get('name_key') and row['name_key'] in wanted) or any(key in wanted for key in row.get('name_keys', []))]
		outside = [row for row in matched if row not in self.tennis_rows[:_MAX_ROWS]]
		self.rows = list(self.tennis_rows[:_MAX_ROWS])
		for row in outside[:2]:
			replaceable = next((index for index in range(min(_MAX_ROWS, len(self.rows)) - 1, _MAX_ROWS - 3, -1) if self.rows[index] not in matched), None)
			if replaceable is None: break
			self.rows.pop(replaceable)
			self.rows.append(row)
		ku.clear_property(_PREFIX + 'MatchKey')
		for index, row in enumerate(self.rows, 1):
			for field_name in ('position', 'team', 'played', 'points'):
				value = row.get('rank_label', row.get(field_name, '')) if field_name == 'position' else row.get(field_name, '')
				ku.set_property('%sRow%d.%s' % (_PREFIX, index, field_name.title()), str(value))
			ku.clear_property('%sRow%d.Highlight' % (_PREFIX, index))
			if row in matched: ku.set_property('%sRow%d.Highlight' % (_PREFIX, index), 'true')
		if wanted: ku.set_property(_PREFIX + 'MatchKey', match_key)

	def close(self):
		self.identity = ''
		self._clear()

	def pause(self):
		if self.identity or self.published:
			self.identity = ''
			self._clear()

	def _load(self, identity, force=False, generation=None, started_day=None):
		try:
			sport = _sports_identity(identity)
			if _tennis_identity(identity):
				from modules import livetv_tennis_rankings
				rows = livetv_tennis_rankings.get_rankings(identity.split('|')[1], 'doubles' if identity.endswith('|doubles') else 'singles', ku.translate_path(ku.profile_path), **({'force_refresh': True} if force else {}))
				heading, metric = identity.split('|')[1].upper(), 'PTS'
			elif identity == 'f1':
				from modules import livetv_f1
				rows = livetv_f1.get_driver_standings(ku.translate_path(ku.profile_path), **({'force_refresh': True} if force else {}))
				heading, metric = 'F1 DRIVERS', 'PTS'
			elif sport:
				from modules import livetv_american_standings
				if sport[0] in ('nhl', 'nfl'):
					loader = livetv_american_standings.get_nhl_groups if sport[0] == 'nhl' else livetv_american_standings.get_nfl_groups
					rows, metric = loader(*sport[1:], **({'force': True} if force else {}))
					heading = sport[0].upper()
				elif sport[0] == 'nba':
					rows, metric = livetv_american_standings.get_nba_groups(*sport[1:], **({'force': True} if force else {}))
					heading = 'NBA'
				else:
					rows, metric = livetv_american_standings.get_ncaa_groups(*sport[1:], **({'force': True} if force else {}))
					heading = 'College football'
			else:
				profile = ku.translate_path(ku.profile_path)
				bundled = os.path.join(ku.translate_path(ku.addon_path), 'resources', 'private', livetv_sportapi.KEY_FILE)
				rows, _ = livetv_sportapi.get_standings(profile, int(identity), bundled=bundled, **({'force': True} if force else {}))
				heading, metric = '', 'PTS'
			result = (identity, rows, heading, metric)
			self.results.put(result if generation is None else result + (generation, started_day, bool(getattr(rows, 'stale', False))))
		except Exception:
			result = (identity, [], '', '')
			self.results.put(result if generation is None else result + (generation, started_day, False))

	def tick(self):
		now = monotonic()
		token = getattr(ku, 'get_property', lambda key: '')(_PREFIX + 'Refresh')
		if token != self.refresh_token:
			self.refresh_token = token
			self.pending_refresh = token.partition(':')[0]
			self.last_attempt = 0.0
		identity, league = self._focused()
		if identity != self.identity:
			self.identity, self.league = identity, league
			self.changed_at, self.last_attempt = now, 0.0
			self._clear()
		elif identity:
			self.league = league
		while True:
			try: result = self.results.get_nowait()
			except Empty: break
			completed, rows, sports_heading, metric = result[:4]
			if len(result) >= 6 and (result[4] != self.refresh_token or result[5] != date.today()): continue
			stale = len(result) >= 7 and result[6]
			if completed != self.identity or self._focused()[0] != completed: continue
			if completed.startswith('nba|'):
				if not isinstance(rows, list) or len(rows) != 2:
					self._clear()
					continue
				ordered = {}
				for group in rows:
					if not isinstance(group, tuple) or len(group) != 2 or not isinstance(group[0], str) or not isinstance(group[1], list): break
					name, items = group
					match = re.fullmatch(r'(Eastern|Western) Conference(?: \(unavailable\))?', name)
					if not match or match.group(1) in ordered or (not items and not name.endswith(' (unavailable)')) or len(items) > 15: break
					if any(not isinstance(row, dict) or not isinstance(row.get('position'), int) or isinstance(row.get('position'), bool) or not isinstance(row.get('team'), str) or not row['team'] or not isinstance(row.get('points'), str) for row in items): break
					ordered[match.group(1)] = group
				if set(ordered) != {'Eastern', 'Western'}:
					self._clear()
					continue
				self._clear()
				ku.set_property(_PREFIX + 'League', sports_heading)
				ku.set_property(_PREFIX + 'Metric', metric)
				ku.set_property(_PREFIX + 'Layout', 'nba')
				for group_index, side in enumerate(('Eastern', 'Western'), 1):
					name, items = ordered[side]
					ku.set_property('%sGroup%d.Name' % (_PREFIX, group_index), name)
					for index, row in enumerate(items, 1):
						for field in ('position', 'team', 'points'): ku.set_property('%sGroup%d.Row%d.%s' % (_PREFIX, group_index, index, field.title()), str(row[field]))
						if row.get('highlight') is True: ku.set_property('%sGroup%d.Row%d.Highlight' % (_PREFIX, group_index, index), 'true')
				ku.set_property(_PREFIX + 'MatchKey', completed)
				self._remember(completed, now, partial=any(not group[1] for group in rows))
				ku.set_property(_PREFIX + 'Key', completed)
				continue
			if completed.startswith('ncaa|'):
				valid = isinstance(rows, list) and rows and all(isinstance(group, tuple) and len(group) == 2 and isinstance(group[0], str) and group[0] and
					isinstance(group[1], list) and (group[1] or group[0].endswith(' (unavailable)')) and all(isinstance(row, dict) and isinstance(row.get('position'), int) and not isinstance(row.get('position'), bool) and
					isinstance(row.get('team'), str) and row.get('team') and isinstance(row.get('points'), str) for row in group[1]) for group in rows)
				if not valid:
					self._clear()
					continue
				selected = [(name, items) for name, items in rows if any(row.get('highlight') is True for row in items)]
				highlighted = [row for _, items in selected for row in items if row.get('highlight') is True]
				if len(highlighted) == 2 and len({' '.join(row['team'].casefold().split()) for row in highlighted}) == 2 and len(selected) in (1, 2) and all(len(items) <= 18 for _, items in selected):
					if len(selected) == 1:
						name, items = selected[0]
						middle = (len(items) + 1) // 2
						columns = [(name, items[:middle]), (name + ' (continued)', items[middle:])]
					else: columns = selected
					if all(len(items) <= 18 for _, items in columns):
						self._clear()
						ku.set_property(_PREFIX + 'League', sports_heading)
						ku.set_property(_PREFIX + 'Metric', metric)
						ku.set_property(_PREFIX + 'Layout', 'college')
						for group_index, (name, items) in enumerate(columns, 1):
							ku.set_property('%sGroup%d.Name' % (_PREFIX, group_index), name)
							for index, row in enumerate(items, 1):
								for field in ('position', 'team', 'points'): ku.set_property('%sGroup%d.Row%d.%s' % (_PREFIX, group_index, index, field.title()), str(row[field]))
								if row.get('highlight') is True: ku.set_property('%sGroup%d.Row%d.Highlight' % (_PREFIX, group_index, index), 'true')
						ku.set_property(_PREFIX + 'MatchKey', completed)
						self._remember(completed, now, partial=any(not group[1] for group in rows))
						ku.set_property(_PREFIX + 'Key', completed)
						continue
				def clean(value): return re.sub(r'[\x00-\x1f\x7f\[\]]', '', str(value)).strip()
				lines = []
				for name, items in rows:
					if lines: lines.append('')
					lines.append('[B]' + clean(name) + ' · ' + clean(metric) + '[/B]')
					for row in items:
						line = '%s. %s  —  %s' % (row['position'], clean(row['team']), clean(row['points']))
						lines.append('[B]' + line + '[/B]' if row.get('highlight') is True else line)
				self._clear()
				ku.set_property(_PREFIX + 'League', sports_heading)
				ku.set_property(_PREFIX + 'Metric', metric)
				ku.set_property(_PREFIX + 'Layout', 'ncaa')
				ku.set_property(_PREFIX + 'AllText', '\n'.join(lines))
				ku.set_property(_PREFIX + 'MatchKey', completed)
				self._remember(completed, now, partial=any(not group[1] for group in rows))
				ku.set_property(_PREFIX + 'Key', completed)
				continue
			if completed.startswith(('nhl|', 'nfl|')):
				expected_groups, expected_rows = (4, 8) if completed.startswith('nhl|') else (8, 4)
				valid = isinstance(rows, list) and len(rows) == expected_groups and all(isinstance(group, tuple) and len(group) == 2 and isinstance(group[0], str) and group[0] and
					isinstance(group[1], list) and (len(group[1]) == expected_rows or (not group[1] and group[0].endswith(' (unavailable)'))) and all(isinstance(row, dict) and isinstance(row.get('position'), int) and not isinstance(row.get('position'), bool) and
					isinstance(row.get('team'), str) and row.get('team') and isinstance(row.get('points'), str) for row in group[1]) and
					[row['position'] for row in group[1]] == list(range(1, len(group[1]) + 1)) for group in rows)
				if not valid:
					self._clear()
					continue
				self._clear()
				ku.set_property(_PREFIX + 'League', sports_heading)
				ku.set_property(_PREFIX + 'Metric', metric)
				ku.set_property(_PREFIX + 'GroupCount', str(expected_groups))
				ku.set_property(_PREFIX + 'Layout', 'hockey' if expected_groups == 4 else 'nfl')
				for group_index, (name, items) in enumerate(rows, 1):
					ku.set_property('%sGroup%d.Name' % (_PREFIX, group_index), name)
					for index, row in enumerate(items, 1):
						for field in ('position', 'team', 'points'): ku.set_property('%sGroup%d.Row%d.%s' % (_PREFIX, group_index, index, field.title()), str(row[field]))
						if row.get('highlight') is True: ku.set_property('%sGroup%d.Row%d.Highlight' % (_PREFIX, group_index, index), 'true')
				ku.set_property(_PREFIX + 'MatchKey', completed)
				self._remember(completed, now, partial=any(not group[1] for group in rows))
				ku.set_property(_PREFIX + 'Key', completed)
				continue
			if completed == 'f1':
				constructors = getattr(rows, 'constructors', None)
				def valid_f1(items):
					return isinstance(items, list) and all(isinstance(row, dict) and isinstance(row.get('position'), int) and not isinstance(row.get('position'), bool) and row['position'] > 0 and isinstance(row.get('team'), str) and row['team'] and isinstance(row.get('points'), str) for row in items) and len({row['position'] for row in items}) == len(items)
				if not valid_f1(rows) or not rows or not valid_f1(constructors) or (not constructors and not stale):
					self._clear()
					continue
				rows = sorted(rows, key=lambda row: row['position'])
				constructors = sorted(constructors, key=lambda row: row['position'])
				self._clear()
				ku.set_property(_PREFIX + 'League', sports_heading)
				ku.set_property(_PREFIX + 'Metric', metric)
				ku.set_property(_PREFIX + 'Layout', 'f1')
				if len(rows) > 24 or len(constructors) > 12: ku.set_property(_PREFIX + 'F1.Overflow', 'true')
				if len(rows) > _F1_DRIVER_ROWS or len(constructors) > _F1_CONSTRUCTOR_ROWS: ku.set_property(_PREFIX + 'F1.ExceedsLayout', 'true')
				for index, row in enumerate(rows[:_F1_DRIVER_ROWS], 1):
					for field in ('position', 'team', 'points'): ku.set_property('%sRow%d.%s' % (_PREFIX, index, field.title()), str(row[field]))
				for index, row in enumerate(constructors[:_F1_CONSTRUCTOR_ROWS], 1):
					for field in ('position', 'team', 'points'): ku.set_property('%sGroup1.Row%d.%s' % (_PREFIX, index, field.title()), str(row[field]))
				self._remember(completed, now, partial=stale or not constructors)
				ku.set_property(_PREFIX + 'Key', completed)
				continue
			if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) or not isinstance(row.get('position'), int) or isinstance(row.get('position'), bool) for row in rows):
				self._clear()
				continue
			groups = {str(row.get('group') or '') for row in rows}
			positions = [row.get('position') for row in rows]
			if len(groups) > 1 or (not _tennis_identity(completed) and len(set(positions)) != len(positions)):
				self._clear()
				continue
			rows = sorted(rows, key=lambda row: row['position'])
			self._clear()
			self.rows = rows[:_MAX_ROWS]
			if _tennis_identity(completed): self.tennis_rows = rows
			group = next(iter(groups), '')
			heading = (sports_heading if _sports_identity(completed) or _tennis_identity(completed) or completed == 'f1' else self.league + (' · ' + group if group else '')) + (' · Top 20' if len(rows) > _MAX_ROWS and not _tennis_identity(completed) else '')
			ku.set_property(_PREFIX + 'League', heading)
			ku.set_property(_PREFIX + 'Metric', metric)
			if _tennis_identity(completed):
				ku.set_property(_PREFIX + 'Layout', 'tennis')
				ku.set_property(_PREFIX + 'Discipline', 'DOUBLES' if completed.endswith('|doubles') else 'SINGLES')
				if rows[0].get('asof'): ku.set_property(_PREFIX + 'AsOf', rows[0]['asof'])
			for index, row in enumerate(rows[:_MAX_ROWS], 1):
				for field in ('position', 'team', 'played', 'points'):
					ku.set_property('%sRow%d.%s' % (_PREFIX, index, field.title()), str(row.get('rank_label', row.get(field, '')) if field == 'position' else row.get(field, '')))
				if _sports_identity(completed) and row.get('highlight') is True: ku.set_property('%sRow%d.Highlight' % (_PREFIX, index), 'true')
			if _sports_identity(completed): ku.set_property(_PREFIX + 'MatchKey', completed)
			self._remember(completed, now, partial=stale)
			ku.set_property(_PREFIX + 'Key', completed)
		if self.published == identity and identity: self._highlight()
		if self.worker is not None and not self.worker.is_alive(): self.worker = None
		self.tennis_workers = {key: worker for key, worker in self.tennis_workers.items() if worker.is_alive()}
		force = bool(identity and self.pending_refresh == _identity_sport(identity))
		if not identity or (not force and self.published == identity and self.published_day == date.today() and (not self.published_partial or now - self.published_at < _REFRESH_SECONDS)): return False
		if _tennis_identity(identity):
			worker = self.tennis_workers.get(identity)
			if worker is None and now - self.changed_at >= _SETTLE_SECONDS and (not self.last_attempt or now - self.last_attempt >= _RETRY_SECONDS):
				self.last_attempt = now
				worker = Thread(target=self._load, args=(identity, force, self.refresh_token, date.today()), daemon=True)
				self.tennis_workers[identity] = worker
				if force: self.pending_refresh = ''
				worker.start()
			return worker is not None or now - self.changed_at < _SETTLE_SECONDS
		if self.worker is None and now - self.changed_at >= _SETTLE_SECONDS and (not self.last_attempt or now - self.last_attempt >= _RETRY_SECONDS):
			self.last_attempt = now
			self.worker = Thread(target=self._load, args=(identity, force, self.refresh_token, date.today()), daemon=True)
			if force: self.pending_refresh = ''
			self.worker.start()
		return self.worker is not None or now - self.changed_at < _SETTLE_SECONDS
