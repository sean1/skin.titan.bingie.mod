"""Live TV accounts stored in the private add-on account file."""
import json
import os
import re
from urllib.parse import urlsplit

from modules import kodi_utils as ku


_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def accounts_path():
	return os.path.join(ku.translate_path(ku.addon_path), 'resources', 'private', 'livetv-account.json')


def load_accounts(primary):
	"""Return stable opaque identities and accounts, retaining the existing primary."""
	accounts = [('primary', primary)] if primary else []
	seen = {'primary'} if primary else set()
	try:
		with open(accounts_path(), encoding='utf-8') as source:
			data = json.load(source)
		entries = data.get('accounts', []) if isinstance(data, dict) else []
		if not isinstance(entries, list): return accounts
		for entry in entries:
			if not isinstance(entry, dict) or entry.get('enabled', True) is not True: continue
			identity = entry.get('id')
			if not isinstance(identity, str) or not _ID.fullmatch(identity) or identity in seen: continue
			if not all(isinstance(entry.get(key), str) and entry[key].strip() for key in ('server', 'username', 'password')): continue
			server = entry['server'].strip().rstrip('/')
			try:
				parts = urlsplit(server)
				if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment: continue
			except ValueError:
				continue
			seen.add(identity)
			account = dict(entry, server=server)
			if identity == 'primary': accounts.insert(0, (identity, account))
			else: accounts.append((identity, account))
	except (OSError, ValueError, TypeError):
		pass
	return accounts
