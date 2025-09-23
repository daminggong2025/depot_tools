
from . import api as _api
DEPS = [
    'recipe_engine/context',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]

API = _api.GitClApi
