# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process


DEPS = [
    'tryserver',
    'recipe_engine/assertions',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
]


def RunSteps(api):
  if api.properties.get('expand_submodules', False):
    result = api.tryserver.get_files_affected_by_patch_with_submodules(
        api.properties['patch_root'],
        report_files_via_property=api.properties.get(
            'report_files_via_property'))

    # Assert on the affected files
    api.assertions.assertCountEqual(result.affected_files,
                                    api.properties['expected_files'])

    # Assert on the other fields of the SubmodulePathsResult if specified in properties
    if 'expected_unchecked_out_submodules' in api.properties:
      api.assertions.assertCountEqual(
          result.unchecked_out_submodules,
          api.properties['expected_unchecked_out_submodules'])
    if 'expected_deleted_submodules' in api.properties:
      api.assertions.assertCountEqual(
          result.deleted_submodules,
          api.properties['expected_deleted_submodules'])
    if 'expected_new_submodules' in api.properties:
      api.assertions.assertCountEqual(result.new_submodules,
                                      api.properties['expected_new_submodules'])
    if 'expected_nested_submodules' in api.properties:
      api.assertions.assertCountEqual(
          result.nested_submodules,
          api.properties['expected_nested_submodules'])
  else:
    files = api.tryserver.get_files_affected_by_patch(
        api.properties['patch_root'],
        report_files_via_property=api.properties.get(
            'report_files_via_property'))
    api.assertions.assertCountEqual(files, api.properties['expected_files'])


