"""Identify explicitly labelled sessions within Formula 1 recording packs."""
import re

from modules import livetv_f1_search as f1_search

_DIMENSIONS = re.compile(r'\b(?:640|720|1280|1366|1440|1600|1920|2048|2560|3840|4096|7680)\s*x\s*(?:360|480|540|576|720|768|900|1080|1088|1440|2160|4320)\b')
_F1 = re.compile(r'\b(?:f\s*1|formula\s*(?:1|one))\b(?!\s*academy\b)')
_SKY_PRACTICE = re.compile(r'\b(?:grand prix|gp)\s+practice\s+sky sports f\s*1\b')
_QUALIFYING_DAYS = {'Q1', 'Q2', 'Q3'}
_QUALIFYING_PHASES = {'SQ1', 'SQ2', 'SQ3'}
_SATURDAY_PRACTICE_SLOTS = {'FP_SATURDAY_FIRST', 'FP_SATURDAY_SECOND'}


def _round_agrees(text, fixture, year, round_numbers, *, event_named=False):
	"""Reject conflicting explicit rounds, even when the race name also matches."""
	return not f1_search._conflicting_year(text, year) and f1_search._rounds_agree(round_numbers, fixture, require_known=True, event_named=event_named)


def _folder_rounds(folders, year):
	pattern = re.compile(year + r'\s*x\s*(\d{1,3})')
	return [int(match[1]) for folder in folders if (match := pattern.fullmatch(f1_search._text(folder))) and int(match[1]) <= 99]


def _path_rounds(text, folder_rounds):
	return (f1_search._explicit_rounds(text) + folder_rounds) or f1_search._round_numbers(text)


def _folder_conflicts(folders, filename, fixture, *, season, require_exact_session=False):
	code = fixture['session_code']
	for index, part in enumerate(folders):
		folder = f1_search._text(part)
		session_text = f1_search._session_text(part)
		if f1_search._recording_rejected(session_text, f1_search._PACK_EXTRAS, code=code, season=season): return True
		collection = index == 0 and re.search(r'\b(?:season|weekend|pack|collection)\b', folder)
		if f1_search._SUPPORT_SERIES.search(folder):
			if not collection or not _F1.search(folder): return True
			if not _F1.search(filename) and not any(_F1.search(f1_search._text(part)) for part in folders[1:]): return True
		codes = f1_search._session_codes(session_text, season=season)
		if code == 'TT' and 'TT' in codes and not codes <= {'TT', 'QUAL'}:
			if not f1_search._session_matches(codes, fixture): return True
			continue
		if 'PQ' in codes or codes.intersection(f1_search._QUALIFYING_WEEKDAYS.keys() | f1_search._PRACTICE_2002_SLOTS | f1_search._PRACTICE_2003_PATTERNS.keys()):
			if not f1_search._session_matches(codes, fixture): return True
			continue
		numbered = codes.intersection(_QUALIFYING_DAYS | _QUALIFYING_PHASES | {'QUALIFYING_PAIR'})
		if code in ('QUAL', 'SS') and numbered: return True
		if code in _QUALIFYING_DAYS and numbered.intersection(_QUALIFYING_PHASES): return True
		if code in _QUALIFYING_DAYS and 'Q3' in numbered and not fixture.get('qualifying_practice'): return True
		if code in _QUALIFYING_DAYS and re.fullmatch(f1_search._QUALIFYING, folder): continue
		if collection and not numbered: continue
		if code in ('FP1', 'FP2', 'FP3', 'FP4') and codes == {'FREE_PRACTICE'}: continue
		if codes.intersection({'FAM', 'FREE_PRACTICE', 'QUALIFYING_PAIR'}) or len(codes.intersection(_QUALIFYING_DAYS)) > 1:
			if not f1_search._session_matches(codes, fixture): return True
			continue
		if codes and (code not in codes or (require_exact_session and codes != {code})): return True
	return False


def _file_session_matches(codes, folders, fixture, year):
	if f1_search._session_matches(codes, fixture): return True
	code = fixture['session_code']
	if year != '2003' or code not in ('FP2', 'FP3') or codes not in ({code}, {'FREE_PRACTICE'}): return False
	for part in folders:
		folder_codes = f1_search._session_codes(f1_search._session_text(part), season=year)
		if len(folder_codes) == 1 and folder_codes & _SATURDAY_PRACTICE_SLOTS and f1_search._session_matches(folder_codes, fixture): return True
	return False


