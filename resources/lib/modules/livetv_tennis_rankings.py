"""Fetch bounded, cached tennis world rankings for singles and doubles."""
import json
import math
import os
import re
import tempfile
import threading
import time
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

_ATP_DATES_URL = 'https://stats.tennismylife.org/api/ranking/dates'
_ATP_SINGLES_URL = 'https://stats.tennismylife.org/api/ranking?date=%s'
_ATP_DOUBLES_URL = 'https://www.tennisexplorer.com/ranking/atp-men/?page=%d&sort=rank&t=doubles'
_WTA_URL = 'https://api.wtatennis.com/tennis/players/ranked?type=rank%s&metric=%s&pageSize=100&page=%d'
_LIMIT = 4 * 1024 * 1024
_CACHE_SECONDS = 30 * 60
_cache = {}
_failure_until = {}
_locks = {'wta': threading.Lock(), 'atp|singles': threading.Lock(), 'atp|doubles': threading.Lock()}


def name_key(value):
	"""Compare complete names only, normalizing case and Unicode presentation."""
	if not isinstance(value, str) or len(value) > 160 or any(ord(char) < 32 for char in value): return ''
	return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def _fresh(value, days=14):
	if not isinstance(value, str): return False
	try:
		stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
		if stamp.utcoffset() is None: return False
		age = datetime.now(timezone.utc) - stamp
		return -timedelta(days=1) <= age <= timedelta(days=days)
	except (ValueError, OverflowError): return False


def _wta_rows(ranks):
	if not isinstance(ranks, list) or not 20 <= len(ranks) <= 4000: raise ValueError('Incomplete rankings')
	rows = []
	for index, rank in enumerate(ranks):
		if not isinstance(rank, dict): raise ValueError('Invalid doubles row')
		player = rank.get('player')
		if not isinstance(player, dict) or not _fresh(rank.get('rankedAt'), 30) or rank.get('rankedAt') != ranks[0].get('rankedAt'): raise ValueError('Invalid WTA rankings response')
		position, name, points = rank.get('ranking'), player.get('fullName'), rank.get('points')
		if not isinstance(position, int) or isinstance(position, bool): raise ValueError('Invalid rank')
		label = str(position)
		if not name_key(name) or not isinstance(points, int) or isinstance(points, bool) or points < 0 or position < 1 or position > index + 1: raise ValueError('Invalid doubles row')
		if rows and (position not in (rows[-1]['position'], index + 1) or points > rows[-1]['points_value']): raise ValueError('Unordered doubles rankings')
		rows.append({'position': position, 'rank_label': label, 'team': name.strip(), 'points': format(points, ','), 'points_value': points, 'name_key': name_key(name)})
	if rows[0]['position'] != 1: raise ValueError('Missing top rank')
	counts = Counter(row['name_key'] for row in rows)
	for row in rows:
		if counts[row['name_key']] != 1: row['name_key'] = ''
		row.pop('points_value')
	return rows


def _atp_singles(payload, asof, days=14):
	if not isinstance(asof, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', asof) or not _fresh(asof + 'T00:00:00Z', days): raise ValueError('Outdated ATP rankings')
	ranks = payload.get('rankings') if isinstance(payload, dict) else None
	if not isinstance(ranks, list) or not 20 <= len(ranks) <= 4000: raise ValueError('Invalid ATP rankings')
	rows, identities = [], set()
	for index, rank in enumerate(ranks):
		if not isinstance(rank, dict): raise ValueError('Invalid ATP row')
		position, name, points, identity = rank.get('rank'), rank.get('name'), rank.get('points'), rank.get('id')
		if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9]{1,32}', identity) or identity in identities: raise ValueError('Invalid ATP identity')
		if not isinstance(position, int) or isinstance(position, bool) or position < 1 or not name_key(name) or not isinstance(points, int) or isinstance(points, bool) or points < 0: raise ValueError('Invalid ATP row')
		if index == 0 and position != 1: raise ValueError('Missing top rank')
		if rows and (position not in (rows[-1]['position'], index + 1) or points > rows[-1]['points_value']): raise ValueError('Unordered ATP rankings')
		identities.add(identity)
		rows.append({'position': position, 'team': name.strip(), 'points': format(points, ','), 'points_value': points, 'name_key': name_key(name)})
	counts = Counter(row['name_key'] for row in rows)
	for row in rows:
		if counts[row['name_key']] != 1: row['name_key'] = ''
		row.pop('points_value')
	rows[0]['asof'] = asof
	return rows


