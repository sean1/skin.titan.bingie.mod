"""Identify explicitly labelled sessions within Formula 1 recording packs."""
import re

from modules import livetv_f1_search as f1_search

_EXTRAS = re.compile(r'\b(?:highlights?|recaps?|previews?|interviews?|analysis|review|documentary|drive to survive|onboard|on board|build\s*up)\b|\b(?:pre|post)\s*(?:race|qualifying|quali|qual|practice|sprint|fp\s*[123])\b')
_DIMENSIONS = re.compile(r'\b(?:640|720|1280|1366|1440|1600|1920|2048|2560|3840|4096|7680)\s*x\s*(?:360|480|540|576|720|768|900|1080|1088|1440|2160|4320)\b')


def _round_agrees(text, fixture, year):
	"""Reject conflicting explicit rounds, even when the race name also matches."""
	round_number = fixture.get('race_round')
	# Long episode identifiers such as 2016x109 are not F1 round numbers.
	markers = [(found_year, number) for found_year, number in re.findall(r'(?<!\d)((?:19|20)\d{2})\s*x\s*(\d{1,3})(?!\d)', text) if int(number) <= 99]
	rounds = re.findall(r'\b(?:round|r)\s*(\d{1,3})\b', text)
	if any(found_year != year for found_year, _ in markers): return False
	if not markers and not rounds: return True
	if not isinstance(round_number, int) or isinstance(round_number, bool) or not 1 <= round_number <= 99: return False
	return all(int(number) == round_number for _, number in markers) and all(int(number) == round_number for number in rounds)


def _event_agrees(text, fixture, year):
	aliases = fixture.get('aliases') if isinstance(fixture.get('aliases'), list) else []
	others = fixture.get('other_event_aliases') if isinstance(fixture.get('other_event_aliases'), list) else []
	names, other_names = f1_search._race_names(fixture, aliases), f1_search._other_names(others)
	unique = {name for name in names if len(name) >= 4 and name not in other_names}
	identified = any(f1_search._contains_race(text, name) for name in unique) or f1_search._usa_round(fixture, text, year)
	# A validated official round identifies the weekend when a season pack
	# uses folders such as "Round 06" instead of circuit names.
	round_number = fixture.get('race_round')
	if not identified and isinstance(round_number, int) and not isinstance(round_number, bool) and 1 <= round_number <= 99:
		identified = any(int(number) == round_number for number in re.findall(r'\b(?:round|r)\s*(\d{1,3})\b', text)) and _round_agrees(text, fixture, year)
	if not identified: return False
	return not any(len(name) >= 4 and not any(f1_search._contains(own, name) for own in names) and f1_search._contains_race(text, name) for name in other_names)


def matching_files(files, fixture, pack_type):
	"""Return original file records only when race and session are unambiguous.

	Weekend pack titles have already established the race. Season packs must
	identify the race in each path; every file must identify its own session.
	"""
	if not isinstance(files, list) or not isinstance(fixture, dict) or pack_type not in ('weekend', 'season'): return []
	date, code = fixture.get('date', ''), fixture.get('session_code')
	if not isinstance(date, str) or not re.match(r'^\d{4}-', date) or code not in f1_search._SESSIONS: return []
	year, matched = date[:4], []
	for item in files:
		if not isinstance(item, dict): continue
		path = item.get('full_filename') or item.get('filename')
		if not isinstance(path, str) or not path or len(path) > 2000 or '\x00' in path: continue
		parts = [part for part in re.split(r'[/\\]', path) if part]
		if not parts: continue
		basename = parts[-1]
		text, filename = _DIMENSIONS.sub(' ', f1_search._text(path)), f1_search._text(basename)
		if not text or not filename or f1_search._REJECT.search(filename): continue
		codes = {key for key, pattern in f1_search._SESSIONS.items() if re.search(pattern, filename)}
		if codes != {code}: continue
		conflict = False
		for index, part in enumerate(parts[:-1]):
			folder = f1_search._text(part)
			if _EXTRAS.search(folder):
				conflict = True
				break
			if index == 0 and re.search(r'\b(?:season|weekend|pack|collection)\b', folder): continue
			folder_codes = {key for key, pattern in f1_search._SESSIONS.items() if re.search(pattern, folder)}
			if folder_codes and code not in folder_codes:
				conflict = True
				break
		if conflict: continue
		if any(found != year for found in re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', text)): continue
		if not _round_agrees(text, fixture, year): continue
		if pack_type == 'season' and not _event_agrees(text, fixture, year): continue
		if pack_type == 'weekend':
			# A pack title identifies the weekend, but an explicitly different
			# event inside a file path must not override that identification.
			others = fixture.get('other_event_aliases') if isinstance(fixture.get('other_event_aliases'), list) else []
			aliases = fixture.get('aliases') if isinstance(fixture.get('aliases'), list) else []
			names, other_names = f1_search._race_names(fixture, aliases), f1_search._other_names(others)
			if any(len(name) >= 4 and not any(f1_search._contains(own, name) for own in names) and f1_search._contains_race(text, name) for name in other_names): continue
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
	year, matched = date[:4], []
	aliases = fixture.get('aliases') if isinstance(fixture.get('aliases'), list) else []
	others = fixture.get('other_event_aliases') if isinstance(fixture.get('other_event_aliases'), list) else []
	other_names = f1_search._other_names(others)
	unique_names = {name for name in f1_search._race_names(fixture, aliases) if len(name) >= 4 and name not in other_names}
	for item in files:
		if not isinstance(item, dict): continue
		path = item.get('full_filename') or item.get('filename')
		if not isinstance(path, str) or not path or len(path) > 2000 or '\x00' in path: continue
		parts = [part for part in re.split(r'[/\\]', path) if part]
		if not parts: continue
		text, filename = _DIMENSIONS.sub(' ', f1_search._text(path)), f1_search._text(parts[-1])
		if not text or not filename or _EXTRAS.search(text) or f1_search._REJECT.search(filename): continue
		if re.search(r'\b(?:practice|fp\s*[123]?|qualifying|quali|qual|sprint|shootout|warm\s*up|s?q\s*[123])\b', text): continue
		if not re.search(r'\b(?:grand\s+prix|gp)\b', filename): continue
		if not any(f1_search._contains_race(filename, name) for name in unique_names): continue
		if any(re.search(pattern, filename) for pattern in f1_search._SESSIONS.values()): continue
		years = re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', text)
		if not years or any(found != year for found in years): continue
		if not any(int(number) == round_number for number in re.findall(r'\b(?:round|r)\s*(\d{1,3})\b', text)): continue
		if not _round_agrees(text, fixture, year) or not _event_agrees(text, fixture, year): continue
		conflict = False
		for index, part in enumerate(parts[:-1]):
			folder = f1_search._text(part)
			if index == 0 and re.search(r'\b(?:season|weekend|pack|collection)\b', folder): continue
			codes = {key for key, pattern in f1_search._SESSIONS.items() if re.search(pattern, folder)}
			if codes and codes != {'RACE'}:
				conflict = True
				break
		if not conflict: matched.append({**item, '_f1_unconfirmed': True})
	return matched
