#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for luci_triage.py."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import luci_triage


class TestLuciTriage(unittest.TestCase):
    @mock.patch("luci_triage.run_prpc")
    def test_resolve_build_id(self, mock_prpc):
        mock_prpc.return_value = {"id": "8673466777718303649"}
        build_id = luci_triage.resolve_build_id(
            "chromium", "ci", "android-13-x64-rel", 20418
        )
        self.assertEqual(build_id, "8673466777718303649")
        mock_prpc.assert_called_once_with(
            "cr-buildbucket.appspot.com",
            "buildbucket.v2.Builds.GetBuild",
            {
                "builder": {
                    "project": "chromium",
                    "bucket": "ci",
                    "builder": "android-13-x64-rel",
                },
                "buildNumber": 20418,
            },
        )

    @mock.patch("luci_triage.run_prpc")
    def test_get_build(self, mock_prpc):
        mock_prpc.return_value = {
            "id": "8673466777718303649",
            "status": "SUCCESS",
        }
        data = luci_triage.get_build("b8673466777718303649")
        self.assertEqual(data["status"], "SUCCESS")

    @mock.patch("luci_triage.run_prpc")
    def test_find_cl_builds(self, mock_prpc):
        mock_prpc.return_value = {
            "builds": [
                {
                    "builder": {"builder": "linux-rel"},
                    "status": "FAILURE",
                    "id": "8673459280982416865",
                },
                {
                    "builder": {"builder": "android-arm64-rel"},
                    "status": "SUCCESS",
                    "id": "8673459280266077729",
                },
            ]
        }
        # Default filters out SUCCESS
        failed = luci_triage.find_cl_builds("8260911", patchset=1)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["builder"], "linux-rel")

        # show_all includes SUCCESS
        all_builds = luci_triage.find_cl_builds(
            "8260911", patchset=1, show_all=True
        )
        self.assertEqual(len(all_builds), 2)

    @mock.patch("luci_triage.run_prpc")
    def test_list_failures_grouped_by_task(self, mock_prpc):
        mock_prpc.return_value = {
            "testVariants": [
                {
                    "testId": "ninja://test/Class#methodA",
                    "status": "UNEXPECTED",
                    "results": [
                        {
                            "result": {
                                "name": "invocations/task-task123/tests/testA/results/1",
                                "status": "FAIL",
                                "failureReason": {
                                    "primaryErrorMessage": "AssertionError"
                                },
                            }
                        }
                    ],
                },
                {
                    "testId": "ninja://test/Class#methodB",
                    "status": "FLAKY",
                    "results": [
                        {
                            "result": {
                                "name": "invocations/task-task123/tests/testB/results/1",
                                "status": "FAIL",
                            }
                        }
                    ],
                },
            ]
        }

        # All failures
        res = luci_triage.list_failures("8673466777718303649")
        self.assertIn("task123", res)
        self.assertEqual(len(res["task123"]), 2)
        self.assertEqual(res["task123"][0]["id"], "ninja://test/Class#methodA")
        self.assertEqual(res["task123"][0]["err"], "AssertionError")

        # Ignore flaky
        res_no_flake = luci_triage.list_failures(
            "8673466777718303649", ignore_flaky=True
        )
        self.assertEqual(len(res_no_flake["task123"]), 1)
        self.assertEqual(
            res_no_flake["task123"][0]["id"], "ninja://test/Class#methodA"
        )

    @mock.patch("luci_triage.subprocess.check_output")
    @mock.patch("luci_triage.run_prpc")
    def test_fetch_log_snippet_regex(self, mock_prpc, mock_check_output):
        mock_prpc.return_value = {
            "artifacts": [
                {
                    "artifactId": "test_log",
                    "fetchUrl": "https://logs.example.com/log.txt",
                }
            ]
        }
        sample_log = (
            "Line 1\n"
            "Line 2\n"
            "org.junit.ComparisonFailure: expected:<[a]> but was:<[b]>\n"
            "  at java.lang.AssertionError\n"
            "Line 5\n"
        )
        mock_check_output.return_value = sample_log.encode("utf-8")

        output = luci_triage.fetch_log_snippet(
            "invocations/task-123/tests/foo/results/1"
        )
        self.assertIn("AssertionError", output)

    @mock.patch("luci_triage.run_prpc")
    def test_check_test(self, mock_prpc):
        mock_prpc.return_value = {
            "testResults": [
                {
                    "testId": "ninja://chrome/android:apk/Class#testMethod",
                    "status": "PASS",
                    "expected": True,
                }
            ]
        }
        res = luci_triage.check_test("8673466777718303649", "testMethod")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
