"""
	Fenomscrapers Module
"""

import gzip
from random import choice
from http import cookiejar
from io import BytesIO
import urllib.request as urllib2
from urllib.parse import urlencode
from urllib.response import addinfourl
from urllib.error import HTTPError
from fenom import cache
from fenom.dom_parser import parseDOM


def request(url, close=True, redirect=True, error=False, proxy=None, post=None, headers=None, mobile=False, XHR=False, limit=None,
					referer=None, cookie=None, compression=True, output='', timeout='30', verifySsl=True, flare=True, ignoreErrors=None, as_bytes=False):
	try:
		if not url: return None
		if url.startswith('//'): url = 'http:' + url

		if isinstance(post, dict):
			post = bytes(urlencode(post), encoding='utf-8')
		elif isinstance(post, str):
			post = bytes(post, encoding='utf-8')

		handlers = []
		if proxy is not None:
			handlers += [urllib2.ProxyHandler({'http':'%s' % (proxy)}), urllib2.HTTPHandler]
			opener = urllib2.build_opener(*handlers)
			urllib2.install_opener(opener)

		if output == 'cookie' or output == 'extended' or close is not True:
			cookies = cookiejar.LWPCookieJar()
			handlers += [urllib2.HTTPHandler(), urllib2.HTTPSHandler(), urllib2.HTTPCookieProcessor(cookies)]
			opener = urllib2.build_opener(*handlers)
			urllib2.install_opener(opener)

		if not verifySsl:
			try:
				import ssl
				ssl_context = ssl._create_unverified_context()
				handlers += [urllib2.HTTPSHandler(context=ssl_context)]
				opener = urllib2.build_opener(*handlers)
				urllib2.install_opener(opener)
			except:
				from fenom import log_utils
				log_utils.error()

		try: headers.update(headers)
		except: headers = {}
		if 'User-Agent' in headers: pass
		elif mobile is not True: headers['User-Agent'] = cache.get(randomagent, 12)
		else: headers['User-Agent'] = 'Apple-iPhone/701.341'
		if 'Referer' in headers: pass
		elif referer is not None: headers['Referer'] = referer
		if 'Accept-Language' not in headers:
			headers['Accept-Language'] = 'en-US'
		if 'X-Requested-With' in headers: pass
		elif XHR: headers['X-Requested-With'] = 'XMLHttpRequest'
		if 'Cookie' in headers: pass
		elif cookie: headers['Cookie'] = cookie
		if 'Accept-Encoding' in headers: pass
		elif compression and limit is None: headers['Accept-Encoding'] = 'gzip'

		if redirect is False:
			class NoRedirectHandler(urllib2.HTTPRedirectHandler):
				def http_error_302(self, reqst, fp, code, msg, head):
					infourl = addinfourl(fp, head, reqst.get_full_url())
					infourl.status = code
					infourl.code = code
					return infourl
				http_error_300 = http_error_302
				http_error_301 = http_error_302
				http_error_303 = http_error_302
				http_error_307 = http_error_302
			opener = urllib2.build_opener(NoRedirectHandler())
			urllib2.install_opener(opener)
			try: del headers['Referer']
			except: pass

		req = urllib2.Request(url, data=post)
		_add_request_header(req, headers)
		try:
			response = urllib2.urlopen(req, timeout=int(timeout))
		except HTTPError as error_response:
			response = error_response
			try: ignore = ignoreErrors and (int(response.code) == ignoreErrors or int(response.code) in ignoreErrors)
			except: ignore = False

			if not ignore:
				if error is False:
					from fenom import log_utils
					log_utils.error('Request-Error url=(%s)' % url)
					return None
				if error is True and response.code in (401, 404, 405):
					try: response_headers = dict([(item[0].title(), item[1]) for item in list(response.info().items())])
					except:
						from fenom import log_utils
						log_utils.error()
						response_headers = response.headers
					return (str(response), str(response.code), response_headers)

		if output == 'cookie':
			try: result = '; '.join(['%s=%s' % (i.name, i.value) for i in cookies])
			except: pass
			if close is True: response.close()
			return result
		elif output == 'geturl':
			result = response.geturl()
			if close is True: response.close()
			return result
		elif output == 'headers':
			result = response.headers
			if close is True: response.close()
			return result
		elif output == 'chunk':
			try: content = int(response.headers['Content-Length'])
			except: content = (2049 * 1024)
			if content < (2048 * 1024): return
			try: result = response.read(16 * 1024)
			except: result = response # testing
			if close is True: response.close()
			return result
		elif output == 'file_size':
			try: content = int(response.headers['Content-Length'])
			except: content = '0'
			if close is True: response.close()
			return content
		if limit == '0': result = response.read(224 * 1024)
		elif limit is not None: result = response.read(int(limit) * 1024)
		else: result = response.read(5242880)

		try: encoding = response.headers["Content-Encoding"]
		except: encoding = None

		if encoding == 'gzip': result = gzip.GzipFile(fileobj=BytesIO(result)).read()
		if not as_bytes:
			# result = result.decode('utf-8') # UnicodeDecodeError -> 'utf-8' codec can't decode byte 0xe5
			result = result.decode(encoding='utf-8', errors='ignore')

		if output == 'extended':
			try:
				response_headers = dict([(item[0].title(), item[1]) for item in list(response.info().items())]) # behaves differently 18 to 19. 18 I had 3 "Set-Cookie:" it combined all 3 values into 1 key. In 19 only the last keys value was present.
			except:
				from fenom import log_utils
				log_utils.error()
				response_headers = response.headers
			try: response_code = str(response.code)
			except: response_code = str(response.status_code)
			try: cookie = '; '.join(['%s=%s' % (i.name, i.value) for i in cookies])
			except: pass
			if close is True: response.close()
			return (result, response_code, response_headers, headers, cookie)
		else:
			if close is True: response.close()
			return result
	except:
		from fenom import log_utils
		log_utils.error('Request-Error url=(%s)' % url)
		return None

def _add_request_header(_request, headers):
	try:
		if not headers: headers = {}
		scheme = _request.type
		host = _request.host
		referer = headers.get('Referer') if 'Referer' in headers else '%s://%s/' % (scheme, host)
		_request.add_unredirected_header('Host', host)
		_request.add_unredirected_header('Referer', referer)
		for key in headers:
			_request.add_header(key, headers[key])
	except:
		from fenom import log_utils
		log_utils.error()

def randomagent():
	RAND_UAS = [
		'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
		'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/116.0',
		'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
		'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
		'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36 Edg/114.0.1823.82',
		'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36 Edg/115.0.1901.188',
	]
	return choice(RAND_UAS)

