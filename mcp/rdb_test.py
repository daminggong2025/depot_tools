# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for rdb tools."""

import json
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
import rdb


class RdbTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_context = AsyncMock()
        self.mock_context.info = AsyncMock()

    @patch('subprocess.run')
    async def test_get_test_failures_success(self, mock_subprocess_run):
        build_id = '12345'
        expected_output = '{"testResults": []}'
        mock_subprocess_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=expected_output, stderr='')

        output = await rdb.get_test_failures(self.mock_context, build_id)

        self.assertEqual(output, expected_output)
        expected_command = [
            'prpc',
            'call',
            'results.api.cr',
            'luci.resultdb.v1.ResultDB.QueryTestResults',
        ]
        expected_request = {
            'invocations': [f'invocations/build-{build_id}'],
            'predicate': {
                'expectancy': 'VARIANTS_WITH_ONLY_UNEXPECTED_RESULTS'
            }
        }
        mock_subprocess_run.assert_called_once_with(
            expected_command,
            capture_output=True,
            input=json.dumps(expected_request),
            check=True,
            text=True)

    @patch('subprocess.run')
    async def test_get_test_failures_exception(self, mock_subprocess_run):
        build_id = '12345'
        mock_subprocess_run.side_effect = Exception('prpc call failed')

        with self.assertRaises(Exception) as context:
            await rdb.get_test_failures(self.mock_context, build_id)

        self.assertIn('prpc call failed', str(context.exception))


if __name__ == '__main__':
    unittest.main()
