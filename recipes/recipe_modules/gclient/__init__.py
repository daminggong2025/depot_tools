from . import api as _api
from . import test_api as _test_api
DEPS = [
    'git',
    'gitiles',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'tryserver',
]

from .config import config_ctx as CONFIG_CTX

__all__ = ['CONFIG_CTX']

API = _api.GclientApi
TEST_API = _test_api.GclientTestApi
