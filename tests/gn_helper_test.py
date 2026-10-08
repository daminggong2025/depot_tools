#!/usr/bin/env vpython3
# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import sys
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

import gn_helper  # noqa: E402
from testing_support import trial_dir  # noqa: E402


def write(filename, content):
    """Writes the content of a file and create the directories as needed."""
    filename = os.path.abspath(filename)
    dirname = os.path.dirname(filename)
    if not os.path.isdir(dirname):
        os.makedirs(dirname)
    with open(filename, "w") as f:
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
        write(".gn", "")
        out_dir = os.path.join("out", "dir")
        # Make sure nested import directives work. This is based on the
        # reclient test.
        write(os.path.join(out_dir, "args.gn"), 'import("//out/common.gni")')
        write(os.path.join("out", "common.gni"), 'import("common_2.gni")')
        write(os.path.join("out", "common_2.gni"), "use_remoteexec=true")

        lines = list(gn_helper.lines(out_dir))

        # The test will only pass if both imports work and
        # 'use_remoteexec=true' is seen.
        self.assertListEqual(
            lines,
            [
                "use_remoteexec=true",
            ],
        )

    def test_args(self):
        write(".gn", "")
        out_dir = os.path.join("out", "dir")
        # Make sure nested import directives work. This is based on the
        # reclient test.
        write(os.path.join(out_dir, "args.gn"), 'import("//out/common.gni")')
        write(os.path.join("out", "common.gni"), 'import("common_2.gni")')
        write(os.path.join("out", "common_2.gni"), "use_remoteexec=true")

        lines = list(gn_helper.args(out_dir))

        # The test will only pass if both imports work and
        # 'use_remoteexec=true' is seen.
        self.assertListEqual(
            lines,
            [
                ("use_remoteexec", "true"),
            ],
        )

    def test_args_spaces(self):
        write(".gn", "")
        out_dir = os.path.join("out", "dir")
        # Make sure nested import directives work. This is based on the
        # reclient test.
        write(os.path.join(out_dir, "args.gn"), 'import("//out/common.gni")')
        write(os.path.join("out", "common.gni"), 'import("common_2.gni")')
        write(os.path.join("out", "common_2.gni"), "  use_remoteexec = true  ")

        lines = list(gn_helper.args(out_dir))

        # The test will only pass if both imports work and
        # 'use_remoteexec=true' is seen.
        self.assertListEqual(
            lines,
            [
                ("use_remoteexec", "true"),
            ],
        )

    def test_import_path_traversal_rejected(self):
        checkout = os.path.join(self.root_dir, "checkout")
        out_dir = os.path.join(checkout, "out", "dir")
        write(os.path.join(checkout, ".gn"), "")
        write(os.path.join(self.root_dir, "outside.gni"), "is_debug = true")
        write(
            os.path.join(out_dir, "args.gn"),
            'import("../../../outside.gni")',
        )

        with self.assertRaises(IOError):
            list(gn_helper.lines(out_dir))

    def test_import_root_traversal_rejected(self):
        checkout = os.path.join(self.root_dir, "checkout")
        out_dir = os.path.join(checkout, "out", "dir")
        write(os.path.join(checkout, ".gn"), "")
        write(os.path.join(self.root_dir, "outside.gni"), "is_debug = true")
        write(
            os.path.join(out_dir, "args.gn"),
            'import("//../outside.gni")',
        )

        with self.assertRaises(IOError):
            list(gn_helper.lines(out_dir))

    @unittest.skipIf(
        sys.platform.startswith("win"), "os.symlink requires privileges on win"
    )
    def test_import_symlink_escape_rejected(self):
        checkout = os.path.join(self.root_dir, "checkout")
        out_dir = os.path.join(checkout, "out", "dir")
        write(os.path.join(checkout, ".gn"), "")
        outside_file = os.path.join(self.root_dir, "outside.gni")
        write(outside_file, "is_debug = true")
        os.symlink(outside_file, os.path.join(checkout, "escaped.gni"))
        write(os.path.join(out_dir, "args.gn"), 'import("//escaped.gni")')

        with self.assertRaises(IOError):
            list(gn_helper.lines(out_dir))

    def test_import_cycle_rejected(self):
        write(".gn", "")
        out_dir = os.path.join("out", "dir")
        write(os.path.join(out_dir, "args.gn"), 'import("//out/a.gni")')
        write(os.path.join("out", "a.gni"), 'import("//out/b.gni")')
        write(os.path.join("out", "b.gni"), 'import("//out/a.gni")')

        with self.assertRaises(IOError):
            list(gn_helper.lines(out_dir))


if __name__ == "__main__":
    unittest.main()
