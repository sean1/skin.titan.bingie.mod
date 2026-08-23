import math
import re
import threading
import time
from datetime import timedelta
from ipaddress import ip_address
from urllib.parse import urlsplit

from caches.main_cache import MainCache


_CACHE_KEY = 'bingie_playback_health_v1'
_VERSION = 1
_EXPIRY = timedelta(days=90)
_HALF_LIFE = 14 * 24 * 60 * 60.0
_MAX_HOSTS = 32
_MIN_ATTEMPTS = 3.0
_KNOWN_PROVIDERS = ('realdebrid', 'alldebrid', 'torbox')
_ALIASES = {
	'realdebrid': 'realdebrid', 'rd': 'realdebrid', 'rdcloud': 'realdebrid',
	'alldebrid': 'alldebrid', 'ad': 'alldebrid',
	'torbox': 'torbox', 'tb': 'torbox'
}
_EVENT_STAGE = {
	'resolve_ok': ('resolve', True), 'resolve_fail': ('resolve', False),
	'startup_ok': ('startup', True), 'startup_fail': ('startup', False),
	'healthy_play': ('stream', True), 'stream_error': ('stream', False)
}
_HOST_RE = re.compile(r'^[a-z0-9.-]+$')
_lock = threading.RLock()


def _provider(value):
	if not isinstance(value, str): return None
	return _ALIASES.get(re.sub(r'[^a-z0-9]', '', value.lower()))


def _hostname(link):
	if not isinstance(link, str) or len(link) > 8192: return None
	try:
		parts = urlsplit(link)
		host = (parts.hostname or '').lower().rstrip('.')
		if parts.scheme not in ('http', 'https') or not host or len(host) > 253 or not _HOST_RE.fullmatch(host): return None
		try:
			ip_address(host)
			return None
		except ValueError: pass
		return host if '.' in host and '..' not in host else None
	except Exception: return None


def source_context(item, link=None):
	"""Return the only source identity fields that may be persisted."""
	if not isinstance(item, dict): return {}
	provider = None
	for key in ('debrid', 'cache_provider', 'provider'):
		provider = _provider(item.get(key))
		if provider: break
	if not provider: return {}
	context = {'provider': provider}
	host = _hostname(link)
	if host: context['host'] = host
	return context


def _empty_state(now):
	return {'version': _VERSION, 'updated': now, 'providers': {}, 'hosts': {}}


def _clean_stage(stage):
	if not isinstance(stage, dict): return None
	try:
		attempts = max(0.0, min(float(stage.get('attempts', 0)), 1000000.0))
		failures = max(0.0, min(float(stage.get('failures', 0)), attempts))
		latency_total = max(0.0, min(float(stage.get('latency_total', 0)), 100000000.0))
		latency_count = max(0.0, min(float(stage.get('latency_count', 0)), attempts))
		return {'attempts': attempts, 'failures': failures, 'latency_total': latency_total, 'latency_count': latency_count}
	except (TypeError, ValueError, OverflowError): return None


def _clean_record(value, now):
	if not isinstance(value, dict): return None
	try: updated = float(value.get('updated', now))
	except (TypeError, ValueError, OverflowError): updated = now
	if not math.isfinite(updated): updated = now
	record = {'updated': min(updated, now), 'stages': {}}
	stages = value.get('stages', {})
	if not isinstance(stages, dict): return record
	for name in ('resolve', 'startup', 'stream'):
		stage = _clean_stage(stages.get(name))
		if stage: record['stages'][name] = stage
	return record


def _load(now):
	try: state = MainCache().get(_CACHE_KEY)
	except Exception: state = None
	if not isinstance(state, dict) or state.get('version') != _VERSION: return _empty_state(now)
	clean = _empty_state(now)
	providers = state.get('providers', {})
	if isinstance(providers, dict):
		for provider in _KNOWN_PROVIDERS:
			record = _clean_record(providers.get(provider), now)
			if record: clean['providers'][provider] = record
	hosts = state.get('hosts', {})
	if isinstance(hosts, dict):
		for host, value in hosts.items():
			if len(clean['hosts']) >= _MAX_HOSTS: break
			if not isinstance(value, dict) or _provider(value.get('provider')) not in _KNOWN_PROVIDERS: continue
			safe_host = _hostname('https://%s' % host)
			record = _clean_record(value, now)
			if safe_host and record:
				record['provider'] = _provider(value['provider'])
				clean['hosts'][safe_host] = record
	return clean


