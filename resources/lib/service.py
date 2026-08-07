from entry import logger, POVMonitor

if __name__ == '__main__':
	logger('BINGIE Lite', 'Main Monitor Service Starting (%s)' % POVMonitor.ver())
	logger('BINGIE Lite', 'Settings Monitor Service Starting')

	POVMonitor().run()

	logger('BINGIE Lite', 'Settings Monitor Service Finished')
	logger('BINGIE Lite', 'Main Monitor Service Finished')
