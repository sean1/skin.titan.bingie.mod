import types
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.module_isolation import load_module


ROOT = Path(__file__).resolve().parents[1]


def load_media_module():
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.build_url = Mock(side_effect=lambda params: params)
	kodi_utils.make_listitem = Mock()
	modules = types.ModuleType('modules')
	modules.kodi_utils = kodi_utils
	utils = types.ModuleType('modules.utils')
	utils.media_percentage_properties = Mock(side_effect=lambda rating: {'rating_percent': str(rating)})
	metadata = types.ModuleType('indexers.metadata')
	metadata.tmdb_image_base = 'https://image/%s%s'
	indexers = types.ModuleType('indexers')
	stubs = {'modules': modules, 'modules.kodi_utils': kodi_utils, 'modules.utils': utils, 'indexers': indexers, 'indexers.metadata': metadata}
	path = ROOT / 'resources' / 'lib' / 'menus' / 'media.py'
	return load_module('test_menu_media_module', path, stubs)


if __name__ == '__main__':
	unittest.main()