class _ExplorerParser(HTMLParser):
	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.heading, self.rows, self.next_link = '', [], ''
		self.saw_table, self.closed_table, self.saw_navigation, self.closed_html = False, False, False, False
		self.in_heading = self.in_table = False
		self.row, self.cell = None, ''

	def handle_starttag(self, tag, attrs):
		attrs = dict(attrs)
		if tag == 'h1': self.in_heading = True
		if tag == 'tbody' and 'flags' in attrs.get('class', '').split():
			self.in_table = True
			self.saw_table = True
		elif self.in_table and tag == 'tr': self.row = {}
		elif self.in_table and self.row is not None and tag == 'td':
			classes = attrs.get('class', '').split()
			self.cell = next((value for value in ('rank', 't-name', 'long-point') if value in classes), '')
			if self.cell: self.row[self.cell] = ''
		elif self.in_table and self.row is not None and self.cell == 't-name' and tag == 'a': self.row['href'] = attrs.get('href', '')
		if tag == 'a' and attrs.get('title') == 'Next page': self.next_link = attrs.get('href', '')
		if tag == 'div' and self.closed_table and 'navigator' in attrs.get('class', '').split(): self.saw_navigation = True

	def handle_data(self, data):
		if self.in_heading: self.heading += data
		if self.in_table and self.row is not None and self.cell: self.row[self.cell] += data

	def handle_endtag(self, tag):
		if tag == 'h1': self.in_heading = False
		elif tag == 'td': self.cell = ''
		elif tag == 'tr' and self.in_table and self.row is not None:
			self.rows.append(self.row)
			self.row = None
		elif tag == 'tbody' and self.in_table: self.in_table = False
		elif tag == 'table' and self.saw_table and not self.in_table: self.closed_table = True
		elif tag == 'html': self.closed_html = True


def _explorer_page(body, page):
	parser = _ExplorerParser()
	parser.feed(body.decode('utf-8', 'strict'))
	if not parser.saw_table or not parser.closed_table or not parser.saw_navigation or not parser.closed_html: raise ValueError('Incomplete ATP doubles page')
	match = re.fullmatch(r'\s*WTA and ATP rankings on\s*-\s*(\d{2}\.\d{2}\.\d{4})\s*', parser.heading)
	if not match: raise ValueError('Invalid ATP doubles date')
	try: asof = datetime.strptime(match.group(1), '%d.%m.%Y').date().isoformat()
	except ValueError: raise ValueError('Invalid ATP doubles date')
	if not _fresh(asof + 'T00:00:00Z'): raise ValueError('Outdated ATP doubles rankings')
	if len(parser.rows) > 50: raise ValueError('Invalid ATP doubles page')
	if parser.next_link:
		link = urlparse(parser.next_link)
		query = parse_qs(link.query)
		if link.netloc or link.scheme or link.path != '/ranking/atp-men/' or query.get('t') != ['doubles'] or query.get('page') != [str(page + 1)]: raise ValueError('Invalid ATP doubles next page')
	return asof, parser.rows, bool(parser.next_link)


