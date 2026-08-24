from time import monotonic
from modules import kodi_utils


KEYMAP_DIRECTORY = 'special://profile/keymaps/'
KEYMAP_PATH = KEYMAP_DIRECTORY + 'bingie-lite-longpress-play.xml'
SKIN_ID = 'skin.titan.bingie.lite'
SOURCE_SELECT_ACTION = 'RunPlugin(plugin://skin.titan.bingie.lite/?mode=source_select_focused)'
OWNERSHIP_MARKER = 'BINGIE Lite managed keymap'
RECONCILE_INTERVAL = 60.0
KEYMAP_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<!-- BINGIE Lite managed keymap v4. Changes to this file are replaced while BINGIE is active. -->
<keymap>
	<window1123>
		<keyboard>
			<play_pause mod="longpress">{action}</play_pause>
			<p mod="longpress">{action}</p>
			<return mod="longpress">{action}</return>
			<enter mod="longpress">{action}</enter>
			<numpadenter mod="longpress">{action}</numpadenter>
		</keyboard>
		<remote><play mod="longpress">{action}</play></remote>
	</window1123>
</keymap>
'''.format(action=SOURCE_SELECT_ACTION)


def _read(path):
	if not kodi_utils.path_exists(path): return None
	keymap_file = kodi_utils.open_file(path)
	try: return keymap_file.read()
	finally: keymap_file.close()


def _write(path, contents):
	keymap_file = kodi_utils.open_file(path, 'w')
	try: keymap_file.write(contents)
	finally: keymap_file.close()


def install():
	"""Install BINGIE's window-scoped long-Play keymap, returning whether it changed."""
	if getattr(kodi_utils, 'current_skin', lambda: SKIN_ID)() != SKIN_ID: return False
	if _read(KEYMAP_PATH) == KEYMAP_XML: return False
	if not kodi_utils.path_exists(KEYMAP_DIRECTORY): kodi_utils.make_directorys(KEYMAP_DIRECTORY)
	_write(KEYMAP_PATH, KEYMAP_XML)
	if _read(KEYMAP_PATH) != KEYMAP_XML: raise IOError('BINGIE keymap write could not be verified')
	# Kodi v21 accepts ReloadKeymaps as a built-in, but some input providers do not
	# observe a newly created keymap until Kodi is restarted.
	kodi_utils.execute_builtin('ReloadKeymaps')
	return True


def remove():
	"""Remove only the keymap file managed by BINGIE, returning whether it changed."""
	contents = _read(KEYMAP_PATH)
	if contents is None or OWNERSHIP_MARKER not in contents: return False
	kodi_utils.delete_file(KEYMAP_PATH)
	remaining = _read(KEYMAP_PATH)
	if remaining is not None and OWNERSHIP_MARKER in remaining: raise IOError('BINGIE keymap removal could not be verified')
	kodi_utils.execute_builtin('ReloadKeymaps')
	return True


class Lifecycle:
	"""Synchronize on skin transitions and periodically repair managed-file drift."""
	def __init__(self, clock=None, reconcile_interval=RECONCILE_INTERVAL):
		self.active = None
		self.clock = clock or monotonic
		self.reconcile_interval = reconcile_interval
		self.next_reconcile = 0.0

	def tick(self):
		now = self.clock()
		active = kodi_utils.current_skin() == SKIN_ID
		if active == self.active and now < self.next_reconcile: return False
		changed = install() if active else remove()
		contents = _read(KEYMAP_PATH)
		synchronized = contents == KEYMAP_XML if active else contents is None or OWNERSHIP_MARKER not in contents
		if synchronized:
			self.active = active
			self.next_reconcile = now + self.reconcile_interval
		return changed
