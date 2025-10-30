# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import collections.abc
import dataclasses
import functools
import typing

from recipe_engine import engine_types
from recipe_engine import recipe_api

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2

class Constants:
  def __init__(self):
    self.NONTRIVIAL_ROLL_FOOTER = 'Recipe-Nontrivial-Roll'
    self.MANUAL_CHANGE_FOOTER = 'Recipe-Manual-Change'
    self.BYPASS_FOOTER = 'Recipe-Tryjob-Bypass-Reason'
    self.SKIP_RETRY_FOOTER = 'Disable-Retries'
    self.CQ_DEPEND_FOOTER = 'Cq-Depend'
    self.ALL_VALID_FOOTERS = set([
        self.NONTRIVIAL_ROLL_FOOTER, self.MANUAL_CHANGE_FOOTER,
        self.BYPASS_FOOTER, self.SKIP_RETRY_FOOTER, self.CQ_DEPEND_FOOTER
    ])


constants = Constants()


@dataclasses.dataclass(frozen=True)
class GerritChange:
  """Type providing access to details about a Gerrit change."""

  _api: TryserverApi
  _proto: common_pb2.GerritChange

  @property
  def host(self) -> str:
    """The gerrit host the change is from.

    e.g. "chromium-review.googlesource.com"
    """
    return self._proto.host

  @property
  def project(self) -> str:
    """The project/repo the change is from.

    e.g. "chromium/src"
    """
    return self._proto.project

  @property
  def change(self) -> int:
    """The gerrit change number."""
    return self._proto.change

  @property
  def patchset(self) -> int:
    """The gerrit patchset number."""
    return self._proto.patchset

  @functools.cached_property
  def gitiles_host(self) -> str:
    """The gitiles host that hosts the change's code.

    e.g. "chromium.googlesource.com"
    """
    base_name = self._proto.host.removesuffix('-review.googlesource.com')
    return f'{base_name}.googlesource.com'

  @functools.cached_property
  def repo_url(self) -> str:
    """The gitiles URL for the repo

    e.g. "https://chromium.googlesource.com/chromium/src"
    """
    return f'https://{self.gitiles_host}/{self.project}'

  @functools.cached_property
  def review_url(self) -> str:
    """The review URL for the active patchset.

    e.g. "https://chromium-review.googlesource.com/c/12345/1"
    """
    # Gerrit redirects to insert the project into the URL.
    proto = self._proto
    return f'https://{proto.host}/c/{proto.change}/{proto.patchset}'

  @dataclasses.dataclass(frozen=True, kw_only=True)
  class _ChangeInfo:
    target_ref: str
    fetch_ref: str
    owner: collections.abc.Mapping[str, typing.Any]

  @functools.cached_property
  def _change_info(self) -> _ChangeInfo:
    cl = self._proto
    api = self._api

    td = api._test_data if api._test_data.enabled else {}
    mock_res = [{
      'branch': td.get('gerrit_change_target_ref', 'main'),
      'revisions': {
        '184ebe53805e102605d11f6b143486d15c23a09c': {
          '_number': str(cl.patchset),
          'ref': 'refs/changes/%02d/%d/%d' % (
              cl.change % 100, cl.change, cl.patchset),
        },
      },
      'owner': {
          'name': 'John Doe',
      },
    }]
    res = api.m.gerrit.get_changes(
        host='https://' + cl.host,
        query_params=[('change', cl.change)],
        # This list must remain static/hardcoded.
        # If you need extra info, either change it here (hardcoded) or
        # fetch separately.
        o_params=['ALL_REVISIONS', 'DOWNLOAD_COMMANDS'],
        limit=1,
        name='fetch current CL info',
        timeout=480,
        step_test_data=lambda: api.m.json.test_api.output(mock_res))[0]

    target_ref = res['branch']
    if not target_ref.startswith('refs/'):
      target_ref = f'refs/heads/{target_ref}'

    for rev in res['revisions'].values():
      if int(rev['_number']) == cl.patchset:
        fetch_ref = rev['ref']
        break
    else:
      assert False  # pragma: no cover

    owner = engine_types.freeze(res['owner'])

    return self._ChangeInfo(
        target_ref=target_ref,
        fetch_ref=fetch_ref,
        owner=owner,
    )

  @property
  def owner(self) -> collections.abc.Mapping[str, typing.Any]:
    """The owner of the change.

    Has the format described in
    https://gerrit-review.googlesource.com/Documentation/rest-api-accounts.html#account-info
    """
    return self._change_info.owner

  @property
  def target_ref(self) -> str:
    """The destination ref of the change.

    e.g. "refs/heads/main"
    """
    return self._change_info.target_ref

  @property
  def fetch_ref(self) -> str:
    """The ref that can be used to fetch the change's code.

    e.g. "refs/changes/27/91827/1"
    """
    return self._change_info.fetch_ref

  @functools.cached_property
  def commit_message(self) -> str:
    """The commit message of the change."""
    return self._api.m.gerrit.get_change_description(
        host=f'https://{self._proto.host}',
        change=self._proto.change,
        patchset=self._proto.patchset,
        timeout=480)

  @functools.cached_property
  def footers(
      self) -> collections.abc.Mapping[str, collections.abc.Sequence[str]]:
    """The footers of the change.

    Footers are machine readable tags embedded in commit messages. See
    git-footers documentation for more information.
    """
    return engine_types.freeze(self._api._get_footer_step(self.commit_message))

  def get_footer(self, tag: str) -> collections.abc.Sequence[str]:
    """Get the values of a specific footer."""
    return self.footers.get(tag, ())