def matching_files(files, fixture, pack_type):
	"""Return original file records only when race and session are unambiguous.

	Weekend pack titles have already established the race. Season packs must
	identify the race in each path. A 2003 Saturday practice file may identify
	its practice slot through an explicit parent folder.
	"""
	if not isinstance(files, list) or not isinstance(fixture, dict) or pack_type not in ('weekend', 'season'): return []
	date, code = fixture.get('date', ''), fixture.get('session_code')
	if not isinstance(date, str) or not re.match(r'^\d{4}-', date) or code not in f1_search._SESSIONS: return []
	year, matched = f1_search._fixture_year(fixture), []
	if not f1_search._fixture_session_allowed(fixture): return []
	for item in files:
		if not isinstance(item, dict): continue
		path = item.get('full_filename') or item.get('filename')
		if not isinstance(path, str) or not path or len(path) > 2000 or '\x00' in path: continue
		parts = [part for part in re.split(r'[/\\]', path) if part]
		if not parts: continue
		basename = parts[-1]
		text, filename = _DIMENSIONS.sub(' ', f1_search._text(path)), f1_search._text(basename)
		session_text = f1_search._session_text(basename)
		if not text or not filename or f1_search._recording_rejected(session_text, f1_search._FILE_REJECT, code=code, season=year): continue
		if f1_search._indy_race_date_conflicts(session_text, fixture): continue
		codes = f1_search._session_codes(session_text, season=year)
		if not codes and fixture.get('single_practice') is True and _SKY_PRACTICE.search(session_text) and f1_search._explicit_rounds(session_text) and f1_search.event_name_matches(session_text, fixture):
			codes = {'FREE_PRACTICE'}
		if not _file_session_matches(codes, parts[:-1], fixture, year): continue
		if _folder_conflicts(parts[:-1], filename, fixture, season=year): continue
		folder_rounds = _folder_rounds(parts[:-1], year)
		event_named = pack_type == 'weekend' or (bool(fixture.get('round_aliases')) and f1_search.event_name_matches(text, fixture))
		round_text = text
		if (episode := f1_search._F1TV_EPISODE.fullmatch(filename)) and text.endswith(filename) and f1_search.event_name_matches(filename, fixture):
			round_text = text[:-len(filename)] + episode[1]
		if not _round_agrees(text, fixture, year, _path_rounds(round_text, folder_rounds), event_named=event_named): continue
		if pack_type == 'season':
			if not f1_search._event_matches(text, fixture, folder_rounds=folder_rounds, require_name=year != date[:4]): continue
		elif f1_search._event_conflicts(text, fixture): continue
		matched.append(item)
	return matched


def unconfirmed_race_files(files, fixture, pack_type):
	"""Offer an explicitly chosen, unlabelled GP video as an unconfirmed race.

	Only a season collection with the correct event and verified official round
	qualifies. Its filename must name a Grand Prix and contain no session label.
	"""
	if not isinstance(files, list) or not isinstance(fixture, dict) or pack_type != 'season' or fixture.get('session_code') != 'RACE': return []
	date, round_number = fixture.get('date', ''), fixture.get('race_round')
	if not isinstance(date, str) or not re.match(r'^\d{4}-', date) or not isinstance(round_number, int) or isinstance(round_number, bool) or not 1 <= round_number <= 99: return []
	year, matched = f1_search._fixture_year(fixture), []
	aliases = fixture.get('aliases') if isinstance(fixture.get('aliases'), list) else []
	others = fixture.get('other_event_aliases') if isinstance(fixture.get('other_event_aliases'), list) else []
	other_names = f1_search._other_names(others)
	unique_names = {name for name in f1_search._race_names(fixture, aliases) if f1_search._valid_alias(name) and name not in other_names}
	for item in files:
		if not isinstance(item, dict): continue
		path = item.get('full_filename') or item.get('filename')
		if not isinstance(path, str) or not path or len(path) > 2000 or '\x00' in path: continue
		parts = [part for part in re.split(r'[/\\]', path) if part]
		if not parts: continue
		text, filename = _DIMENSIONS.sub(' ', f1_search._text(path)), f1_search._text(parts[-1])
		if not text or not filename or f1_search._recording_rejected(f1_search._session_text(path), f1_search._PACK_EXTRAS, code='RACE', season=year): continue
		if f1_search._recording_rejected(filename, f1_search._FILE_REJECT, code='RACE', season=year): continue
		if re.search(r'\b(?:practice|fp(?:\s*0?[1-4])?|qualif(?:y(?:ing)?|ication)|quali|qual|sprint|shootout|warm\s*ups?|wu|familiari[sz]ation|s?q\s*[123])\b', text): continue
		if not re.search(r'\b(?:grand\s+prix|gp)\b', filename): continue
		if not any(f1_search._contains_race(filename, name) for name in unique_names): continue
		if f1_search._session_codes(filename, season=year): continue
		years = re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', text)
		if not years or any(found != year for found in years): continue
		folder_rounds = _folder_rounds(parts[:-1], year)
		round_numbers = _path_rounds(text, folder_rounds)
		episode_rounds = re.findall(rf'\bs{year}e(\d{{1,3}})\b', filename)
		if episode_rounds and (len(episode_rounds) != 1 or int(episode_rounds[0]) != round_number): continue
		if not (round_numbers or episode_rounds) or not _round_agrees(text, fixture, year, round_numbers, event_named=True): continue
		if not f1_search._event_matches(text, fixture, folder_rounds=folder_rounds): continue
		if not _folder_conflicts(parts[:-1], filename, fixture, season=year, require_exact_session=True): matched.append({**item, '_f1_unconfirmed': True})
	return matched
