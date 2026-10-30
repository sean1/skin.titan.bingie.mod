import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_module(profile):
	modules = types.ModuleType('modules')
	modules.kodi_utils = types.SimpleNamespace(profile_path=str(profile), addon_path=str(profile), translate_path=lambda path: path)
	spec = importlib.util.spec_from_file_location('livetv_accounts_test', ROOT / 'resources/lib/modules/livetv_accounts.py')
	module = importlib.util.module_from_spec(spec)
	with patch.dict(sys.modules, {'modules': modules}): spec.loader.exec_module(module)
	return module


class LiveTVAccountsTests(unittest.TestCase):
	def setUp(self):
		(ROOT / 'tmp').mkdir(exist_ok=True)
		self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
		self.addCleanup(self.temp.cleanup)
		self.profile = Path(self.temp.name)
		self.module = load_module(self.profile)
		(self.profile / 'resources/private').mkdir(parents=True)
		self.primary = {'server': 'https://main.example', 'username': 'primary-user', 'password': 'primary-secret'}
		self.account = {'id': 'backup_1', 'server': 'https://backup.example', 'username': 'backup-user', 'password': 'backup-secret'}

	def test_load_preserves_primary_and_valid_account(self):
		(self.profile / 'resources/private/livetv-account.json').write_text(json.dumps({'accounts': [self.account]}))
		self.assertEqual(self.module.load_accounts(self.primary), [('primary', self.primary), ('backup_1', self.account)])

	def test_rejects_disabled_duplicate_reserved_and_invalid_entries(self):
		entries = [dict(self.account, enabled=False), self.account, dict(self.account, username='other'), dict(self.account, id='primary'), dict(self.account, id='bad:id'), dict(self.account, id='missing', password=''), 'invalid']
		(self.profile / 'resources/private/livetv-account.json').write_text(json.dumps({'accounts': entries}))
		self.assertEqual(self.module.load_accounts(self.primary), [('primary', self.primary), ('backup_1', self.account)])

	def test_json_primary_fallback_and_server_validation(self):
		primary = dict(self.account, id='primary', server='  https://backup.example/  ')
		invalid = [dict(self.account, id='bad', server='https://user:pass@host'), dict(self.account, id='bad2', server='ftp://host'), dict(self.account, id='bad3', server='https://host/?secret=x')]
		(self.profile / 'resources/private/livetv-account.json').write_text(json.dumps({'accounts': [self.account, primary] + invalid}))
		result = self.module.load_accounts(None)
		self.assertEqual([identity for identity, _ in result], ['primary', 'backup_1'])
		self.assertEqual(result[0][1]['server'], 'https://backup.example')
		self.assertEqual(self.module.load_accounts(self.primary)[0], ('primary', self.primary))

	def test_absent_malformed_or_invalid_document_preserves_primary(self):
		path = self.profile / 'resources/private/livetv-account.json'
		self.assertEqual(self.module.load_accounts(self.primary), [('primary', self.primary)])
		for content in ('{', '[]', '{"accounts":{}}'):
			path.write_text(content)
			self.assertEqual(self.module.load_accounts(self.primary), [('primary', self.primary)])
		self.assertEqual(self.module.load_accounts(None), [])


if __name__ == '__main__': unittest.main()
