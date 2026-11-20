import os
import re

from modules import kodi_utils as ku
from windows import BaseDialog, open_window


_MAX_BYTES = 256 * 1024
_ENTRY_START = re.compile(r'(?m)(?=^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)')


class KodiLogViewer(BaseDialog):
	def __init__(self, *args, heading, text):
		super().__init__(*args)
		self.heading = heading
		self.text = text

	def onInit(self):
		self.getControl(1).setLabel(self.heading)
		self.getControl(5).setText(self.text)

	def onAction(self, action):
		if action in self.closing_actions: self.close()

	def onClick(self, control_id):
		if control_id == 7: self.close()

	def run(self):
		self.doModal()


def show(session=None):
	if session is None:
		choice = ku.dialog.select('Kodi logs', ['Current session', 'Previous session'])
		if choice < 0: return
		session = 'previous' if choice == 1 else 'current'
	previous = session == 'previous'
	filename = 'kodi.old.log' if previous else 'kodi.log'
	heading = 'Previous Kodi log' if previous else 'Current Kodi log'
	try:
		with open(ku.translate_path('special://logpath/' + filename), 'rb') as log:
			end = log.seek(0, os.SEEK_END)
			start = max(0, end - _MAX_BYTES)
			log.seek(start)
			data = log.read(end - start)
	except FileNotFoundError:
		return ku.dialog.ok(heading, 'Kodi has no saved previous log.' if previous else 'The current Kodi log was not found.')
	except OSError:
		return ku.dialog.ok(heading, 'The Kodi log could not be read.')
	if not data:
		return ku.dialog.ok(heading, 'The Kodi log is empty.')
	if start:
		_, _, complete_lines = data.partition(b'\n')
		data = complete_lines or data
	body = data.decode('utf-8-sig', errors='replace')
	entries = _ENTRY_START.split(body)
	if len(entries) > 1:
		preamble, *entries = entries
		body = '\n'.join(entry.rstrip('\n') for entry in reversed(entries))
		if preamble and not start: body += '\n' + preamble
		heading += ' (newest first)'
	if start:
		body = 'Showing the most recent log entries.\n\n' + body
	return open_window(('modules.kodi_log', 'KodiLogViewer'), 'kodi_log.xml', heading=heading, text=body)
