from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from indexers.tmdb_api import base_url, get_tmdb, cache_object, EXPIRES_1_WEEK
from modules import kodi_utils


def episode_credits(tmdb_id, season, episode):
	url = '%s/tv/%s/season/%s/episode/%s/credits' % (base_url, tmdb_id, season, episode)
	key = 'tmdb_episode_credits_%s_%s_%s' % (tmdb_id, season, episode)
	return cache_object(get_tmdb, key, url, expiration=EXPIRES_1_WEEK)


def credited_actor(credits, actor_id):
	if not isinstance(credits, dict) or 'cast' not in credits or 'guest_stars' not in credits: raise ValueError('Episode credits unavailable')
	return any(str(person.get('id')) == str(actor_id) for person in credits['cast'] + credits['guest_stars'])


def filter_actor_episodes(tmdb_id, episodes, actor_id, actor_name, paged=False):
	items = sorted(episodes, key=lambda item: (int(item['season']), int(item['episode'])))
	matches, failed = [], 0
	progress = kodi_utils.progressDialog
	progress.create('Episodes featuring %s' % actor_name, 'Checking episode credits…')
	pool = ThreadPoolExecutor(max_workers=4)
	pending, remaining = {}, iter(items)
	try:
		completed = 0
		while True:
			if progress.iscanceled() or kodi_utils.monitor.abortRequested(): return None
			while len(pending) < 4:
				item = next(remaining, None)
				if item is None: break
				pending[pool.submit(episode_credits, tmdb_id, item['season'], item['episode'])] = item
			if not pending: break
			done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
			for future in done:
				item = pending.pop(future)
				try:
					if credited_actor(future.result(), actor_id): matches.append(item)
				except Exception: failed += 1
				completed += 1
			if done: progress.update(int(100 * completed / len(items)))
	finally:
		for future in pending: future.cancel()
		pool.shutdown(wait=False)
		progress.close()
	if failed: kodi_utils.notification('Could not check %s episodes. Results may be incomplete.' % failed)
	elif not matches: kodi_utils.notification('No credited episodes found for %s%s.' % (actor_name, ' on this page' if paged else ''))
	return sorted(matches, key=lambda item: (int(item['season']), int(item['episode'])))
