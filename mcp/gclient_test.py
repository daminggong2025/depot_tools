# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for gclient tools."""

import os
import pathlib
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, call, patch

sys.path.insert(
    0,
    os.path.abspath(
        pathlib.Path(__file__).resolve().parent.parent.joinpath(
            pathlib.Path('infra_lib'))))
import gclient


class GclientTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_context = AsyncMock()
        self.mock_context.info = AsyncMock()
        self.checkout = '/path/to/checkout'

    @patch('subprocess.run')
    async def test_sync_checkout_success(self, mock_subprocess_run):
        revision = 'test_revision'
        mock_subprocess_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='', stderr='')

        await gclient.sync_checkout(self.mock_context, self.checkout, revision)

        calls = [
            call(['git', 'checkout', revision],
                 capture_output=True,
                 check=False,
                 text=True,
                 cwd=self.checkout),
            call(['gclient', 'sync', '-f'],
                 capture_output=True,
                 check=False,
                 text=True,
                 cwd=self.checkout)
        ]
        mock_subprocess_run.assert_has_calls(calls)

    @patch('subprocess.run')
    async def test_sync_checkout_head(self, mock_subprocess_run):
        mock_subprocess_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='', stderr='')

        await gclient.sync_checkout(self.mock_context, self.checkout)

        calls = [
            call(['git', 'checkout', 'HEAD'],
                 capture_output=True,
                 check=False,
                 text=True,
                 cwd=self.checkout),
            call(['gclient', 'sync', '-f'],
                 capture_output=True,
                 check=False,
                 text=True,
                 cwd=self.checkout)
        ]
        mock_subprocess_run.assert_has_calls(calls)


if __name__ == '__main__':
    unittest.main()
