"""Match discovered Formula 1 torrents to enabled debrid services."""

from concurrent.futures import ThreadPoolExecutor
from threading import Thread

from modules import debrid, kodi_utils, settings


def enabled_providers():
	return debrid.debrid_type_enabled('torrent', debrid.debrid_enabled())


def cached_candidates(candidates):
	"""Return provider-specific candidates, preserving unknown availability."""
	candidates = [item for item in candidates if item.get('hash') and item.get('magnet')]
	providers = enabled_providers()
	hashes = list(dict.fromkeys(item['hash'] for item in candidates))

	def check(provider):
		# AllDebrid's availability check uploads magnets, so leave availability
		# unknown until playback is selected. Real-Debrid has no usable hash check.
		if provider in ('realdebrid', 'alldebrid'): return {}
		api = debrid.import_debrid(provider)
		results = {}
		for start in range(0, len(hashes), 25):
			try: results.update(api.check_cache(hashes[start:start + 25]) or {})
			except Exception: pass
		return results

	if not providers or not candidates: return []
	with ThreadPoolExecutor(max_workers=len(providers)) as executor:
		checks = list(executor.map(check, providers))
	results = []
	for provider, checked in zip(providers, checks):
		for candidate in candidates:
			status = checked.get(candidate['hash'])
			if status is False: continue
			results.append({**candidate, 'debrid': provider, 'availability': 'cached' if status is True else 'unknown'})
	return results


def prepare(candidate):
	"""Retrieve playable files; the caller must resolve or discard this result."""
	api = debrid.import_debrid(candidate['debrid'])
	if not api: return []
	if candidate['debrid'] == 'realdebrid': return _prepare_real_debrid(api, candidate)
	return api.parse_magnet_pack(candidate['magnet'], candidate['hash']) or []


def _prepare_real_debrid(api, candidate):
	# Snapshot every existing transfer before adding anything. Never select or delete an
	# existing transfer; the API may return one when a magnet is already present.
	from modules.source_utils import supported_video_extensions
	existing = []
	for page in range(1, 101):
		batch = api._get('torrents?limit=100&page=%d' % page)
		if not isinstance(batch, list): return []
		existing.extend(batch)
		if len(batch) < 100: break
	else: return []
	ids = {str(item.get('id')) for item in existing if item.get('id') is not None}
	match = next((item for item in existing if str(item.get('hash', '')).lower() == candidate['hash'].lower()), None)
	created = None
	try:
		if match:
			transfer_id = match['id']
		else:
			added = api.add_magnet(candidate['magnet']) or {}
			transfer_id = added.get('id')
			if not transfer_id: return []
			if str(transfer_id) not in ids:
				created = transfer_id
				api.add_torrent_select(transfer_id, 'all')
		info = api.torrent_info(transfer_id) or {}
		if created and not info.get('ended'):
			for delay in (500, 1000):
				kodi_utils.sleep(delay)
				info = api.torrent_info(transfer_id) or {}
				if info.get('ended'): break
		if not info.get('ended'): return []
		selected = (item for item in info.get('files', []) if item.get('selected'))
		extensions = tuple(supported_video_extensions())
		files = [{'filename': item['path'], 'size': item.get('bytes', 0), 'link': link, 'torrent_id': created or ''}
			for item, link in zip(selected, info.get('links', [])) if item.get('path', '').lower().endswith(extensions)]
		if files: created = None
		return files
	finally:
		if created: api.delete_torrent(created)


def discard(candidate, files):
	"""Remove only transfers created for this selection, according to the setting."""
	if settings.store_resolved_torrent_to_cloud(candidate['debrid']): return
	transfer_id = next((item.get('torrent_id') for item in files if item.get('torrent_id')), None)
	if transfer_id:
		api = debrid.import_debrid(candidate['debrid'])
		if api: Thread(target=api.delete_torrent, args=(transfer_id,)).start()


def resolve(candidate, files, selected):
	"""Unlock the chosen file and clean up a temporary transfer."""
	try:
		api = debrid.import_debrid(candidate['debrid'])
		return api.unrestrict_link(selected['link']) if api else None
	finally:
		discard(candidate, files)