class TryserverApi(recipe_api.RecipeApi):

  def __init__(self, *args, **kwargs):
    super(TryserverApi, self).__init__(*args, **kwargs)
    self._gerrit_change: GerritChange | None = None

  def initialize(self):
    changes = self.m.buildbucket.build.input.gerrit_changes
    if len(changes) == 1:
      self.set_change(changes[0])

  @property
  def valid_footers(self):  #pragma: nocover
    return constants.ALL_VALID_FOOTERS

  @property
  def constants(self):  #pragma: nocover
    # Nocover to be removed when callers (not within depot_tools) exercise this
    return constants

  @property
  def gerrit_change(self) -> GerritChange | None:
    """Get the current gerrit change.

    If the currently executing build has exactly 1 gerrit change, then
    the module will be initialized with that as the current change.
    Otherwise, it will be initialized with None. Calling the set_change
    method will change the current gerrit change.
    """
    return self._gerrit_change

  @property
  def gerrit_change_repo_url(self) -> str | None:
    """Returns canonical URL of the gitiles repo of the current Gerrit CL.

    Populated iff gerrit_change is populated.
    """
    self.m.warning.issue('TRYSERVER_GERRIT_CHANGE_PROPERTIES')
    return self._gerrit_change.repo_url if self._gerrit_change else None

  @property
  def gerrit_change_repo_host(self) -> str | None:
    """Returns the host of the gitiles repo of the current Gerrit CL.

    Populated iff gerrit_change is populated.
    """
    self.m.warning.issue('TRYSERVER_GERRIT_CHANGE_PROPERTIES')
    return self._gerrit_change.gitiles_host if self._gerrit_change else None

  @property
  def gerrit_change_repo_project(self) -> str | None:
    """Returns the project of the gitiles repo of the current Gerrit CL.

    Populated iff gerrit_change is populated.
    """
    self.m.warning.issue('TRYSERVER_GERRIT_CHANGE_PROPERTIES')
    return self._gerrit_change.project if self._gerrit_change else None

  @property
  def gerrit_change_owner(
      self) -> collections.abc.Mapping[str, typing.Any] | None:
    """Returns owner of the current Gerrit CL.

    Populated iff gerrit_change is populated.
    Is a dictionary with keys like "name".
    """
    self.m.warning.issue('TRYSERVER_GERRIT_CHANGE_PROPERTIES')
    return self._gerrit_change.owner if self._gerrit_change else None

  @property
  def gerrit_change_review_url(self) -> str | None:
    """Returns the review URL for the active patchset."""
    self.m.warning.issue('TRYSERVER_GERRIT_CHANGE_PROPERTIES')
    return self._gerrit_change.review_url if self._gerrit_change else None

  @property
  def gerrit_change_fetch_ref(self) -> str | None:
    """Returns gerrit patch ref, e.g. "refs/heads/45/12345/6, or None.

    Populated iff gerrit_change is populated.
    """
    self.m.warning.issue('TRYSERVER_GERRIT_CHANGE_PROPERTIES')
    return self._gerrit_change.fetch_ref if self._gerrit_change else None

  @property
  def gerrit_change_target_ref(self) -> str | None:
    """Returns gerrit change destination ref, e.g. "refs/heads/main".

    Populated iff gerrit_change is populated.
    """
    self.m.warning.issue('TRYSERVER_GERRIT_CHANGE_PROPERTIES')
    return self._gerrit_change.target_ref if self._gerrit_change else None

  @property
  def gerrit_change_number(self) -> int | None:
    """Returns gerrit change patchset, e.g. 12345 for a patch ref of
    "refs/heads/45/12345/6".

    Populated iff gerrit_change is populated. Returns None if not populated.
    """
    self.m.warning.issue('TRYSERVER_GERRIT_CHANGE_PROPERTIES')
    return self._gerrit_change.change if self._gerrit_change else None

  @property
  def gerrit_patchset_number(self) -> int | None:
    """Returns gerrit change patchset, e.g. 6 for a patch ref of
    "refs/heads/45/12345/6".

    Populated iff gerrit_change is populated Returns None if not populated..
    """
    self.m.warning.issue('TRYSERVER_GERRIT_CHANGE_PROPERTIES')
    return self._gerrit_change.patchset if self._gerrit_change else None

  @property
  def is_tryserver(self):
    """Returns true iff we have a change to check out."""
    return (self.is_patch_in_git or self.is_gerrit_issue)

  @property
  def is_gerrit_issue(self):
    """Returns true iff the properties exist to match a Gerrit issue."""
    if self.gerrit_change:
      return True
    # TODO(tandrii): remove this, once nobody is using buildbot Gerrit Poller.
    return ('event.patchSet.ref' in self.m.properties and
            'event.change.url' in self.m.properties and
            'event.change.id' in self.m.properties)

  @property
  def is_patch_in_git(self):
    return (self.m.properties.get('patch_storage') == 'git' and
            self.m.properties.get('patch_repo_url') and
            self.m.properties.get('patch_ref'))

  def require_is_tryserver(self):
    if self.m.tryserver.is_tryserver:
      return

    status = self.m.step.EXCEPTION
    step_text = 'This recipe requires a gerrit CL for the source under test'
    if self.m.led.launched_by_led:
      status = self.m.step.FAILURE
      step_text += (
          "\n run 'led edit-cr-cl <source CL URL>' to attach a CL to test"
      )
    self.m.step.empty('not a tryjob', status=status, step_text=step_text)

  def get_files_affected_by_patch(self, patch_root,
                                  report_files_via_property=None,
                                  **kwargs):
    """Returns list of paths to files affected by the patch.

    Args:
      * patch_root: path relative to api.path['root'], usually obtained from
        api.gclient.get_gerrit_patch_root().
      * report_files_via_property: name of the output property to report the
        list of the files. If None (default), do not report.

    Returned paths will be relative to to api.path['root'].
    """
    cwd = self.m.context.cwd or self.m.path.start_dir / patch_root
    with self.m.context(cwd=cwd):
      step_result = self.m.git(
          '-c', 'core.quotePath=false', 'diff', '--cached', '--name-only',
          name='git diff to analyze patch',
          stdout=self.m.raw_io.output(),
          step_test_data=lambda:
            self.m.raw_io.test_api.stream_output('foo.cc'),
          **kwargs)
    paths = [self.m.path.join(patch_root, p.decode('utf-8')) for p in
             step_result.stdout.splitlines()]
    paths.sort()
    if self.m.platform.is_win:
      # Looks like "analyze" wants POSIX slashes even on Windows (since git
      # uses that format even on Windows).
      paths = [path.replace('\\', '/') for path in paths]
    step_result.presentation.logs['files'] = paths
    if report_files_via_property:
      step_result.presentation.properties[report_files_via_property] = {
        'total_count': len(paths),
        # Do not report too many because it might violate build size limits,
        # and isn't very useful anyway.
        'first_100': paths[:100],
      }
    return paths

  def set_subproject_tag(self, subproject_tag):
    """Adds a subproject tag to the build.

    This can be used to distinguish between builds that execute different steps
    depending on what was patched, e.g. blink vs. pure chromium patches.
    """
    assert self.is_tryserver

    step_result = self.m.step('TRYJOB SET SUBPROJECT_TAG', cmd=None)
    step_result.presentation.properties['subproject_tag'] = subproject_tag
    step_result.presentation.step_text = subproject_tag

  def _set_failure_type(self, failure_type):
    if not self.is_tryserver:
      return

    # TODO(iannucci): add API to set properties regardless of the current step.
    step_result = self.m.step('TRYJOB FAILURE', cmd=None)
    step_result.presentation.properties['failure_type'] = failure_type
    step_result.presentation.step_text = failure_type
    step_result.presentation.status = 'FAILURE'

  def set_patch_failure_tryjob_result(self):
    """Mark the tryjob result as failure to apply the patch."""
    self._set_failure_type('PATCH_FAILURE')

  def set_compile_failure_tryjob_result(self):
    """Mark the tryjob result as a compile failure."""
    self._set_failure_type('COMPILE_FAILURE')

  def set_test_failure_tryjob_result(self):
    """Mark the tryjob result as a test failure.

    This means we started running actual tests (not prerequisite steps
    like checkout or compile), and some of these tests have failed.
    """
    self._set_failure_type('TEST_FAILURE')

  def set_invalid_test_results_tryjob_result(self):
    """Mark the tryjob result as having invalid test results.

    This means we run some tests, but the results were not valid
    (e.g. no list of specific test cases that failed, or too many
    tests failing, etc).
    """
    self._set_failure_type('INVALID_TEST_RESULTS')

  def set_test_timeout_tryjob_result(self):
    """Mark the tryjob result as a test timeout.

    This means tests were scheduled but didn't finish executing within the
    timeout.
    """
    self._set_failure_type('TEST_TIMEOUT')

  def set_test_expired_tryjob_result(self):
    """Mark the tryjob result as a test expiration.

    This means a test task expired and was never scheduled, most likely due to
    lack of capacity.
    """
    self._set_failure_type('TEST_EXPIRED')

  def get_footers(
      self,
      patch_text: str | None = None,
  ) -> collections.abc.Mapping[str, collections.abc.Sequence[str]]:
    """Retrieves footers from the patch description.

    footers are machine readable tags embedded in commit messages. See
    git-footers documentation for more information.
    """
    if patch_text is not None:
      return self._get_footer_step(patch_text)
    if self._gerrit_change is None:
      raise Exception(
          'No patch text or associated changelist, cannot get footers'
      )  # pragma: nocover
    self.m.warning.issue('TRYSERVER_GERRIT_CHANGE_PROPERTIES')
    return self._gerrit_change.footers

  def _get_footer_step(
      self,
      patch_text: str,
  ) -> collections.abc.Mapping[str, collections.abc.Sequence[str]]:
    result = self.m.step(
        'parse description',
        [
            'python3',
            self.repo_resource('git_footers.py'),
            '--json',
            self.m.json.output(),
        ],
        stdin=self.m.raw_io.input(data=patch_text),
        step_test_data=lambda: self.m.json.test_api.output({}),
    )
    return result.json.output

  def get_footer(
      self,
      tag: str,
      patch_text: str | None = None,
  ) -> collections.abc.Sequence[str]:
    """Gets a specific tag from a CL description"""
    footers = self.get_footers(patch_text)
    return footers.get(tag, ())

  def normalize_footer_name(self, footer):
    return '-'.join([ word.title() for word in footer.strip().split('-') ])

  def get_change_description(self) -> str | None:
    """Gets the CL description."""
    self.m.warning.issue('TRYSERVER_GERRIT_CHANGE_PROPERTIES')
    return self._gerrit_change.commit_message if self._gerrit_change else None

  def set_change(self, change: common_pb2.GerritChange) -> None:
    """Set the gerrit change for this module.

    Args:
      * change: a self.m.buildbucket.common_pb2.GerritChange.
    """
    copy = common_pb2.GerritChange()
    copy.CopyFrom(change)
    self._gerrit_change = GerritChange(self, copy)
