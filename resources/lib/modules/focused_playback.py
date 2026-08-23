from modules import kodi_utils


ALLOWED_WINDOW_IDS = (10000, 10025, 11110, 11111, 11112, 11118, 11119, 11123)
ALLOWED_WINDOW_VISIBILITY = ' | '.join('Window.IsActive(%s)' % window for window in ('Home', 'Videos', 1110, 1111, 1112, 1118, 1119, 1123, 11123))
PLAYBACK_WINDOW_VISIBILITY = 'Window.IsActive(FullscreenVideo) | Window.IsActive(VideoOSD)'


def _current_window_runtime_id():
	try:
		window = kodi_utils.current_window_id()
		return int(window if isinstance(window, int) else window.getId())
	except (AttributeError, TypeError, ValueError): return None


def _active_window_allowed():
	if kodi_utils.get_visibility(PLAYBACK_WINDOW_VISIBILITY): return False
	if _current_window_runtime_id() in ALLOWED_WINDOW_IDS: return True
	return kodi_utils.get_visibility(ALLOWED_WINDOW_VISIBILITY)


def _pov_info_active():
	return _current_window_runtime_id() == 11123 or kodi_utils.get_visibility('Window.IsActive(1123) | Window.IsActive(11123)')


def _focused_label(label):
	return kodi_utils.get_infolabel('Container.ListItem.%s' % label).strip() or kodi_utils.get_infolabel('ListItem.%s' % label).strip()


def _positive_number(value):
	try: return str(int(value)) if int(value) >= 0 else ''
	except (TypeError, ValueError): return ''


def source_select_focused():
	"""Open manual source selection for the focused BINGIE movie, show, or episode."""
	if not _active_window_allowed(): return False
	trusted_item = _focused_label('Property(PovLiteItem)').lower() == 'true' or bool(_focused_label('Property(PovLiteSourceSelect)'))
	if trusted_item:
		mediatype = (_focused_label('DBType') or _focused_label('Property(DBTYPE)') or _focused_label('Property(mediatype)')).lower()
		tmdb_id = _positive_number(_focused_label('UniqueID(tmdb)') or _focused_label('Property(tmdb_id)'))
	elif _pov_info_active():
		mediatype = kodi_utils.get_property('PovInfoType').strip().lower()
		tmdb_id = _positive_number(kodi_utils.get_property('PovInfoTmdb'))
	else: return False
	if mediatype not in ('movie', 'tvshow', 'episode'): return False
	if not tmdb_id or tmdb_id == '0': return False
	params = {'mode': 'play_media', 'mediatype': mediatype, 'tmdb_id': tmdb_id, 'autoplay': 'false'}
	if mediatype == 'episode':
		season = _positive_number(_focused_label('Season') or _focused_label('Property(season)'))
		episode = _positive_number(_focused_label('Episode') or _focused_label('Property(episode)'))
		if not season or not episode or episode == '0': return False
		params.update({'season': season, 'episode': episode})
	if mediatype == 'tvshow':
		from modules.episode_tools import SmartPlay
		SmartPlay(params)
	else:
		from modules.sources import Sources
		Sources.factory(params)
	return True
