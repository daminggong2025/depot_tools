#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for bb_wrapper.py."""

import json
import unittest
import unittest.mock
from bb_wrapper import (
    parse_gerrit_url,
    get_patchsets,
    get_latest_build,
    list_steps,
    get_log,
)


class TestBBWrapper(unittest.TestCase):

    def test_parse_gerrit_url(self):
        # Standard URL
        host, change, ps, project = parse_gerrit_url(
            "https://chromium-review.googlesource.com"
            "/c/chromium/src/+/6072788/24")
        self.assertEqual(host, "chromium-review.googlesource.com")
        self.assertEqual(change, "6072788")
        self.assertEqual(ps, "24")
        self.assertEqual(project, "chromium/src")

        # Without patchset in URL
        host, change, ps, project = parse_gerrit_url(
            "https://chromium-review.googlesource.com/c/chromium/src/+/6072788")
        self.assertEqual(host, "chromium-review.googlesource.com")
        self.assertEqual(change, "6072788")
        self.assertIsNone(ps)

        # Short crrev URL
        host, change, ps, project = parse_gerrit_url(
            "https://crrev.com/c/6072788/24")
        self.assertEqual(host, "chromium-review.googlesource.com")
        self.assertEqual(change, "6072788")
        self.assertEqual(ps, "24")
        self.assertEqual(project, "chromium/src")

        # explicit patchset override
        host, change, ps, project = parse_gerrit_url(
            "https://crrev.com/c/6072788/24",
            explicit_patchset="25",
        )
        self.assertEqual(ps, "25")

    @unittest.mock.patch("bb_wrapper.gerrit_util.CallGerritApi")
    def test_get_patchsets_gerrit_api(self, mock_call_gerrit_api):
        mock_call_gerrit_api.return_value = {"current_revision_number": 25}

        patchsets = get_patchsets("chromium-review.googlesource.com",
                                  "chromium/src", "6072788")
        self.assertEqual(patchsets, list(range(1, 26)))

    @unittest.mock.patch("bb_wrapper.run_bb")
    def test_get_latest_build(self, mock_run_bb):
        mock_run_bb.return_value = (
            '{"id": "8765432109", "number": 1234, "status": "SUCCESS", '
            '"createTime": "2024-01-01T00:00:00Z"}\n')
        build = get_latest_build("chromium/try/linux-rel")
        self.assertEqual(build["id"], "8765432109")
        self.assertEqual(build["number"], 1234)
        self.assertEqual(build["status"], "SUCCESS")

    @unittest.mock.patch("bb_wrapper.run_bb")
    def test_list_steps(self, mock_run_bb):
        mock_run_bb.return_value = json.dumps({
            "builder": {
                "builder": "linux-rel"
            },
            "number":
            1234,
            "id":
            "8765432109",
            "status":
            "FAILURE",
            "steps": [{
                "name": "compile",
                "status": "SUCCESS",
                "logs": [{
                    "name": "stdout"
                }]
            }, {
                "name": "browser_tests",
                "status": "FAILURE",
                "logs": [{
                    "name": "failure_summary"
                }]
            }]
        })
        list_steps("8765432109")

    @unittest.mock.patch("bb_wrapper.run_bb")
    @unittest.mock.patch("builtins.open", new_callable=unittest.mock.mock_open)
    @unittest.mock.patch("os.path.getsize", return_value=100)
    def test_get_log(self, _mock_getsize, _mock_open, mock_run_bb):
        mock_run_bb.return_value = "line1\nline2\n"
        get_log("8765432109", "compile", "stdout", "/tmp/bb_log.log")


if __name__ == "__main__":
    unittest.main()
