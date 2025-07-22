# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for build.py"""
import asyncio
import os
import subprocess
import sys
import unittest
from unittest import mock

# Mock modules that are not available in the test environment.
# This needs to be done before importing the module under test.
sys.modules['telemetry'] = mock.MagicMock()
sys.modules['mcp.server'] = mock.MagicMock()
sys.modules['mcp.server.fastmcp'] = mock.MagicMock()

import build


class BuildTest(unittest.TestCase):

    def test_build_success(self):
        """Tests that the build function returns 'Build suceeded' on success"""
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = 'Build output'
        mock_result.stderr = ''

        mock_ctx = mock.MagicMock()
        mock_ctx.info = mock.AsyncMock()

        with mock.patch('subprocess.run', return_value=mock_result):
            result = asyncio.run(build.build(mock_ctx, 'out/Release'))
            self.assertEqual(result, 'Build suceeded.')

    def test_build_failure_with_siso_output(self):
        """Tests that the build function returns siso_output on failure"""
        mock_result = mock.Mock()
        mock_result.returncode = 1
        mock_result.stdout = ''
        mock_result.stderr = 'Build failed'

        mock_ctx = mock.MagicMock()
        mock_ctx.info = mock.AsyncMock()

        with mock.patch('subprocess.run', return_value=mock_result), \
             mock.patch('pathlib.Path.joinpath') as mock_joinpath, \
             mock.patch('os.path.exists', return_value=True), \
             mock.patch('builtins.open', mock.mock_open(read_data='siso output')):
            mock_joinpath.return_value = 'out/Release/siso_output'
            result = asyncio.run(build.build(mock_ctx, 'out/Release'))
            self.assertEqual(result, 'Build failed: siso output')

    def test_build_failure_without_siso_output(self):
        """Tests that the build function returns stderr on failure"""
        mock_result = mock.Mock()
        mock_result.returncode = 1
        mock_result.stdout = ''
        mock_result.stderr = 'Build failed'

        mock_ctx = mock.MagicMock()
        mock_ctx.info = mock.AsyncMock()

        with mock.patch('subprocess.run', return_value=mock_result), \
             mock.patch('os.path.exists', return_value=False):
            result = asyncio.run(build.build(mock_ctx, 'out/Release'))
            self.assertEqual(result, 'Build failed')


if __name__ == '__main__':
    unittest.main()
