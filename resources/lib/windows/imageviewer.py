import json
from windows import BaseDialog
from menus.people import person_data_dialog
from modules.kodi_utils import media_path, show_busy_dialog, hide_busy_dialog, local_string as ls
# from modules.kodi_utils import logger

fanart = BaseDialog.fanart
next_icon = media_path('item_next.png')
nextpage_str = ls(32799)

class ThumbImageViewer(BaseDialog):
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, args)
		self.window_id = 2000
		self.current_page = 1
		self.selected = None
		self.list_items = kwargs.get('list_items')
		self.next_page_params = kwargs.get('next_page_params')
		self.ImagesInstance = kwargs.get('ImagesInstance')

	def onInit(self):
		self.make_page()

	def run(self):
		self.doModal()
		self.clearProperties()

	def onAction(self, action):
		if action in self.closing_actions:
			return self.previous_page()
		try:
			position = self.get_position(self.window_id)
			chosen_listitem = self.get_listitem(self.window_id)
		except: return
		if action in self.selection_actions:
			if chosen_listitem.getProperty('tikiskins.next_page_item') == 'true': self.new_page()
			else:
				thumb_params = chosen_listitem.getProperty('tikiskins.action')
				thumb_params = json.loads(thumb_params)
				if thumb_params['mode'] == 'slideshow_image':
					thumb_params['current_index'] = position
					ending_position = self.ImagesInstance.run(thumb_params)
					self.win.selectItem(ending_position)
				elif thumb_params['mode'] in ('person_data_dialog', 'show_person_info'):
					person_data_dialog(thumb_params)

	def make_page(self):
		try:
			self.set_properties()
			if self.next_page_params.get('page_no', 'final_page') != 'final_page': self.make_next_page()
			self.win = self.getControl(self.window_id)
			self.win.addItems(self.list_items)
			self.setFocusId(self.window_id)
		except: pass

	def new_page(self):
		try:
			show_busy_dialog()
			self.win.reset()
			self.current_page += 1
			self.next_page_params['in_progress'] = 'true'
			self.list_items, self.next_page_params = self.ImagesInstance.run(self.next_page_params)
			hide_busy_dialog()
			self.make_page()
		except: self.close()

	def previous_page(self):
		try:
			self.current_page -= 1
			if self.current_page < 1: return self.close()
			self.win.reset()
			self.next_page_params['page_no'] = self.current_page
			self.next_page_params['in_progress'] = 'true'
			self.list_items, self.next_page_params = self.ImagesInstance.run(self.next_page_params)
			self.make_page()
		except: self.close()

	def make_next_page(self):
		try:
			listitem = self.make_listitem()
			listitem.setProperty('tikiskins.name', nextpage_str)
			listitem.setProperty('tikiskins.thumb', next_icon)
			listitem.setProperty('tikiskins.next_page_item', 'true')
			self.list_items.append(listitem)
		except: pass

	def set_properties(self):
		self.setProperty('tikiskins.page_no', str(self.current_page))
		self.setProperty('tikiskins.thumbviewer.fanart', fanart)

class SlideShow(BaseDialog):
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, args)
		self.window_id = 5000
		self.all_images = kwargs.get('all_images')
		self.index = kwargs.get('index')
		self.set_properties()
		self.make_items()

	def onInit(self):
		self.win = self.getControl(self.window_id)
		self.win.addItems(self.item_list)
		self.win.selectItem(self.index)
		self.setFocusId(self.window_id)

	def run(self):
		self.doModal()
		return self.position

	def onAction(self, action):
		if action in self.closing_actions:
			self.position = self.get_position(self.window_id)
			self.close()

	def make_items(self):
		def builder():
			for item in self.all_images:
				try:
					listitem = self.make_listitem()
					listitem.setProperty('tikiskins.slideshow.image', item[0])
					listitem.setProperty('tikiskins.slideshow.title', item[1])
					yield listitem
				except: pass
		self.item_list = list(builder())

	def set_properties(self):
		self.setProperty('tikiskins.slideshow.fanart', fanart)
