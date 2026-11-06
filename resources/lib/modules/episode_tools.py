from windows import open_window
from indexers.metadata import tvshow_meta, season_episodes_meta
from modules import kodi_utils, settings
from modules.utils import get_next_episode_pointer, adjust_premiered_date, get_datetime
from modules.sources import Sources
# from modules.kodi_utils import logger

ls = kodi_utils.local_string
nextep_str, nores_str = ls(32801), ls(32760)

def SmartPlay(params):
	tmdb_id = params.get('tmdb_id')
	if not tmdb_id: return kodi_utils.notification(32574)
	meta_user_info, current_date = settings.metadata_user_info(), get_datetime()
	try:
		from caches.smartplay_cache import lookup
		season, episode = lookup(tmdb_id) or (1, 0)
	except Exception: season, episode = 1, 0
	meta = tvshow_meta('tmdb_id', tmdb_id, meta_user_info, current_date)
	meta.update({'season': season, 'episode': episode})
	nextep_meta, nextep_params = nextep_playback_info(meta)
	if nextep_params == 'error': return kodi_utils.notification(32574)
	if nextep_params == 'no_next_episode': return kodi_utils.notification('%s %s' % (nextep_str, nores_str))
	if str(params.get('autoplay', 'false')).lower() == 'false':
		nextep_params['autoplay'] = 'false'
		nextep_params.pop('autoplay_next', None)
	Sources.factory(nextep_params)

def nextep_playback_info(meta):
	adjust_hours, current_date = settings.date_offset(), get_datetime()
	meta_get = meta.get
	try:
		sn, en = int(meta_get('season')), int(meta_get('episode'))
		season, episode, new_season = get_next_episode_pointer(meta, sn, en)
		if new_season and season > meta_get('total_seasons'): return meta, 'no_next_episode'
		ep_data = season_episodes_meta(season, meta, settings.metadata_user_info())
		try: ep_data = next((i for i in ep_data if i['episode'] == episode))
		except: return meta, 'no_next_episode'
		episode_date, premiered = adjust_premiered_date(ep_data['premiered'], adjust_hours)
		if current_date < episode_date: return meta, 'no_next_episode'
		custom_title = meta_get('custom_title')
		title = custom_title or meta_get('title')
		display_name = '%s - %dx%.2d' % (title, int(season), int(episode))
		meta.update({
			'mediatype': 'episode', 'rootname': display_name, 'season': season, 'episode': episode,
			'premiered': premiered, 'ep_name': ep_data['title'], 'plot': ep_data['plot']
		})
		url_params = {
			'mode': 'play_media', 'mediatype': 'episode', 'autoplay': 'true', 'autoplay_next': 'true', 'season': season, 'episode': episode,
			'tmdb_id': meta_get('tmdb_id'), 'tvshowtitle': meta_get('rootname')
		}
		if custom_title: url_params['custom_title'] = custom_title
	except: url_params = 'error'
	return meta, url_params

def execute_skip_intro(player, meta, generation=None):
	intro_start, intro_end = player.intro
	try:
		playing_file = player.getPlayingFile() if hasattr(player, 'getPlayingFile') else None
		if hasattr(player, 'getPlayingFile') and not playing_file: return
		if generation is None: generation = getattr(player, 'playback_generation', 0)
	except Exception: return
	while player.isPlayingVideo():
		try:
			if getattr(player, 'playback_event', True) is not True or getattr(player, 'playback_generation', 0) != generation: return
			if playing_file is not None and player.getPlayingFile() != playing_file: return
			current_time = player.getTime()
		except Exception: break
		if current_time > intro_end: break
		if intro_start <= current_time <= intro_end:
			if _continue_action(True, meta, 'skip_intro'):
				try:
					if not player.isPlayingVideo() or getattr(player, 'playback_event', True) is not True: return
					if getattr(player, 'playback_generation', 0) != generation: return
					if playing_file is not None and player.getPlayingFile() != playing_file: return
					if intro_start <= player.getTime() < intro_end: player.seekTime(intro_end)
				except Exception: return
			return
		kodi_utils.sleep(200)

def execute_scrape_nextep(player, meta):
	nextep_meta, nextep_params = nextep_playback_info(meta)
	if nextep_params == 'error': return kodi_utils.notification(32574)
	if nextep_params == 'no_next_episode': return
	if not Sources.background_prep(nextep_params): return kodi_utils.notification('%s %s' % (nextep_str, nores_str))
	Sources.nextep_callback(nextep_params)
	action = _continue_action(True, nextep_meta)
	if action == 'cancel':
		Sources.clear_nextep()
		return kodi_utils.notification(32736)
	if action == 'play': player.request_next_episode()

def execute_nextep(player, meta, nextep_settings):
	nextep_meta, nextep_params = nextep_playback_info(meta)
	if nextep_params == 'error': return kodi_utils.notification(32574)
	if nextep_params == 'no_next_episode': return
	if not Sources.background_prep(nextep_params): return kodi_utils.notification('%s %s' % (nextep_str, nores_str))
	Sources.nextep_callback(nextep_params)
	action = _control_playback(player, nextep_settings, nextep_meta)
	if action == 'cancel':
		Sources.clear_nextep()
		kodi_utils.clear_property('pov_lite_total_autoplays')
		return kodi_utils.notification(32736)
	if action == 'close':
		if nextep_settings['run_popup']: return
		text = '%s %s S%02dE%02d' % (ls(32801), nextep_meta['title'], nextep_meta['season'], nextep_meta['episode'])
		kodi_utils.notification(text, 6500, nextep_meta['poster'])
	if action == 'play': player.request_next_episode()

def _continue_action(run_popup, nextep_meta, function='next_ep'):
	if not run_popup: return 'close'
	return open_window(('windows.episodes', 'NextEpisode'), 'episodes.xml', meta=nextep_meta, function=function)

def _confirm_threshold(nextep_settings, nextep_meta):
	nextep_threshold = nextep_settings['threshold']
	if nextep_threshold == 0: return True
	try: current_number = int(kodi_utils.get_property('pov_lite_total_autoplays'))
	except: current_number = 1
	if current_number < nextep_threshold:
		current_number += 1
		kodi_utils.set_property('pov_lite_total_autoplays', str(current_number))
		return True
	if _continue_action(True, nextep_meta, 'confirm'):
		current_number = 1
		kodi_utils.set_property('pov_lite_total_autoplays', str(current_number))
		return True
	return False

def _control_playback(player, nextep_settings, nextep_meta):
	confirm_threshold = False
	final_action = 'cancel'
	failures = 0
	run_popup = nextep_settings['run_popup']
	display_nextep_popup = nextep_settings['window_time']
	nextep_threshold_check = nextep_settings['threshold_check']
	while not kodi_utils.monitor.abortRequested():
		try:
			if not player.isPlayingVideo(): break
			total_time = player.getTotalTime()
			curr_time = player.getTime()
			remaining_time = round(total_time - curr_time)
			if remaining_time <= nextep_threshold_check:
				if not confirm_threshold:
					confirm_threshold = _confirm_threshold(nextep_settings, nextep_meta)
					if not confirm_threshold:
						final_action = 'cancel'
						break
			if remaining_time <= display_nextep_popup:
				final_action = _continue_action(run_popup, nextep_meta)
				break
			failures = 0
		except Exception:
			failures += 1
			if failures >= 5: break
		if kodi_utils.monitor.waitForAbort(0.2): break
	return final_action
