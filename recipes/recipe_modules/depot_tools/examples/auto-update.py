# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'depot_tools',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/runtime',
    'recipe_engine/step',
]


def RunSteps(api):
  api.step('root', ['ls', api.depot_tools.root])

  with api.depot_tools.on_path(auto_update=True):
    api.step('on_path_with_update', ['echo', '$PATH'])


def GenTests(api):
  yield (api.test('auto-update'))
