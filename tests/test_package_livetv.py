import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PackageLiveTVTests(unittest.TestCase):
	def setUp(self):
		(ROOT / 'tmp').mkdir(exist_ok=True)
		self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
		self.addCleanup(self.temp.cleanup)
		self.project = Path(self.temp.name) / 'project'
		(self.project / 'scripts').mkdir(parents=True)
		(self.project / 'resources/private').mkdir(parents=True)
		shutil.copy(ROOT / 'scripts/package.sh', self.project / 'scripts/package.sh')
		(self.project / 'addon.xml').write_text('<addon id="skin.example" version="1.0.0" />\n', encoding='utf-8')
		(self.project / '.gitignore').write_text('livetv-account.json\nresources/private/subtitle_providers.json\n', encoding='utf-8')
		subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
		subprocess.run(['git', '-C', str(self.project), 'add', 'addon.xml', '.gitignore'], check=True)
		self.account = self.project / 'resources/private/livetv-account.json'
		self.output = self.project / 'out'

	def package(self):
		env = {**os.environ, 'TMPDIR': self.temp.name}
		return subprocess.run(['sh', str(self.project / 'scripts/package.sh'), str(self.output)], capture_output=True, text=True, env=env)


	def test_symlink_account_is_rejected(self):
		self.account.symlink_to(self.project / 'addon.xml')
		result = self.package()
		self.assertNotEqual(result.returncode, 0)
		self.assertIn('not a symlink', result.stderr)

	def test_bad_permissions_and_invalid_json_are_rejected(self):
		self.account.write_text('{}', encoding='utf-8')
		self.account.chmod(0o644)
		result = self.package()
		self.assertNotEqual(result.returncode, 0)
		self.assertIn('0600', result.stderr)
		self.account.chmod(0o600)
		self.account.write_text('not json', encoding='utf-8')
		result = self.package()
		self.assertNotEqual(result.returncode, 0)
		self.assertFalse((self.output / 'skin.example-1.0.0.zip').exists())


if __name__ == '__main__': unittest.main()
