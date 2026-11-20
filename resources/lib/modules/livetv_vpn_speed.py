"""Compare verified Mullvad exits with bounded, uncached Live TV catalog requests."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
import ipaddress
import math
import os
import re
import socket
from statistics import median
import subprocess
from time import monotonic

from modules.livetv_catalog import MAX_ENTRIES

_PING_COUNT = 3
_PING_TIMEOUT = 4
_PING_LIMIT = 8
_TCP_PING_PORT = 5001
_TCP_PING_TIMEOUT = 1
_MAX_WORKERS = 8
_PING_RTT = re.compile(r'\btime[=<]\s*([0-9]+(?:\.[0-9]+)?)\s*ms\b')


def _ping(host):
	"""Return packet count and median latency using flags shared by Android and Linux."""
	try:
		probe = subprocess.run(['ping', '-c', str(_PING_COUNT), host], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=_PING_TIMEOUT, env=dict(os.environ, LC_ALL='C'))
		output = probe.stdout
	except subprocess.TimeoutExpired as error:
		output = error.stdout or b''
	except OSError:
		return None
	samples = [float(sample) for sample in _PING_RTT.findall(output.decode('ascii', errors='ignore'))]
	return (len(samples), median(samples)) if samples else None


def _tcp_ping(host):
	"""Time connections to Mullvad's UDP-over-TCP listener without sending data."""
	samples = []
	for _ in range(_PING_COUNT):
		started = monotonic()
		try:
			with socket.create_connection((host, _TCP_PING_PORT), timeout=_TCP_PING_TIMEOUT):
				samples.append(1000 * (monotonic() - started))
		except OSError: continue
	return (len(samples), median(samples)) if samples else None


def _collect(jobs, work, deadline, cancel):
	pool = ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(jobs)), thread_name_prefix='bingie-vpn-speed')
	pending = {}
	try:
		for key, options in jobs.items():
			if cancel and cancel(): break
			pending[pool.submit(work, *options)] = key
		while pending and not (cancel and cancel()):
			remaining = deadline - monotonic()
			if remaining <= 0: break
			finished, _ = wait(pending, timeout=min(0.2, remaining), return_when=FIRST_COMPLETED)
			for future in finished:
				key = pending.pop(future)
				try: result = future.result()
				except Exception: continue
				if result is not None: yield key, result
	finally:
		pool.shutdown(wait=False, cancel_futures=True)


def shortlist_relays(relays, current_host, *, limit=2, cancel=None):
	"""Shortlist eligible alternatives by response reliability, then median latency."""
	if limit <= 0 or (cancel and cancel()): return []
	eligible = {}
	for relay in relays:
		try: host = str(ipaddress.IPv4Address(relay['host']))
		except (KeyError, TypeError, ValueError): continue
		if host != current_host: eligible.setdefault(host, relay)
	if not eligible: return []
	cities = {}
	host_cities = {}
	for host, relay in eligible.items():
		parts = str(relay.get('hostname', '')).split('-', 2)
		city = parts[1] if len(parts) > 1 else ''
		host_cities[host] = city
		cities.setdefault(city, host)
	hosts = list(dict.fromkeys([*cities.values(), *eligible]))[:_PING_LIMIT]
	jobs = {host: (host,) for host in hosts}
	samples = dict(_collect(jobs, _ping, monotonic() + _PING_TIMEOUT + 1, cancel))
	method = 'ICMP'
	if sum(replies >= 2 for replies, _ in samples.values()) < min(2, len(hosts)) and not (cancel and cancel()):
		tcp = dict(_collect(jobs, _tcp_ping, monotonic() + _PING_COUNT * _TCP_PING_TIMEOUT + 1, cancel))
		if tcp or not samples: method, samples = 'TCP', tcp
	if cancel and cancel(): return []
	order = {host: position for position, host in enumerate(hosts)}

	def preference(host):
		replies, delay = samples.get(host, (0, math.inf))
		return -replies, delay, order[host]

	ranked = sorted(hosts, key=preference)
	if limit >= 2:
		diverse = {}
		for host in ranked:
			city = host_cities[host]
			if city and host in samples and samples[host][0] >= 2: diverse.setdefault(city, host)
		ranked = list(dict.fromkeys([*diverse.values(), *ranked]))
	return [dict(eligible[host], ping_ms=samples[host][1] if host in samples else None, ping_method=method) for host in ranked[:limit]]


def _measure_catalog(account, relay_descriptor_path, deadline):
	from menus import livetv
	from modules import livetv_vpn_relay

	@contextmanager
	def open_catalog(url, headers=None, timeout=20):
		with livetv_vpn_relay.open_url(url, headers=headers, timeout=timeout, descriptor_path=relay_descriptor_path) as response:
			if response.status != 200: raise OSError('Incomplete Live TV catalog')
			yield response
			if response.length not in (None, 0): raise OSError('Incomplete Live TV catalog')

	catalog = livetv._request(account, 'get_live_streams', _deadline=deadline, _vpn_opener=open_catalog)
	if not isinstance(catalog, list) or not catalog or len(catalog) > MAX_ENTRIES: return False
	for stream in catalog:
		if not isinstance(stream, dict): continue
		stream_id, name = stream.get('stream_id'), stream.get('name')
		if type(stream_id) in (int, str) and str(stream_id).isdecimal() and isinstance(name, str) and name.strip(): return True
	return False


def measure_exit(accounts, relay_descriptor_path, *, timeout=35, cancel=None):
	"""Measure full playable catalogs through the supplied relay under one wall deadline."""
	if not relay_descriptor_path or (cancel and cancel()): return {}
	deadline = monotonic() + min(45, max(0, timeout))
	jobs = {identity: (account, relay_descriptor_path, deadline) for identity, account in accounts}
	if not jobs or monotonic() >= deadline: return {}

	def measure(account, descriptor, deadline):
		started = monotonic()
		if started >= deadline or (cancel and cancel()): return None
		if not _measure_catalog(account, descriptor, deadline): return None
		finished = monotonic()
		if finished <= deadline: return finished - started

	results = dict(_collect(jobs, measure, deadline, cancel))
	return {} if cancel and cancel() else results


def is_faster(candidate, current):
	"""Prefer wider coverage, or clearly quicker results for the same providers."""
	if len(candidate) != len(current): return candidate.keys() > current.keys()
	if not current or candidate.keys() != current.keys(): return False
	saved = median(current.values()) - median(candidate.values())
	if saved < max(1, 0.2 * median(current.values())): return False
	return all(candidate[key] <= value + max(1, 0.2 * value) for key, value in current.items())


def best_relay(results, baseline):
	"""Pick the best measured improvement over a shared baseline."""
	eligible = ((relay, timings) for relay, timings in results if is_faster(timings, baseline))
	winner = min(eligible, key=lambda item: (-len(item[1]), median(item[1].values()), max(item[1].values())), default=None)
	return winner[0] if winner is not None else None
