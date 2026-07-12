#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for git_nav_downstream."""

import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import git_nav_downstream


class GitNavDownstreamTest(unittest.TestCase):

    def setUp(self):
        mock.patch('git_nav_downstream.current_branch',
                   return_value='main').start()
        mock.patch('git_nav_downstream.branches',
                   return_value=['branch0', 'branch1', 'branch2']).start()
        mock.patch('git_nav_downstream.upstream', return_value='main').start()
        self.run_mock = mock.patch('git_nav_downstream.run').start()
        mock.patch('gclient_utils.IsEnvCog', return_value=False).start()
        self.addCleanup(mock.patch.stopall)

    @mock.patch('gclient_utils.AskForData', side_effect=['9', '1'])
    def testOutOfRangeSelectionRePrompts(self, _):
        """An out-of-range selection prints 'Invalid choice.' and asks
        again instead of crashing with IndexError."""
        with mock.patch('sys.stdout', io.StringIO()) as out:
            ret = git_nav_downstream.main([])
        self.assertEqual(ret, 0)
        self.assertIn('Invalid choice.', out.getvalue())
        self.run_mock.assert_called_once()
        self.assertEqual(self.run_mock.call_args[0], ('checkout', 'branch1'))

    @mock.patch('gclient_utils.AskForData', return_value='2')
    def testInRangeSelectionChecksOutBranch(self, _):
        """The highest valid selection still works."""
        with mock.patch('sys.stdout', io.StringIO()):
            ret = git_nav_downstream.main([])
        self.assertEqual(ret, 0)
        self.run_mock.assert_called_once()
        self.assertEqual(self.run_mock.call_args[0], ('checkout', 'branch2'))


if __name__ == '__main__':
    unittest.main()
