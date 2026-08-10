from os.path import dirname
import sys

lib_path = dirname(__file__) or '.'
if lib_path not in sys.path: sys.path.insert(0, lib_path)

if __name__ == '__main__':
	from routing import Router
	Router().run(sys)
