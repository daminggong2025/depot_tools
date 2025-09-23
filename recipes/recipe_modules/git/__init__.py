
from . import api as _api
from . import test_api as _test_api
DEPS = [
  'depot_tools',
  'recipe_engine/buildbucket',
  'recipe_engine/context',
  'recipe_engine/runtime',
  'recipe_engine/path',
  'recipe_engine/platform',
  'recipe_engine/properties',
  'recipe_engine/raw_io',
  'recipe_engine/step',
]

API = _api.GitApi
TEST_API = _test_api.GitTestApi
