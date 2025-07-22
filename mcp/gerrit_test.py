# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for gerrit."""
import asyncio
import unittest
from unittest import mock

import gerrit
from mcp.server import fastmcp


class GerritTest(unittest.TestCase):

    def test_get_gerrit_host(self):

        async def run_test():
            with mock.patch('asyncio.create_subprocess_exec') as mock_subprocess, \
                 mock.patch('requests.get') as mock_get, \
                 mock.patch('builtins.open', mock.mock_open(read_data='test-cookie')):
                process = mock.AsyncMock()
                process.communicate.return_value = (
                    b'origin\thttps://chromium-review.googlesource.com/chromium/src (fetch)\n'
                    b'origin\thttps://chromium-review.googlesource.com/chromium/src (push)\n',
                    b'')
                mock_subprocess.return_value = process
                ctx = mock.AsyncMock()
                host = await gerrit.get_gerrit_host(ctx, '/path/to/checkout')
                self.assertEqual(host, 'chromium')

        asyncio.run(run_test())

    @mock.patch('gerrit.requests.get')
    def test_get_change_files(self, mock_get):

        async def run_test():
            with mock.patch('asyncio.create_subprocess_exec') as mock_subprocess, \
                 mock.patch('builtins.open', mock.mock_open(read_data='test-cookie')):
                # Mock get_gerrit_host
                process = mock.AsyncMock()
                process.communicate.return_value = (
                    b'origin\thttps://chromium-review.googlesource.com/chromium/src (fetch)\n',
                    b'')

                # Mock git config to get cookie file
                cookie_process = mock.AsyncMock()
                cookie_process.communicate.return_value = (
                    b'/path/to/cookiefile', b'')
                # The first call to subprocess is for get_gerrit_host, the second is for the cookie
                mock_subprocess.side_effect = [process, cookie_process]

                # Mock requests.get for change details
                mock_response_details = mock.Mock()
                mock_response_details.status_code = 200
                mock_response_details.text = ')]}\'{"current_revision": "rev1"}'

                # Mock requests.get for files list
                mock_response_files = mock.Mock()
                mock_response_files.status_code = 200
                mock_response_files.text = ')]}\'{"/COMMIT_MSG": {}, "file1.txt": {}}'

                # Mock requests.get for file content
                mock_response_content = mock.Mock()
                mock_response_content.status_code = 200
                mock_response_content.text = 'SGVsbG8sIFdvcmxkIQ=='  # "Hello, World!" in base64

                mock_get.side_effect = [
                    mock_response_details, mock_response_files,
                    mock_response_content
                ]

                ctx = mock.AsyncMock()
                files = await gerrit.get_change_files(ctx, 12345,
                                                      '/path/to/checkout')

                self.assertEqual(files, {'file1.txt': 'Hello, World!'})

        asyncio.run(run_test())


if __name__ == '__main__':
    unittest.main()