def _decay(record, now):
	age = max(0.0, now - record.get('updated', now))
	# Sub-day decay only introduces floating-point noise between stages of one play.
	# Age the aggregate once it is old enough for recency to be meaningful.
	factor = math.pow(0.5, age / _HALF_LIFE) if age >= 24 * 60 * 60 else 1.0
	if factor < 1.0:
		for stage in record.get('stages', {}).values():
			for key in ('attempts', 'failures', 'latency_total', 'latency_count'): stage[key] *= factor
	record['updated'] = now
	return record


def _save(state):
	try: MainCache().set(_CACHE_KEY, state, _EXPIRY)
	except Exception: pass


def _record_event(target, stage_name, success, latency, now):
	target = _decay(target, now)
	stage = target['stages'].setdefault(stage_name, {'attempts': 0.0, 'failures': 0.0, 'latency_total': 0.0, 'latency_count': 0.0})
	stage['attempts'] += 1.0
	if not success: stage['failures'] += 1.0
	if latency is not None:
		try: latency = float(latency)
		except (TypeError, ValueError, OverflowError): latency = -1
		if math.isfinite(latency) and 0 <= latency <= 600:
			stage['latency_total'] += latency
			stage['latency_count'] += 1.0


def record(context, event, latency=None, elapsed=None, now=None):
	"""Record one stage outcome; identifiers other than provider and hostname are discarded."""
	if event not in _EVENT_STAGE or not isinstance(context, dict): return False
	provider = _provider(context.get('provider'))
	if not provider: return False
	try: now = float(time.time() if now is None else now)
	except (TypeError, ValueError, OverflowError): return False
	if not math.isfinite(now): return False
	stage_name, success = _EVENT_STAGE[event]
	measurement = latency if latency is not None else elapsed if event in ('startup_ok', 'startup_fail') else None
	host = _hostname('https://%s' % context.get('host', ''))
	with _lock:
		state = _load(now)
		target = state['providers'].setdefault(provider, {'updated': now, 'stages': {}})
		_record_event(target, stage_name, success, measurement, now)
		if host:
			host_target = state['hosts'].setdefault(host, {'provider': provider, 'updated': now, 'stages': {}})
			if host_target.get('provider') == provider: _record_event(host_target, stage_name, success, measurement, now)
		if len(state['hosts']) > _MAX_HOSTS:
			oldest = sorted(state['hosts'], key=lambda key: state['hosts'][key].get('updated', 0))[:len(state['hosts']) - _MAX_HOSTS]
			for key in oldest: del state['hosts'][key]
		state['updated'] = now
		_save(state)
	return True


def provider_penalty(provider, now=None):
	"""Return a bounded positive sort penalty after enough stage-specific evidence."""
	if isinstance(provider, dict): provider = provider.get('provider')
	provider = _provider(provider)
	if not provider: return 0.0
	try: now = float(time.time() if now is None else now)
	except (TypeError, ValueError, OverflowError): return 0.0
	if not math.isfinite(now): return 0.0
	with _lock:
		state = _load(now)
		record_data = state['providers'].get(provider)
		if not record_data: return 0.0
		record_data = _decay(record_data, now)
	stages = [value for value in record_data.get('stages', {}).values() if value.get('attempts', 0) >= _MIN_ATTEMPTS]
	if not stages: return 0.0
	weighted_failure, weight = 0.0, 0.0
	for stage in stages:
		stage_weight = min(stage['attempts'], 20.0)
		weighted_failure += (stage['failures'] / stage['attempts']) * stage_weight
		weight += stage_weight
	penalty = 80.0 * weighted_failure / weight if weight else 0.0
	startup = record_data.get('stages', {}).get('startup')
	if startup and startup.get('attempts', 0) >= _MIN_ATTEMPTS and startup.get('latency_count', 0) > 0:
		average_latency = startup['latency_total'] / startup['latency_count']
		penalty += min(20.0, max(0.0, average_latency - 2.0))
	return round(min(100.0, max(0.0, penalty)), 3)
