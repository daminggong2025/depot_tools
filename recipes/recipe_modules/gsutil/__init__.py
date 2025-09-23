from . import api as _api
from PB.recipe_modules.depot_tools.gsutil import properties


DEPS = [
  'recipe_engine/context',
  'recipe_engine/file',
  'recipe_engine/path',
  'recipe_engine/platform',
  'recipe_engine/step',
]

ENV_PROPERTIES = properties.EnvProperties

API = _api.GSUtilApi
