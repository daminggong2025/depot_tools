# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import engine_types, post_process

DEPS = [
    'gerrit',
    'recipe_engine/assertions',
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/raw_io',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
    'tryserver',
]


def RunSteps(api):
  gerrit_change = api.tryserver.gerrit_change
  if gerrit_change is None:
    return

  api.step.empty('testing gerrit change')

  api.assertions.assertEqual(gerrit_change.host,
                             'fake-host-review.googlesource.com')
  api.assertions.assertEqual(gerrit_change.project, 'fake/project')
  api.assertions.assertEqual(gerrit_change.change, 91827)
  api.assertions.assertEqual(gerrit_change.patchset, 1)

  api.assertions.assertEqual(gerrit_change.gitiles_host,
                             'fake-host.googlesource.com')
  api.assertions.assertEqual(gerrit_change.repo_url,
                             'https://fake-host.googlesource.com/fake/project')
  api.assertions.assertEqual(
      gerrit_change.review_url,
      'https://fake-host-review.googlesource.com/c/91827/1')

  api.assertions.assertEqual(gerrit_change.owner,
                             engine_types.freeze({'name': 'John Doe'}))
  api.assertions.assertEqual(gerrit_change.target_ref, 'refs/heads/fake-ref')
  api.assertions.assertEqual(gerrit_change.fetch_ref, 'refs/changes/27/91827/1')

  api.assertions.assertEqual(gerrit_change.commit_message,
                             'Change commit message')
  api.assertions.assertEqual(
      dict(gerrit_change.footers),
      engine_types.freeze({
          'foo': ['foo1', 'foo2'],
          'bar': ['bar1', 'bar2'],
      }))
  api.assertions.assertEqual(gerrit_change.get_footer('foo'), ('foo1', 'foo2'))
  api.assertions.assertEqual(gerrit_change.get_footer('baz'), ())


def GenTests(api):
  yield api.test(
      'no-change',
      api.buildbucket.ci_build(),
      api.post_check(post_process.DoesNotRun, 'testing gerrit change'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'change',
      api.buildbucket.try_build(
          git_repo='https://fake-host.googlesource.com/fake/project',
          change_number=91827,
          patch_set=1),
      api.tryserver.gerrit_change_target_ref('refs/heads/fake-ref'),
      api.tryserver.get_footers({
          'foo': ['foo1', 'foo2'],
          'bar': ['bar1', 'bar2']
      }),
      api.post_check(post_process.MustRun, 'testing gerrit change'),
      api.post_process(post_process.DropExpectation),
  )
