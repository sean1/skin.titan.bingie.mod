from modules import kodi_utils


ALLOWED_WINDOW_IDS = (10000, 10025, 11110, 11111, 11112, 11118, 11119, 11123)
ALLOWED_WINDOW_VISIBILITY = ' | '.join('Window.IsActive(%s)' % window for window in ('Home', 'Videos', 1110, 1111, 1112, 1118, 1119, 1123, 11123))
PLAYBACK_WINDOW_VISIBILITY = 'Window.IsActive(FullscreenVideo) | Window.IsActive(VideoOSD)'
POV_INFO_PLAY_CONTROL_VISIBILITY = 'Control.HasFocus(51) | Control.HasFocus(80)'


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


def _positive_number(value):
	try: return str(int(value)) if int(value) >= 0 else ''
	except (TypeError, ValueError): return ''


def source_select_focused():
	"""Open manual source selection only from the focused BINGIE info Play button."""
	if not _active_window_allowed(): return False
	if not _pov_info_active() or not kodi_utils.get_visibility(POV_INFO_PLAY_CONTROL_VISIBILITY): return False
	mediatype = kodi_utils.get_property('PovInfoType').strip().lower()
	tmdb_id = _positive_number(kodi_utils.get_property('PovInfoTmdb'))
	if mediatype not in ('movie', 'tvshow'): return False
	if not tmdb_id or tmdb_id == '0': return False
	params = {'mode': 'play_media', 'mediatype': mediatype, 'tmdb_id': tmdb_id, 'autoplay': 'false'}
	if mediatype == 'tvshow':
		from modules.episode_tools import SmartPlay
		SmartPlay(params)
	else:
		from modules.sources import Sources
		Sources.factory(params)
	return True
