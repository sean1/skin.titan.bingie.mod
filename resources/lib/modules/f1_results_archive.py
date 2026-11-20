"""Find published classification links for one historical Formula 1 race."""
import re
from datetime import date
from html.parser import HTMLParser
from http.client import HTTPException
from urllib.request import HTTPRedirectHandler, Request, build_opener

from modules.f1_calendar import _normal, _race_name

_ORIGIN = 'https://www.formula1.com'
_MAX_BYTES = 2 * 1024 * 1024
_MONTHS = ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec')
_SESSIONS = {
	'practice/0': ('WU', 'warm up'),
	'practice/1': ('FP1', 'practice 1'),
	'practice/2': ('FP2', 'practice 2'),
	'practice/3': ('FP3', 'practice 3'),
	'practice/4': ('FP4', 'practice 4'),
	'qualifying': ('QUAL', 'qualifying'),
	'qualifying/0': ('QUAL', 'qualifying'),
	'qualifying/1': ('Q1', 'qualifying 1'),
	'qualifying/2': ('Q2', 'qualifying 2'),
}
_INDY_500 = re.compile(r'\b(?:indianapolis|indy) 500\b')


class _NoRedirects(HTTPRedirectHandler):
	def redirect_request(self, req, fp, code, msg, headers, newurl):
		return None


def _fetch(path):
	url = _ORIGIN + path
	opener = build_opener(_NoRedirects())
	with opener.open(Request(url, headers={'User-Agent': 'BINGIE Live TV', 'Accept': 'text/html'}), timeout=4) as response:
		if response.geturl() != url or response.headers.get_content_type() != 'text/html':
			raise ValueError('Unexpected F1 archive response')
		body = response.read(_MAX_BYTES + 1)
	if len(body) > _MAX_BYTES:
		raise ValueError('F1 archive response too large')
	return body.decode('utf-8')


class _ResultsPage(HTMLParser):
	def __init__(self, html):
		super().__init__()
		self.rows = []
		self.links = []
		self.heading = ''
		self.paragraphs = []
		self._cells = None
		self._cell = None
		self._link = None
		self._heading = None
		self._paragraph = None
		self._hidden = None
		self.feed(html)
		self.close()

	def handle_starttag(self, tag, attrs):
		if self._hidden:
			return
		if tag in ('svg', 'script', 'style', 'template'):
			self._hidden = tag
		elif tag == 'tr':
			self._cells = ([], [])
		elif tag == 'td' and self._cells is not None:
			self._cell = []
		elif tag == 'a':
			href = dict(attrs).get('href')
			if href and href.startswith('/en/results/'):
				self._link = (href, [])
		elif tag == 'h1':
			self._heading = []
		elif tag == 'p' and self.heading and len(self.paragraphs) < 2:
			self._paragraph = []

	def handle_endtag(self, tag):
		if self._hidden:
			if tag == self._hidden:
				self._hidden = None
			return
		if tag == 'td' and self._cell is not None and self._cells is not None:
			self._cells[0].append(''.join(self._cell).strip())
			self._cell = None
		elif tag == 'tr' and self._cells is not None:
			self.rows.append(self._cells)
			self._cells = None
			self._cell = None
		elif tag == 'a' and self._link is not None:
			href, parts = self._link
			link = (href, ''.join(parts).strip())
			self.links.append(link)
			if self._cell is not None and self._cells is not None and not self._cells[0]:
				self._cells[1].append(link)
			self._link = None
		elif tag == 'h1' and self._heading is not None:
			self.heading = ''.join(self._heading).strip()
			self._heading = None
		elif tag == 'p' and self._paragraph is not None:
			self.paragraphs.append(''.join(self._paragraph).strip())
			self._paragraph = None

	def handle_data(self, data):
		if self._hidden:
			return
		for parts in (self._cell, self._link[1] if self._link else None, self._heading, self._paragraph):
			if parts is not None:
				parts.append(data)


def _archive_day(value, year):
	match = re.fullmatch(r'(\d{1,2}) ([A-Za-z]{3})(?: (\d{4}))?', value)
	if not match:
		return None
	day, month, printed_year = match.groups()
	if printed_year is not None and int(printed_year) != year:
		return None
	try:
		return date(year, _MONTHS.index(month.lower()) + 1, int(day))
	except ValueError:
		return None


def _race_names(race):
	names = [race.get(field) for field in ('event', 'event_name', 'race_name', 'name')]
	aliases = []
	for field in ('event_names', 'aliases', 'race_aliases'):
		values = race.get(field)
		if isinstance(values, (list, tuple)):
			aliases.extend(values[:80])
	return {_race_name(value) for value in names + aliases} - {''}


def _place_key(value):
	return re.sub(r' (?:grand prix|gp)$', '', _race_name(value))


def _corroborates(year, slug, title, venue, names):
	headline = ' ' + _race_name(title) + ' '
	is_indy = slug in ('indianapolis', 'indianapolis-500') and 1950 <= year <= 1960
	if is_indy or any(_INDY_500.search(name) for name in names):
		return is_indy and _INDY_500.search(headline) is not None and any(_INDY_500.search(name) for name in names)
	parts = {_place_key(value) for value in venue.split(',')}
	parts.discard('')
	return any(
		_place_key(name) in parts or (name.endswith(' grand prix') and ' ' + name + ' ' in headline)
		for name in names
	)


def _season_race(page, year, race_day, exact):
	route = re.compile(r'/en/results/%d/races/[0-9]{1,6}/[a-z0-9]+(?:-[a-z0-9]+)*/race-result' % year)
	entries = set()
	for cells, links in page.rows:
		if len(cells) < 2:
			continue
		day = _archive_day(cells[1], year)
		if day is None:
			continue
		paths = {href for href, _ in links if route.fullmatch(href)}
		if len(paths) == 1:
			entries.add((paths.pop(), day))
	matches = [entry for entry in entries if abs((entry[1] - race_day).days) <= (0 if exact else 1)]
	exact_matches = [entry for entry in matches if entry[1] == race_day]
	matches = exact_matches or matches
	if len(matches) != 1:
		return None
	path, day = matches[0]
	return (path, day) if not any(other_path == path and other_day != day for other_path, other_day in entries) else None


def race_sessions(year, race):
	"""Return canonical classification URLs, or None if the race cannot be verified."""
	if isinstance(year, bool) or not isinstance(year, int) or not 1950 <= year <= 2100 or not isinstance(race, dict):
		return None
	names = _race_names(race)
	stamp = race.get('calendar_day') or race.get('date')
	if not names or not isinstance(stamp, str):
		return None
	try:
		race_day = date.fromisoformat(stamp[:10])
		if race_day.year != year:
			return None
		index = _ResultsPage(_fetch('/en/results/%d/races' % year))
		selected = _season_race(index, year, race_day, bool(race.get('calendar_day')))
		if selected is None:
			return None
		path, archive_day = selected
		page = _ResultsPage(_fetch(path))
		heading = _normal(page.heading)
		if not heading.endswith(' race result') or len(page.paragraphs) != 2 or _archive_day(page.paragraphs[0], year) != archive_day:
			return None
		if not _corroborates(year, path.split('/')[-2], page.heading, page.paragraphs[1], names):
			return None
		prefix = path.rsplit('/', 1)[0] + '/'
		valid = {prefix + suffix: (code, label) for suffix, (code, label) in _SESSIONS.items()}
		return {valid[href][0]: _ORIGIN + href for href, label in page.links if href in valid and _normal(label) == valid[href][1]}
	except (OSError, HTTPException, ValueError):
		return None
