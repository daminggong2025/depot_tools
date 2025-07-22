# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for utr tools."""

import os
import pathlib
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(
    0,
    os.path.abspath(
        pathlib.Path(__file__).resolve().parent.parent.joinpath(
            pathlib.Path('infra_lib'))))
import utr


class UtrTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_context = AsyncMock()
        self.mock_context.info = AsyncMock()
        self.checkout = '/path/to/checkout'
        self.builder = 'test_builder'
        self.bucket = 'try'
        self.test_suite = 'all'

    @patch('utr.run_utr')
    async def test_test_builder_compile_success(self, mock_run_utr):
        expected_stdout = 'compile stdout'
        expected_stderr = 'compile stderr'
        mock_run_utr.return_value = (expected_stdout, expected_stderr)

        stdout = await utr.test_builder_compile(self.mock_context,
                                                self.checkout, self.builder,
                                                self.bucket, self.test_suite)

        self.assertEqual(stdout, expected_stdout)
        mock_run_utr.assert_called_once_with('compile', self.checkout,
                                             self.builder, self.bucket,
                                             self.test_suite)

    @patch('utr.run_utr')
    async def test_test_builder_test_success(self, mock_run_utr):
        expected_stdout = 'test stdout'
        expected_stderr = 'test stderr'
        mock_run_utr.return_value = (expected_stdout, expected_stderr)

        stdout = await utr.test_builder_test(self.mock_context, self.checkout,
                                             self.builder, self.bucket,
                                             self.test_suite)

        self.assertEqual(stdout, expected_stdout)
        mock_run_utr.assert_called_once_with('test', self.checkout,
                                             self.builder, self.bucket,
                                             self.test_suite)

    @patch('subprocess.run')
    async def test_run_utr(self, mock_subprocess_run):
        command = 'compile'
        expected_stdout = 'utr stdout'
        expected_stderr = 'utr stderr'
        mock_subprocess_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=expected_stdout,
            stderr=expected_stderr)

        stdout, stderr = utr.run_utr(command, self.checkout, self.builder,
                                     self.bucket, self.test_suite)

        self.assertEqual(stdout, expected_stdout)
        self.assertEqual(stderr, expected_stderr)
        expected_command = [
            "vpython3", "tools", "utr", "run.py", "-B", self.bucket, "-b",
            self.builder, "-t", self.test_suite, command
        ]
        mock_subprocess_run.assert_called_once_with(expected_command,
                                                    capture_output=True,
                                                    check=False,
                                                    text=True,
                                                    cwd=self.checkout)


if __name__ == '__main__':
    unittest.main()
