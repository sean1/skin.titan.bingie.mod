import importlib.util
import sys
import types
import unittest
from queue import Empty
from threading import Barrier, Lock, Thread
from pathlib import Path

from tests.module_isolation import load_module, temporary_modules


ROOT = Path(__file__).resolve().parents[1]


class TailRaceQueue:
	def __init__(self, item):
		self.items = [item]
		self.lock = Lock()
		self.call_lock = Lock()
		self.calls = 0
		self.tail_barrier = Barrier(2)

	def empty(self):
		raise AssertionError('workers must not check empty before consuming')

	def get_nowait(self):
		with self.call_lock:
			self.calls += 1
			competing_for_tail = self.calls <= 2
		if competing_for_tail: self.tail_barrier.wait(1)
		with self.lock:
			if not self.items: raise Empty
			return self.items.pop()


def load_worker_modules():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.local_string = str
	kodi_utils.media_path = lambda *parts: '/'.join(parts)
	kodi_utils.get_setting = lambda key, fallback=None: fallback
	kodi_utils.logger = lambda *args: None
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	settings = types.ModuleType('modules.settings')
	settings.paginate = lambda: True
	settings.page_limit = lambda: 100
	settings.nav_jump_use_alphabet = lambda: 0
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils, 'modules.settings': settings}
	with temporary_modules(stubs, isolate=('modules.utils',)):
		utils_path = ROOT / 'resources' / 'lib' / 'modules' / 'utils.py'
		utils_spec = importlib.util.spec_from_file_location('modules.utils', utils_path)
		utils = importlib.util.module_from_spec(utils_spec)
		sys.modules['modules.utils'] = utils
		utils_spec.loader.exec_module(utils)
		list_helper_path = ROOT / 'resources' / 'lib' / 'indexers' / 'list_helper.py'
		list_helper = load_module('test_task_pool_list_helper', list_helper_path)
	return utils, list_helper


class TaskPoolTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.utils, cls.list_helper = load_worker_modules()

	def assert_tail_workers_terminate(self, worker, item):
		queue = TailRaceQueue(item)
		threads = [Thread(target=worker, args=(queue,)) for _ in range(2)]
		for thread in threads: thread.start()
		for thread in threads: thread.join(1)
		self.assertFalse(any(thread.is_alive() for thread in threads))

	def test_task_pool_workers_terminate_when_competing_for_tail_item(self):
		processed = []
		pool = self.utils.TaskPool(maxsize=2)
		self.assert_tail_workers_terminate(lambda queue: pool._thread_target(queue, processed.append), ('item',))
		self.assertEqual(processed, ['item'])

	def test_media_list_workers_terminate_when_competing_for_tail_item(self):
		processed = []
		builder = object.__new__(self.list_helper.BaseMediaListBuilder)
		self.assert_tail_workers_terminate(builder._thread_target, (processed.append, 'item'))
		self.assertEqual(processed, ['item'])


if __name__ == '__main__':
	unittest.main()
