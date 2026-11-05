from windows import BaseDialog
from math import isfinite
from modules import kodi_utils
from modules.kodi_utils import media_path
from modules.settings import get_art_provider
# from modules.kodi_utils import logger

poster_empty = media_path('box_office.png')
click_actions = {10: 'close', 11: 'play', 12: 'cancel'}
confirm_actions = {10: False, 11: True}

class NextEpisode(BaseDialog):
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, args)
		self.closed = False
		self.meta = kwargs.get('meta')
		self.function = kwargs.get('function', 'next_ep')# or 'confirm'
		if self.function == 'next_ep':
			self.actions = click_actions
			self.selected = 'close'
		else:
			self.actions = confirm_actions
			self.selected = False
		self.set_properties()

	def onInit(self):
		self.monitor()

	def run(self):
		self.doModal()
		self.clearProperties()
		return self.selected

	def onClick(self, controlID):
		self.closed = True
		self.selected = self.actions[controlID]
		self.close()

	def onAction(self, action):
		if action in self.closing_actions:
			self.closed = True
			self.close()

	def set_properties(self):
		if self.function == 'confirm': prompt = 'Still watching?'
		elif self.function == 'skip_intro': prompt = 'Skip Intro?'
		else: prompt = 'Up Next in'
		episode = 'S%dE%d' % (self.meta['season'], self.meta['episode'])
		title = '%s [B]|[/B] %s [B]|[/B] %s' % (self.meta['title'], episode, self.meta['ep_name'])
		poster_main, poster_backup, fanart_main, fanart_backup = get_art_provider()
		self.poster = self.meta.get(poster_main) or self.meta.get(poster_backup) or poster_empty
		self.fanart = self.meta.get(fanart_main) or self.meta.get(fanart_backup) or ''
		self.setProperty('tikiskins.poster', self.poster)
		self.setProperty('tikiskins.fanart', self.fanart)
		self.setProperty('tikiskins.title', title)
		self.setProperty('tikiskins.nextep_prompt', prompt)
		self.setProperty('tikiskins.nextep_episode', episode)
		self.setProperty('tikiskins.nextep_function', self.function)

	def monitor(self):
		try: progress_bar = self.getControl(5000)
		except Exception:
			if self.function == 'next_ep' and not self.closed: self.selected = 'cancel'
			self.close()
			return
		if self.function == 'next_ep':
			total_time = total_remaining = None
			for attempt in range(5):
				if self.closed or kodi_utils.monitor.abortRequested(): break
				try:
					total_time = float(self.player.getTotalTime())
					total_remaining = total_time - float(self.player.getTime())
				except Exception:
					if attempt < 4 and not kodi_utils.monitor.waitForAbort(0.2): continue
					break
				break
			if self.closed:
				self.close()
				return
			if kodi_utils.monitor.abortRequested() or total_remaining is None or not isfinite(total_time) or not isfinite(total_remaining) or total_remaining <= 0:
				self.selected = 'cancel'
				self.close()
				return
			failures = 0
			while not self.closed and not kodi_utils.monitor.abortRequested():
				try:
					if not self.player.isPlaying(): break
					remaining = round(total_time - self.player.getTime())
					current_point = (remaining / float(total_remaining)) * 100
					progress_bar.setPercent(current_point)
					failures = 0
				except Exception:
					failures += 1
					if failures >= 5:
						if not self.closed: self.selected = 'cancel'
						break
				if kodi_utils.monitor.waitForAbort(1): break
			if kodi_utils.monitor.abortRequested() and not self.closed: self.selected = 'cancel'
		else:
			for current_point in range(100, 0, -5):
				if self.closed or kodi_utils.monitor.abortRequested(): break
				try:
					progress_bar.setPercent(current_point)
				except Exception: pass
				if kodi_utils.monitor.waitForAbort(1): break
		self.close()

