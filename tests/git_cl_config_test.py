#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for git_cl_config.py."""

import os
import sys
import unittest
from unittest import mock

# Add parent directory to sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import git_cl_config  # noqa: E402


class GitClConfigTest(unittest.TestCase):
    def testSettingsDefaults(self):
        s = git_cl_config.Settings()
        self.assertIsNone(s.cc)
        self.assertIsNone(s.root)
        self.assertFalse(s.updated)

    @mock.patch("scm.GIT.GetCheckoutRoot", return_value="/fake/root")
    def testGetRoot(self, mock_root):
        s = git_cl_config.Settings()
        self.assertEqual(os.path.realpath("/fake/root"), s.GetRoot())

    def testSplitArgsByCmdLineLimit(self):
        args = ["a" * 100 for _ in range(500)]
        chunks = list(git_cl_config._SplitArgsByCmdLineLimit(args))
        self.assertTrue(len(chunks) >= 1)
        self.assertEqual(sum(len(c) for c in chunks), 500)


if __name__ == "__main__":
    unittest.main()
