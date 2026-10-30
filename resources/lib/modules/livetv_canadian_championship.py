"""Read upcoming Canadian Championship games from Canada Soccer's schedule."""
import io
import json
import os
import re
import tempfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zoneinfo import ZoneInfo


_URL = 'https://cs-data-production.azurewebsites.net/api/teamplayer/GetChampionshipScheduleByYear/?champCode=canchamp&year=%d&lang=en'
_LIVE_URL = 'https://webws.365scores.com/web/games/?langId=1&timezoneName=UTC&userCountryId=-1&appTypeId=5&competitions=6246'
_NS = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
_MAX_SIZE = 2 * 1024 * 1024
_CACHE_SECONDS = 30 * 60


def _parse(body):
	"""Parse the documented schedule columns without inferring live status."""
	try:
		with zipfile.ZipFile(io.BytesIO(body)) as archive:
			def read(name):
				info = archive.getinfo(name)
				if info.file_size > _MAX_SIZE: raise ValueError('Schedule component too large')
				return ElementTree.fromstring(archive.read(info))
			strings = [''.join(node.itertext()) for node in read('xl/sharedStrings.xml').findall('x:si', _NS)]
			sheet = read('xl/worksheets/sheet1.xml')
			rows = sheet.findall('x:sheetData/x:row', _NS)
			if len(rows) > 10000: raise ValueError('Too many schedule rows')
			parsed = []
			for row in rows:
				cells = {}
				for cell in row.findall('x:c', _NS):
					column = re.match(r'^[A-Z]+', cell.get('r', ''))
					value = cell.findtext('x:v', default='', namespaces=_NS)
					if not column: continue
					if cell.get('t') == 's': value = strings[int(value)]
					cells[column.group()] = value
				parsed.append(cells)
			if not parsed or [parsed[0].get(col) for col in ('B', 'E', 'F', 'H')] != ['Date', 'Kick-off Time (EST)', 'Team A', 'Team B']: raise ValueError('Unexpected schedule columns')
			fixtures, seen = [], set()
			for row in parsed[1:]:
				try:
					home, away = row['F'].strip(), row['H'].strip()
					if not home or not away or len(home) > 200 or len(away) > 200 or home.casefold() == away.casefold(): continue
					day, clock = float(row['B']), float(row['E'])
					if not 1 <= day <= 100000 or not 0 <= clock < 1: continue
					local = datetime(1899, 12, 30) + timedelta(days=int(day), seconds=round(clock * 86400))
					start = local.replace(tzinfo=ZoneInfo('America/Toronto')).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
					key = (home.casefold(), away.casefold(), start)
					if key in seen: continue
					seen.add(key)
					fixtures.append({'home': home, 'away': away, 'league': 'Canadian Championship', 'status': 'Scheduled', 'date': start})
				except (KeyError, TypeError, ValueError, OverflowError): continue
			return fixtures
	except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, IndexError, TypeError, ValueError, OverflowError) as error:
		raise ValueError('Invalid Canadian Championship schedule') from error


def _year(year, cache_dir, now):
	path = os.path.join(cache_dir, 'livetv-canadian-championship-%d.json' % year) if cache_dir else None
	if path:
		try:
			if os.path.getsize(path) <= _MAX_SIZE:
				with open(path, encoding='utf-8') as source: cached = json.load(source)
				if 0 <= now - cached['created'] < _CACHE_SECONDS and isinstance(cached['fixtures'], list): return cached['fixtures']
		except (OSError, ValueError, TypeError, KeyError): pass
	request = Request(_URL % year, headers={'User-Agent': 'BINGIE Live TV'})
	with urlopen(request, timeout=10) as response: body = response.read(_MAX_SIZE + 1)
	if len(body) > _MAX_SIZE: raise ValueError('Schedule too large')
	fixtures = _parse(body)
	if path:
		temporary = None
		try:
			with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=cache_dir, prefix='.canchamp-', delete=False) as output:
				temporary = output.name
				json.dump({'created': now, 'fixtures': fixtures}, output)
			os.replace(temporary, path)
			temporary = None
		except OSError: pass
		finally:
			if temporary:
				try: os.unlink(temporary)
				except OSError: pass
	return fixtures


def get_schedule(now=None, cache_dir=None):
	"""Return only scheduled games kicking off in the next 24 hours."""
	now = time.time() if now is None else now
	start = datetime.fromtimestamp(now, timezone.utc)
	zone = ZoneInfo('America/Toronto')
	years = {start.astimezone(zone).year, (start + timedelta(days=1)).astimezone(zone).year}
	result = []
	for year in sorted(years):
		for fixture in _year(year, cache_dir, now):
			try:
				seconds = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00')).timestamp() - now
				if 0 < seconds <= 86400: result.append(fixture)
			except (KeyError, AttributeError, TypeError, ValueError, OverflowError, OSError): continue
	return result


def get_live(now=None, cache_dir=None):
	"""Return games explicitly reported in progress by 365Scores."""
	now = time.time() if now is None else now
	path = os.path.join(cache_dir, 'livetv-canadian-championship-live.json') if cache_dir else None
	if path:
		try:
			if os.path.getsize(path) <= _MAX_SIZE:
				with open(path, encoding='utf-8') as source: cached = json.load(source)
				if 0 <= now - cached['created'] < 30 and isinstance(cached['fixtures'], list): return cached['fixtures']
		except (OSError, ValueError, TypeError, KeyError): pass
	request = Request(_LIVE_URL, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.365scores.com/', 'Accept': 'application/json'})
	with urlopen(request, timeout=10) as response: body = response.read(_MAX_SIZE + 1)
	if len(body) > _MAX_SIZE: raise ValueError('Live fixture response too large')
	try:
		data = json.loads(body)
		games = data.get('games', [])
		competitions = data.get('competitions', [])
		if not isinstance(data.get('summary'), dict) or not isinstance(games, list) or len(games) > 1000 or not isinstance(competitions, list): raise ValueError('Invalid live fixtures')
		if competitions and not any(isinstance(item, dict) and item.get('id') == 6246 and item.get('name', '').casefold() == 'canadian championship' for item in competitions): raise ValueError('Unexpected competition')
		fixtures = []
		for game in games:
			if not isinstance(game, dict) or game.get('competitionId') != 6246 or game.get('statusGroup') != 3: continue
			try:
				home, away = (game.get(side + 'Competitor', {}).get('name') for side in ('home', 'away'))
				date = game.get('startTime')
				if not all(isinstance(value, str) and 0 < len(value) <= 200 for value in (home, away, date)) or home.casefold() == away.casefold(): continue
				kickoff = datetime.fromisoformat(date.replace('Z', '+00:00'))
				if kickoff.utcoffset() is None or not -8 * 3600 <= kickoff.timestamp() - now <= 300: continue
				fixtures.append({'home': home, 'away': away, 'league': 'Canadian Championship', 'status': 'LIVE', 'date': kickoff.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')})
			except (AttributeError, TypeError, ValueError, OverflowError, OSError): continue
	except (AttributeError, KeyError, TypeError, ValueError, OverflowError, OSError) as error:
		raise ValueError('Invalid Canadian Championship live fixtures') from error
	if path:
		temporary = None
		try:
			with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=cache_dir, prefix='.canchamp-live-', delete=False) as output:
				temporary = output.name
				json.dump({'created': now, 'fixtures': fixtures}, output)
			os.replace(temporary, path)
			temporary = None
		except OSError: pass
		finally:
			if temporary:
				try: os.unlink(temporary)
				except OSError: pass
	return fixtures
