"""
	Fenomscrapers Module
"""

import re
import html
from fenom import log_utils

def log_utils_error(*args):
	return log_utils.error(*args)

def get(title):
	try:
		if not title: return
		title = html.unescape(title).lower()
		title = re.sub(r'\<.*?\>|\[.*?\]|\(.*?\)|\{.*?\}', '', title)
		title = re.sub(r'[^a-z0-9]', '', title)
		return title
	except:
		log_utils_error()
		return title

def geturl(title):
	if not title: return
	try:
		title = title.lower().rstrip()
		try: title = title.translate(None, ':*?"\'\\.<>|&!,')
		except:
			try: title = title.translate(title.maketrans('', '', ':*?"\'\\.<>|&!,'))
			except:
				for c in ':*?"\'\\.<>|&!,': title = title.replace(c, '')
		title = title.replace('/', '-').replace(' ', '-').replace('--', '-').replace('–', '-').replace('!', '')
		return title
	except:
		log_utils_error()
		return title

def normalize(title):
	try:
		import unicodedata
		title = ''.join(c for c in unicodedata.normalize('NFKD', title) if unicodedata.category(c) != 'Mn')
		return str(title)
	except:
		log_utils_error()
		return title

