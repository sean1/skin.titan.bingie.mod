from concurrent.futures import ThreadPoolExecutor
from indexers.tmdb_api import base_url, get_tmdb, cache_object, EXPIRES_1_WEEK
from modules import kodi_utils


def episode_credits(tmdb_id, season, episode):
	url = '%s/tv/%s/season/%s/episode/%s/credits' % (base_url, tmdb_id, season, episode)
	key = 'tmdb_episode_credits_%s_%s_%s' % (tmdb_id, season, episode)
	return cache_object(get_tmdb, key, url, expiration=EXPIRES_1_WEEK)


def credited_actor(credits, actor_id):
	if not isinstance(credits, dict) or 'cast' not in credits or 'guest_stars' not in credits: raise ValueError('Episode credits unavailable')
	return any(str(person.get('id')) == str(actor_id) for person in credits['cast'] + credits['guest_stars'])


def filter_actor_episodes(tmdb_id, episodes, actor_id, actor_name):
	items = sorted(episodes, key=lambda item: (int(item['season']), int(item['episode'])))
	matches, failed = [], 0
	progress = kodi_utils.progressDialog
	progress.create('Episodes featuring %s' % actor_name, 'Checking episode credits…')
	try:
		with ThreadPoolExecutor(max_workers=4) as pool:
			for start in range(0, len(items), 4):
				if progress.iscanceled() or kodi_utils.monitor.abortRequested(): return []
				batch = items[start:start + 4]
				futures = [pool.submit(episode_credits, tmdb_id, item['season'], item['episode']) for item in batch]
				for item, future in zip(batch, futures):
					try:
						if credited_actor(future.result(), actor_id): matches.append(item)
					except Exception: failed += 1
				progress.update(int(100 * (start + len(batch)) / len(items)))
				if progress.iscanceled() or kodi_utils.monitor.abortRequested(): return []
	finally: progress.close()
	if failed: kodi_utils.notification('Could not check %s episodes. Results may be incomplete.' % failed)
	elif not matches: kodi_utils.notification('No credited episodes found for %s.' % actor_name)
	return matches