def GenTests(api):
  def no_properties_set(check, steps, step_name):
    check(not steps[step_name].output_properties)

  def has_nested_submodules_log(check, steps, step_name):
    check('nested_submodules' in steps[step_name].logs)

  def has_unchecked_out_submodules_log(check, steps, step_name):
    check('unchecked_out_submodules' in steps[step_name].logs)

  def has_deleted_submodules_log(check, steps, step_name):
    check('deleted_submodules' in steps[step_name].logs)

  def has_new_submodules_log(check, steps, step_name):
    check('new_submodules' in steps[step_name].logs)

  yield api.test(
      'basic',
      api.properties(
          patch_root='',
          expected_files=['foo.cc'],
      ),
      api.post_check(no_properties_set, 'git diff to analyze patch'),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'patch_root',
      api.properties(
          patch_root='test/patch/root',
          expected_files=['test/patch/root/foo.cc'],
      ),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'test-data',
      api.tryserver.get_files_affected_by_patch(['foo/bar.cc', 'baz/shaz.cc']),
      api.properties(
          patch_root='test/patch/root',
          expected_files=[
              'test/patch/root/foo/bar.cc',
              'test/patch/root/baz/shaz.cc',
          ],
      ),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'report-files-via-property',
      api.tryserver.get_files_affected_by_patch(['foo/bar.cc', 'baz/shaz.cc']),
      api.properties(
          patch_root='test/patch/root',
          report_files_via_property='affected-files',
          expected_files=[
              'test/patch/root/foo/bar.cc',
              'test/patch/root/baz/shaz.cc',
          ],
      ),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'windows',
      api.tryserver.get_files_affected_by_patch(['foo/bar.cc', 'baz/shaz.cc']),
      api.platform('win', 32),
      api.properties(
          patch_root='test\\patch\\root',
          report_files_via_property='affected-files',
          expected_files=[
              'test/patch/root/foo/bar.cc',
              'test/patch/root/baz/shaz.cc',
          ],
      ),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'file-with-spaces',
      api.tryserver.get_files_affected_by_patch(['file with spaces.txt']),
      api.properties(
          patch_root='',
          expected_files=['file with spaces.txt'],
      ),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'submodule',
      api.path.files_exist(api.path.start_dir / 'sub' / '.git'),
      api.step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output(
              '\n:100644 160000 1234567 89abcdef M\tsub\n')),
      api.step_data(
          'git diff submodules.git diff submodule sub',
          api.raw_io.stream_output(
              '\n:100644 100644 1234567 89abcdef M\tsub_foo.cc\n')),
      api.properties(
          patch_root='',
          expand_submodules=True,
          expected_files=['sub/sub_foo.cc'],
      ),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'submodule-added',
      api.path.files_exist(api.path.start_dir / 'sub' / '.git'),
      api.step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output(':000000 160000 0000000 89abcdef A\tsub')),
      api.step_data(
          'git diff submodules.git diff submodule sub',
          api.raw_io.stream_output(
              ':000000 100644 0000000 89abcdef A\tsub_foo.cc')),
      api.properties(
          patch_root='',
          expand_submodules=True,
          expected_files=['sub/sub_foo.cc'],
          expected_new_submodules=['sub'],
      ),
      api.post_check(has_new_submodules_log, 'git diff submodules'),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'submodule-deleted',
      api.step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output(':160000 160000 1234567 0000000 D\tsub')),
      api.properties(
          patch_root='',
          expand_submodules=True,
          expected_files=[],
          expected_deleted_submodules=['sub'],
      ),
      api.post_check(has_deleted_submodules_log, 'git diff submodules'),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'submodule-none',
      api.step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output(
              ':100644 100644 1234567 89abcdef M\tfoo.cc')),
      api.properties(
          patch_root='',
          expand_submodules=True,
          expected_files=['foo.cc'],
      ),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'submodule-win',
      api.platform('win', 32),
      api.path.files_exist(api.path.start_dir / 'sub' / '.git'),
      api.step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output(
              '\n:100644 160000 1234567 89abcdef M\tsub\n')),
      api.step_data(
          'git diff submodules.git diff submodule sub',
          api.raw_io.stream_output(
              '\n:100644 100644 1234567 89abcdef M\tsub_foo.cc\n')),
      api.properties(
          patch_root='',
          expand_submodules=True,
          expected_files=['sub/sub_foo.cc'],
      ),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'submodule-nested',
      api.path.files_exist(api.path.start_dir / 'sub' / '.git'),
      api.step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output(
              '\n:100644 160000 1234567 89abcdef M\tsub\n')),
      api.step_data(
          'git diff submodules.git diff submodule sub',
          api.raw_io.stream_output(
              '\n:100644 160000 1234567 89abcdef M\tnested_sub\n')),
      api.properties(
          patch_root='',
          expand_submodules=True,
          expected_files=[],
          expected_nested_submodules=['sub/nested_sub'],
      ),
      api.post_check(has_nested_submodules_log, 'git diff submodules'),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'submodule-unchecked-out',
      api.step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output(
              '\n:100644 160000 1234567 89abcdef M\tsub\n')),
      api.properties(
          patch_root='',
          expand_submodules=True,
          expected_files=[],
          expected_unchecked_out_submodules=['sub'],
      ),
      api.post_check(has_unchecked_out_submodules_log, 'git diff submodules'),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'submodule-unchecked-out-win',
      api.platform('win', 32),
      api.step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output(
              '\n:100644 160000 1234567 89abcdef M\tsub\n')),
      api.properties(
          patch_root='',
          expand_submodules=True,
          expected_files=[],
          expected_unchecked_out_submodules=['sub'],
      ),
      api.post_check(has_unchecked_out_submodules_log, 'git diff submodules'),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'submodule-expansion-failed',
      api.path.files_exist(api.path.start_dir / 'sub' / '.git'),
      api.step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output(
              '\n:100644 160000 1234567 89abcdef M\tsub\n')),
      api.step_data('git diff submodules.git diff submodule sub', retcode=1),
      api.properties(
          patch_root='',
          expand_submodules=True,
          expected_files=[],
      ),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.StatusException),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'submodule-deleted-win',
      api.platform('win', 32),
      api.step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output(':160000 160000 1234567 0000000 D\tsub')),
      api.properties(
          patch_root='',
          expand_submodules=True,
          expected_files=[],
          expected_deleted_submodules=['sub'],
      ),
      api.post_check(has_deleted_submodules_log, 'git diff submodules'),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'submodule-added-win',
      api.platform('win', 32),
      api.path.files_exist(api.path.start_dir / 'sub' / '.git'),
      api.step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output(':000000 160000 0000000 89abcdef A\tsub')),
      api.step_data(
          'git diff submodules.git diff submodule sub',
          api.raw_io.stream_output(
              ':000000 100644 0000000 89abcdef A\tsub_foo.cc')),
      api.properties(
          patch_root='',
          expand_submodules=True,
          expected_files=['sub/sub_foo.cc'],
          expected_new_submodules=['sub'],
      ),
      api.post_check(has_new_submodules_log, 'git diff submodules'),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'submodule-nested-win',
      api.platform('win', 32),
      api.path.files_exist(api.path.start_dir / 'sub' / '.git'),
      api.step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output(
              '\n:100644 160000 1234567 89abcdef M\tsub\n')),
      api.step_data(
          'git diff submodules.git diff submodule sub',
          api.raw_io.stream_output(
              '\n:100644 160000 1234567 89abcdef M\tnested_sub\n')),
      api.properties(
          patch_root='',
          expand_submodules=True,
          expected_files=[],
          expected_nested_submodules=['sub/nested_sub'],
      ),
      api.post_check(has_nested_submodules_log, 'git diff submodules'),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'submodule-none-win',
      api.platform('win', 32),
      api.step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output(
              ':100644 100644 1234567 89abcdef M\tfoo\\bar.cc')),
      api.properties(
          patch_root='',
          expand_submodules=True,
          expected_files=['foo/bar.cc'],
      ),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'submodule-report-property',
      api.path.files_exist(api.path.start_dir / 'sub' / '.git'),
      api.step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output(
              '\n:100644 160000 1234567 89abcdef M\tsub\n')),
      api.step_data(
          'git diff submodules.git diff submodule sub',
          api.raw_io.stream_output(
              '\n:100644 100644 1234567 89abcdef M\tsub_foo.cc\n')),
      api.properties(
          patch_root='',
          expand_submodules=True,
          report_files_via_property='affected-files',
          expected_files=['sub/sub_foo.cc'],
      ),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'submodule-none-report-property',
      api.step_data(
          'git diff to analyze patch',
          api.raw_io.stream_output(
              ':100644 100644 1234567 89abcdef M\tfoo.cc')),
      api.properties(
          patch_root='',
          expand_submodules=True,
          report_files_via_property='affected-files',
          expected_files=['foo.cc'],
      ),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )
