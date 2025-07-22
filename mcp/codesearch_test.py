# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for codesearch tools."""

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
import codesearch


class CodesearchTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_context = AsyncMock()
        self.mock_context.info = AsyncMock()

    @patch('subprocess.run')
    async def test_code_search_success(self, mock_subprocess_run):
        expression = 'test_expression'
        max_results = 20
        context = 10
        expected_output = 'some search results'
        mock_subprocess_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=expected_output, stderr='')

        output = await codesearch.code_search(self.mock_context, expression,
                                              max_results, context)

        self.assertEqual(output, expected_output)
        expected_command = [
            'cs',
            f'--max_num_results={max_results}',
            f'--context={context}',
            '--',
            'git:chromium/chromium/src@main',
            'case:y',
            expression,
        ]
        mock_subprocess_run.assert_called_once_with(expected_command,
                                                    capture_output=True,
                                                    check=True,
                                                    text=True)

    @patch('subprocess.run')
    async def test_code_search_exception(self, mock_subprocess_run):
        expression = 'test_expression'
        mock_subprocess_run.side_effect = Exception('CS call failed')

        result = await codesearch.code_search(self.mock_context, expression)

        self.assertIn('Exception calling prpc', result)
        self.assertIn('CS call failed', result)


if __name__ == '__main__':
    unittest.main()
