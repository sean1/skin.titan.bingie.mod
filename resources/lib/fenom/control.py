"""
	Fenomscrapers Module
"""

import os.path
import xbmcaddon
import xbmcgui
import xbmcvfs

addon = xbmcaddon.Addon
addonObject = addon()
addonInfo = addonObject.getAddonInfo
getLangString = addonObject.getLocalizedString

dialog = xbmcgui.Dialog()
homeWindow = xbmcgui.Window(10000)

existsPath = xbmcvfs.exists
makeFile = xbmcvfs.mkdir
transPath = xbmcvfs.translatePath
joinPath = os.path.join

try: dataPath = transPath(addonInfo('profile')).decode('utf-8')
except: dataPath = transPath(addonInfo('profile'))
cacheFile = joinPath(dataPath, 'fenomcache.db')
def lang(language_id):
	return getLangString(language_id)

def addonName():
	return addonInfo('name')

def addonVersion():
	return addonInfo('version')

def addonIcon():
	return addonInfo('icon')

def addonPath():
	try: return transPath(addonInfo('path').decode('utf-8'))
	except: return transPath(addonInfo('path'))

def yesnoDialog(line, heading=addonInfo('name'), nolabel='', yeslabel=''):
	return dialog.yesno(heading, line, nolabel, yeslabel)

def selectDialog(list, heading=addonInfo('name')):
	return dialog.select(heading, list)

def notification(title=None, message=None, icon=None, time=3000, sound=False):
	if title == 'default' or title is None: title = addonName()
	if isinstance(title, int): heading = lang(title)
	else: heading = str(title)
	if isinstance(message, int): body = lang(message)
	else: body = str(message)
	if icon is None or icon == '' or icon == 'default': icon = addonIcon()
	elif icon == 'INFO': icon = xbmcgui.NOTIFICATION_INFO
	elif icon == 'WARNING': icon = xbmcgui.NOTIFICATION_WARNING
	elif icon == 'ERROR': icon = xbmcgui.NOTIFICATION_ERROR
	dialog.notification(heading, body, icon, time, sound=sound)
