from threading import Thread
from queue import SimpleQueue
from modules import kodi_utils
from modules.utils import LIST_WORKERS, paginate_list, TaskPool
from modules.settings import paginate, page_limit, nav_jump_use_alphabet
# logger = kodi_utils.logger

ls, media_path = kodi_utils.local_string, kodi_utils.media_path
item_jump = kodi_utils.media_path('item_jump.png')
nextpage_str, jump2_str = ls(32799), ls(32964)

class BaseList:
	def __init__(self, params):
		self.params = params
		self.lists = []
		self.category_name = params.get('name')
		self.sort_method = None

	def fetch_results(self):
		raise NotImplementedError

	def process_results(self):
		raise NotImplementedError

	def parse_item(self, item):
		return item, 'user_lists'

	def get_display_and_plot(self, item, name, item_count, user):
		if item_count: display = '[B]%s[/B] | [I](x%s) - %s[/I]' % (name, str(item_count), user)
		else: display = '[B]%s[/B] | [I]%s[/I]' % (name, user)
		plot = '[B]Likes[/B]: %s' % item.get('likes')
		return display, plot

	def add_next_page(self):
		pass

	def build(self):
		self.handle = int(kodi_utils.argv1())
		self.fetch_results()
		kodi_utils.add_items(self.handle, list(self.process_results()))
		self.add_next_page()
		kodi_utils.set_category(self.handle, self.category_name)
		if self.sort_method: kodi_utils.set_sort_method(self.handle, self.sort_method)
		kodi_utils.set_content(self.handle, 'files')
		kodi_utils.end_directory(self.handle)
		kodi_utils.set_view_mode('view.main')

class BaseMediaListBuilder:
	mode = None  # Must be overridden by subclass

	def __init__(self, params):
		self.params = params
		self.is_widget = kodi_utils.external_browse()
		self.use_alphabet = nav_jump_use_alphabet() > 0
		self.max_threads = min(int(kodi_utils.get_setting('pov.max_threads', str(LIST_WORKERS))), LIST_WORKERS)
		self.page = int(params.get('new_page', '1'))
		self.list_id = params.get('list_id')
		self.user = params.get('user')
		self.name = params.get('name')

	def _thread_target(self, q):
		while not q.empty():
			try: target, *args = q.get()
			except: pass
			else: target(*args)

	def fetch_results(self):
		raise NotImplementedError

	def process_media_types(self, queue, process_list):
		raise NotImplementedError

	def get_url_params(self):
		return {'user': self.user, 'name': self.name, 'list_id': self.list_id}

	def build(self):
		self.handle = int(kodi_utils.argv1())
		queue = SimpleQueue()
		results = self.fetch_results()
		if paginate() and results: process_list, total_pages = paginate_list(results, self.page, page_limit())
		else: process_list, total_pages = results, 1
		media_groups = self.process_media_types(queue, process_list)
		max_threads = min(queue.qsize(), self.max_threads)
		threads = (Thread(target=self._thread_target, args=(queue,)) for _ in range(max_threads))
		threads = list(TaskPool.process(threads))
		[i.join() for i in threads]
		items = [x for i in media_groups.values() for x in i.items]
		items.sort(key=lambda k: int(k[1].getProperty('pov_lite_sort_order')))
		content, _ = max(media_groups.items(), key=lambda k: len(k[1].items))
		url_base = {'mode': self.mode, **self.get_url_params()}
		if total_pages > 2 and not self.is_widget and self.use_alphabet:
			jump_url = {**url_base, 'current_page': self.page, 'total_pages': total_pages,
						'transfer_mode': self.mode, 'mode': 'build_navigate_to_page', 'mediatype': 'Media'}
			kodi_utils.add_dir(self.handle, jump_url, jump2_str, iconImage=item_jump, isFolder=False)
		kodi_utils.add_items(self.handle, items)
		if total_pages > self.page:
			kodi_utils.add_dir(self.handle, {**url_base, 'new_page': self.page + 1}, nextpage_str)
		kodi_utils.set_category(self.handle, self.name)
		kodi_utils.set_content(self.handle, content)
		kodi_utils.end_directory(self.handle, False if self.is_widget else None)
		kodi_utils.set_view_mode('view.%s' % content, content, self.is_widget)
