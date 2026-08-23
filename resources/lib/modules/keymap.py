from modules import kodi_utils


KEYMAP_DIRECTORY = 'special://profile/keymaps/'
KEYMAP_PATH = KEYMAP_DIRECTORY + 'bingie-lite-longpress-play.xml'
SOURCE_SELECT_ACTION = 'RunPlugin(plugin://skin.titan.bingie.lite/?mode=source_select_focused)'
KEYMAP_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<!-- BINGIE Lite managed keymap v3. Changes to this file are replaced at service startup. -->
<keymap>
	<global>
		<keyboard>
			<play_pause mod="longpress">{action}</play_pause>
			<p mod="longpress">{action}</p>
			<return mod="longpress">{action}</return>
			<enter mod="longpress">{action}</enter>
			<numpadenter mod="longpress">{action}</numpadenter>
		</keyboard>
		<remote><play mod="longpress">{action}</play></remote>
	</global>
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
	"""Install BINGIE's isolated long-Play keymap, returning whether it changed."""
	if _read(KEYMAP_PATH) == KEYMAP_XML: return False
	if not kodi_utils.path_exists(KEYMAP_DIRECTORY): kodi_utils.make_directorys(KEYMAP_DIRECTORY)
	_write(KEYMAP_PATH, KEYMAP_XML)
	# Kodi v21 accepts ReloadKeymaps as a built-in, but some input providers do not
	# observe a newly created keymap until Kodi is restarted.
	kodi_utils.execute_builtin('ReloadKeymaps')
	return True