def _atp_explorer(payload, asof, days=14):
	if not isinstance(asof, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', asof) or not _fresh(asof + 'T00:00:00Z', days): raise ValueError('Outdated ATP doubles rankings')
	ranks = payload.get('rankings') if isinstance(payload, dict) else None
	if not isinstance(ranks, list) or not 20 <= len(ranks) <= 6000: raise ValueError('Invalid ATP doubles rankings')
	rows, identities = [], set()
	for index, rank in enumerate(ranks):
		if not isinstance(rank, dict): raise ValueError('Invalid ATP doubles row')
		position, name, points, href = rank.get('rank'), rank.get('t-name'), rank.get('long-point'), rank.get('href')
		if not isinstance(position, str) or not re.fullmatch(r'[1-9][0-9]{0,3}\.', position.strip()) or not isinstance(points, str) or not re.fullmatch(r'[0-9]+', points.strip()): raise ValueError('Invalid ATP doubles score')
		if not isinstance(href, str) or not re.fullmatch(r'/player/[a-z0-9-]+/\?type=doubles', href) or href in identities or not name_key(name): raise ValueError('Invalid ATP doubles player')
		identities.add(href)
		position, points = int(position.strip()[:-1]), int(points.strip())
		if index == 0 and position != 1: raise ValueError('Missing top ATP doubles rank')
		if rows and (position not in (rows[-1]['position'], index + 1) or points > rows[-1]['points_value']): raise ValueError('Unordered ATP doubles rankings')
		tokens = name_key(name).split()
		keys = [' '.join(tokens[shift:] + tokens[:shift]) for shift in range(len(tokens))] if 2 <= len(tokens) <= 8 else []
		rows.append({'position': position, 'team': name.strip(), 'points': format(points, ','), 'points_value': points, 'name_keys': list(dict.fromkeys(keys))})
	counts = Counter(key for row in rows for key in row['name_keys'])
	for row in rows:
		row['name_keys'] = [key for key in row['name_keys'] if counts[key] == 1]
		row.pop('points_value')
	rows[0]['asof'] = asof
	return rows


def _fetch_explorer():
	ranks, asof = [], ''
	for page in range(1, 121):
		if page > 1: time.sleep(1)
		request = Request(_ATP_DOUBLES_URL % page, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html'})
		with urlopen(request, timeout=10) as response: body = response.read(_LIMIT + 1)
		if len(body) > _LIMIT: raise ValueError('ATP doubles page too large')
		date, page_rows, has_next = _explorer_page(body, page)
		if asof and date != asof: raise ValueError('ATP doubles snapshot changed during fetch')
		asof = date
		if not page_rows: raise ValueError('Empty ATP doubles rankings page')
		if has_next and len(page_rows) != 50: raise ValueError('Incomplete ATP doubles page')
		ranks.extend(page_rows)
		if not has_next:
			if len(page_rows) == 50: raise ValueError('Uncertain ATP doubles completion')
			break
	else: raise ValueError('ATP doubles rankings exceed page limit')
	data = {'rankings': ranks}
	return data, asof, _atp_explorer(data, asof)


def _request(url):
	request = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
	with urlopen(request, timeout=10) as response: data = response.read(_LIMIT + 1)
	if len(data) > _LIMIT: raise ValueError('Rankings response too large')
	return json.loads(data)


def _save_snapshot(path, payload):
	if not path: return
	temporary = None
	try:
		with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=os.path.dirname(path), prefix='.livetv-tennis-ranks-', delete=False) as target:
			temporary = target.name
			json.dump(payload, target, separators=(',', ':'))
		os.replace(temporary, path)
		temporary = None
	except (OSError, ValueError, TypeError): pass
	finally:
		if temporary:
			try: os.unlink(temporary)
			except OSError: pass


def _retry_delay(error, now):
	delay = 3600
	if isinstance(error, HTTPError) and error.code == 429:
		delay = 6 * 3600
		header = error.headers.get('Retry-After', '') if error.headers else ''
		try:
			if header.isdecimal(): delay = max(delay, min(int(header), 7 * 86400))
			elif header: delay = max(delay, min(int(parsedate_to_datetime(header).timestamp() - now), 7 * 86400))
		except (TypeError, ValueError, OverflowError): pass
	return delay


def _provider_retry(cache_dir, provider):
	value = _failure_until.get(provider, 0)
	if not cache_dir: return value
	try:
		with open(os.path.join(cache_dir, 'livetv-tennis-%s-backoff.json' % provider), encoding='utf-8') as source: data = json.loads(source.read(2048))
		retry_at = data.get('retry_at', 0) if isinstance(data, dict) else 0
		if isinstance(retry_at, (int, float)) and not isinstance(retry_at, bool) and math.isfinite(retry_at): value = max(value, min(retry_at, time.time() + 7 * 86400))
	except (OSError, ValueError, TypeError): pass
	return value


def _set_provider_retry(cache_dir, provider, retry_at):
	_failure_until[provider] = retry_at
	if cache_dir: _save_snapshot(os.path.join(cache_dir, 'livetv-tennis-%s-backoff.json' % provider), {'retry_at': retry_at})


def _snapshot_is_fresh(asof, saved, now):
	try:
		asof_time = datetime.fromisoformat(asof.replace('Z', '+00:00')).timestamp()
		saved = float(saved)
		if saved > now + 86400 or saved < 0: return False
		return now < max(asof_time + 7 * 86400 + 12 * 3600, saved + 12 * 3600)
	except (TypeError, ValueError, OverflowError): return False


def _wta_rankings(discipline, cache_dir):
	path = os.path.join(cache_dir, 'livetv-tennis-wta-%s-rankings.json' % discipline) if cache_dir else None
	payload, rows = {}, None
	try:
		if path:
			with open(path, encoding='utf-8') as source: payload = json.loads(source.read(_LIMIT + 1))
		if not isinstance(payload, dict) or payload.get('version') != 1 or payload.get('discipline') != discipline: payload = {}
		if payload.get('complete') is True:
			rows = _wta_rows(payload.get('ranks'))
			asof = payload['ranks'][0]['rankedAt']
			rows[0]['asof'] = asof[:10]
	except (OSError, ValueError, TypeError, KeyError): payload, rows = {}, None
	now = time.time()
	if rows and _snapshot_is_fresh(asof, payload.get('saved'), now): return rows
	retry_at = payload.get('retry_at', 0)
	if not isinstance(retry_at, (int, float)) or isinstance(retry_at, bool) or not math.isfinite(retry_at): retry_at = 0
	if now < max(retry_at, _failure_until.get(discipline, 0), _provider_retry(cache_dir, 'wta')):
		if rows: return rows
		raise ValueError('Tennis rankings refresh delayed')
	try:
		ranks = []
		for page in range(40):
			if page: time.sleep(0.5)
			page_data = _request(_WTA_URL % (discipline.title(), discipline, page))
			if not isinstance(page_data, list) or not page_data or len(page_data) > 100: raise ValueError('Invalid rankings page')
			ranks.extend(page_data)
			if len(page_data) < 100: break
		else: raise ValueError('Tennis rankings exceed page limit')
		fresh_rows = _wta_rows(ranks)
		asof = ranks[0]['rankedAt']
		if not _fresh(asof): raise ValueError('Outdated WTA rankings')
		fresh_rows[0]['asof'] = asof[:10]
		_save_snapshot(path, {'version': 1, 'discipline': discipline, 'complete': True, 'saved': now, 'retry_at': 0, 'ranks': ranks})
		_failure_until.pop(discipline, None)
		return fresh_rows
	except Exception as error:
		retry_at = now + _retry_delay(error, now)
		_failure_until[discipline] = retry_at
		if isinstance(error, HTTPError) and error.code == 429: _set_provider_retry(cache_dir, 'wta', retry_at)
		if not payload: payload = {'version': 1, 'discipline': discipline, 'complete': False}
		payload['retry_at'] = retry_at
		_save_snapshot(path, payload)
		if rows: return rows
		raise


def _atp_rankings(discipline, cache_dir):
	path = os.path.join(cache_dir, 'livetv-tennis-atp-%s-rankings.json' % discipline) if cache_dir else None
	key = 'atp|' + discipline
	payload, rows, asof = {}, None, ''
	try:
		if path:
			with open(path, encoding='utf-8') as source: payload = json.loads(source.read(_LIMIT + 1))
		if not isinstance(payload, dict) or payload.get('version') != 1 or payload.get('discipline') != discipline: payload = {}
		if payload.get('usable') is True:
			asof = payload['asof']
			rows = _atp_singles(payload['data'], asof, 30) if discipline == 'singles' else _atp_explorer(payload['data'], asof, 30)
			rows[0]['asof'] = asof
	except (OSError, ValueError, TypeError, KeyError, IndexError): payload, rows = {}, None
	now = time.time()
	if rows and _snapshot_is_fresh(asof + 'T00:00:00Z', payload.get('saved'), now): return rows
	retry_at = payload.get('retry_at', 0)
	if not isinstance(retry_at, (int, float)) or isinstance(retry_at, bool) or not math.isfinite(retry_at): retry_at = 0
	if now < max(retry_at, _failure_until.get(key, 0)):
		if rows: return rows
		raise ValueError('Tennis rankings refresh delayed')
	try:
		if discipline == 'singles':
			dates = _request(_ATP_DATES_URL)
			if not isinstance(dates, dict) or not isinstance(dates.get('dates'), list) or not 1 <= len(dates['dates']) <= 10000: raise ValueError('Invalid ATP ranking dates')
			valid = [date for date in dates['dates'] if isinstance(date, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', date) and _fresh(date + 'T00:00:00Z')]
			if not valid: raise ValueError('No current ATP rankings')
			asof = max(valid)
			time.sleep(0.5)
			data = _request(_ATP_SINGLES_URL % asof)
			fresh_rows = _atp_singles(data, asof)
		else:
			data, asof, fresh_rows = _fetch_explorer()
		_save_snapshot(path, {'version': 1, 'discipline': discipline, 'usable': True, 'saved': now, 'retry_at': 0, 'asof': asof, 'data': data})
		_failure_until.pop(key, None)
		return fresh_rows
	except Exception as error:
		retry_at = now + _retry_delay(error, now)
		_failure_until[key] = retry_at
		if not payload: payload = {'version': 1, 'discipline': discipline, 'usable': False}
		payload['retry_at'] = retry_at
		_save_snapshot(path, payload)
		if rows: return rows
		raise


def get_rankings(tour, discipline='singles', cache_dir=None):
	"""Return verified rankings, cached separately per tour and discipline."""
	if tour not in ('atp', 'wta') or discipline not in ('singles', 'doubles'): raise ValueError('Unknown tennis ranking')
	key = (tour, discipline)
	with _locks['wta' if tour == 'wta' else 'atp|' + discipline]:
		now = time.monotonic()
		cached = _cache.get(key)
		if cached and 0 <= now - cached[0] < _CACHE_SECONDS: return list(cached[1])
		rows = _wta_rankings(discipline, cache_dir) if tour == 'wta' else _atp_rankings(discipline, cache_dir)
		_cache[key] = (time.monotonic(), rows)
		return list(rows)
