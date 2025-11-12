# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process, recipe_api, turboci

from PB.turboci.graph.orchestrator.v1 import (
    check_kind as check_kind_pb2,
    check_state as check_state_pb2,
    graph_view as graph_view_pb2,
)
from PB.turboci.data.gerrit.v1 import (
    gerrit_change_info as gerrit_change_info_pb2,
    gob_source_check_options as gob_source_check_options_pb2,
    gob_source_check_results as gob_source_check_results_pb2,
)

DEPS = [
    'bot_update',
    'gclient',
    'recipe_engine/assertions',
    'recipe_engine/buildbucket',
    'recipe_engine/path',
    'recipe_engine/properties',
]

PROPERTIES = {
    'turboci_check_id': recipe_api.Property(),
}

_FAKE_REPO_URL = 'https://fake-host.googlesource.com/fake/project'


def RunSteps(api, turboci_check_id: str | None):
  gclient_config = api.gclient.make_config()
  s = gclient_config.solutions.add()
  s.url = _FAKE_REPO_URL
  s.name = 'fake-project'
  api.bot_update.ensure_checkout(gclient_config,
                                 turboci_check_id=turboci_check_id)


def GenTests(api):
  yield api.test(
      'no-check',
      api.properties(turboci_check_id=None),
      api.buildbucket.try_build(git_repo=_FAKE_REPO_URL),
      api.override_step_data(
          'bot_update',
          api.bot_update.output_json(
              'fake-project',
              {
                  'got_revision': 'fake-project',
              },
              repo_urls={
                  'fake-project': f'{_FAKE_REPO_URL}.git',
              },
          ),
      ),
      api.post_process(post_process.DropExpectation),
      api.assert_turboci_graph(
          lambda assert_, graph: assert_(not graph.checks)),
  )

  def check_try_graph_assert(assert_, graph: graph_view_pb2.GraphView):
    check_id = 'fake-check-id'
    check_view = graph.checks[check_id]
    assert_(list(graph.checks.keys()) == [check_id])

    assert_(check_view.check.kind == check_kind_pb2.CheckKind.CHECK_KIND_SOURCE)
    assert_(
        check_view.check.state == check_state_pb2.CheckState.CHECK_STATE_FINAL)

    gob_source_check_options = turboci.get_option(
        gob_source_check_options_pb2.GobSourceCheckOptions, check_view)
    expected_gob_source_check_options = gob_source_check_options_pb2.GobSourceCheckOptions(
        gerrit_changes=[
            gob_source_check_options_pb2.GobSourceCheckOptions.GerritChange(
                hostname='fake-host',
                change_number=123456,
                patchset=7,
                mounts_to_apply='.',
            ),
        ],
        base_pinned_repos=gob_source_check_options_pb2.GobSourceCheckOptions.
        PinnedRepoMounts(project_commit=gob_source_check_options_pb2.
                         GobSourceCheckOptions.PinnedRepoMounts.GitCommit(
                             ref='refs/heads/main')),
    )
    assert_(gob_source_check_options == expected_gob_source_check_options)

    gob_source_check_results = turboci.get_results(
        gob_source_check_results_pb2.GobSourceCheckResults, check_view)
    expected_gob_source_check_results = [
        gob_source_check_results_pb2.GobSourceCheckResults(changes=[
            gerrit_change_info_pb2.GerritChangeInfo(
                host='fake-host',
                project='fake/project',
                branch='main',
                change_number=123456,
                patchset=7,
            ),
        ]),
    ]
    assert_(gob_source_check_results == expected_gob_source_check_results)

  yield api.test(
      'check-try',
      api.properties(turboci_check_id='fake-check-id'),
      api.buildbucket.try_build(
          git_repo=_FAKE_REPO_URL,
          change_number=123456,
          patch_set=7,
      ),
      api.override_step_data(
          'bot_update',
          api.bot_update.output_json(
              'fake-project',
              {
                  'got_revision': 'fake-project',
              },
              repo_urls={
                  'fake-project': f'{_FAKE_REPO_URL}.git',
              },
          ),
      ),
      api.assert_turboci_graph(check_try_graph_assert),
  )
