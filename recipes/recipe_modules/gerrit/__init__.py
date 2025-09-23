
from . import api as _api
from . import test_api as _test_api
DEPS = [
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'recipe_engine/time',
]

API = _api.GerritApi
TEST_API = _test_api.GerritTestApi
