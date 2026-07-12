#!/usr/bin/env vpython3
# Copyright (c) 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import ntpath
import os
import sys
import unittest
from unittest import mock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

import gn_helper
from testing_support import trial_dir


def write(filename, content):
    """Writes the content of a file and create the directories as needed."""
    filename = os.path.abspath(filename)
    dirname = os.path.dirname(filename)
    if not os.path.isdir(dirname):
        os.makedirs(dirname)
    with open(filename, 'w') as f:
        f.write(content)


class GnHelperTest(trial_dir.TestCase):

    def setUp(self):
        super().setUp()
        self.previous_dir = os.getcwd()
        os.chdir(self.root_dir)

    def tearDown(self):
        os.chdir(self.previous_dir)
        super().tearDown()

    def test_lines(self):
        write('.gn', '')
        out_dir = os.path.join('out', 'dir')
        # Make sure nested import directives work. This is based on the
        # reclient test.
        write(os.path.join(out_dir, 'args.gn'), 'import("//out/common.gni")')
        write(os.path.join('out', 'common.gni'), 'import("common_2.gni")')
        write(os.path.join('out', 'common_2.gni'), 'use_remoteexec=true')

        lines = list(gn_helper.lines(out_dir))

        # The test will only pass if both imports work and
        # 'use_remoteexec=true' is seen.
        self.assertListEqual(lines, [
            'use_remoteexec=true',
        ])

    def test_args(self):
        write('.gn', '')
        out_dir = os.path.join('out', 'dir')
        # Make sure nested import directives work. This is based on the
        # reclient test.
        write(os.path.join(out_dir, 'args.gn'), 'import("//out/common.gni")')
        write(os.path.join('out', 'common.gni'), 'import("common_2.gni")')
        write(os.path.join('out', 'common_2.gni'), 'use_remoteexec=true')

        lines = list(gn_helper.args(out_dir))

        # The test will only pass if both imports work and
        # 'use_remoteexec=true' is seen.
        self.assertListEqual(lines, [
            ('use_remoteexec', 'true'),
        ])

    def test_args_spaces(self):
        write('.gn', '')
        out_dir = os.path.join('out', 'dir')
        # Make sure nested import directives work. This is based on the
        # reclient test.
        write(os.path.join(out_dir, 'args.gn'), 'import("//out/common.gni")')
        write(os.path.join('out', 'common.gni'), 'import("common_2.gni")')
        write(os.path.join('out', 'common_2.gni'), '  use_remoteexec = true  ')

        lines = list(gn_helper.args(out_dir))

        # The test will only pass if both imports work and
        # 'use_remoteexec=true' is seen.
        self.assertListEqual(lines, [
            ('use_remoteexec', 'true'),
        ])

    def test_import_os_absolute_path_raises(self):
        write('.gn', '')
        out_dir = os.path.join('out', 'dir')
        # GN requires absolute paths to start with "/", so an OS-absolute
        # path like C:/foo/common.gni must be rejected. os.path.isabs only
        # returns True for it under Windows path semantics, so substitute
        # ntpath.isabs to cover this branch on any host OS.
        write(os.path.join(out_dir, 'args.gn'), 'import("C:/foo/common.gni")')
        with mock.patch('gn_helper.os.path.isabs', ntpath.isabs):
            with self.assertRaisesRegex(
                    Exception,
                    'Absolute path "C:/foo/common.gni" should start with '
                    '"/" in GN'):
                list(gn_helper.lines(out_dir))

if __name__ == '__main__':
    unittest.main()
