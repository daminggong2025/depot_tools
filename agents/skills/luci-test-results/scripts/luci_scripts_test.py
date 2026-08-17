#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for modular luci-test-results scripts."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_test_in_build
import fetch_log
import find_cl_builds
import list_failures
import luci_client
import test_history


class TestLuciClient(unittest.TestCase):
    @mock.patch("luci_client.run_prpc")
    def test_resolve_build_id(self, mock_prpc):
        mock_prpc.return_value = {"id": "8673466777718303649"}
        build_id = luci_client.resolve_build_id(
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

    @mock.patch("luci_client.run_prpc")
    def test_get_build(self, mock_prpc):
        mock_prpc.return_value = {
            "id": "8673466777718303649",
            "status": "SUCCESS",
        }
        data = luci_client.get_build("b8673466777718303649")
        self.assertEqual(data["status"], "SUCCESS")


class TestListFailures(unittest.TestCase):
    @mock.patch("list_failures.run_prpc")
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
        res = list_failures.list_failures("8673466777718303649")
        self.assertIn("task123", res)
        self.assertEqual(len(res["task123"]), 2)
        self.assertEqual(res["task123"][0]["id"], "ninja://test/Class#methodA")
        self.assertEqual(res["task123"][0]["err"], "AssertionError")

        # Ignore flaky
        res_no_flake = list_failures.list_failures(
            "8673466777718303649", ignore_flaky=True
        )
        self.assertEqual(len(res_no_flake["task123"]), 1)
        self.assertEqual(
            res_no_flake["task123"][0]["id"], "ninja://test/Class#methodA"
        )


class TestFetchLog(unittest.TestCase):
    @mock.patch("fetch_log.subprocess.check_output")
    @mock.patch("fetch_log.run_prpc")
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

        output = fetch_log.fetch_log_snippet(
            "invocations/task-123/tests/foo/results/1"
        )
        self.assertIn("AssertionError", output)


class TestCheckTestInBuild(unittest.TestCase):
    @mock.patch("check_test_in_build.run_prpc")
    def test_check_test_in_build(self, mock_prpc):
        mock_prpc.return_value = {
            "testResults": [
                {
                    "testId": "ninja://chrome/android:apk/Class#testMethod",
                    "status": "PASS",
                    "expected": True,
                }
            ]
        }
        res = check_test_in_build.check_test_in_build(
            "8673466777718303649", "testMethod"
        )
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["status"], "PASS")


class TestTestHistory(unittest.TestCase):
    @mock.patch("test_history.run_prpc")
    def test_pagination_and_aggregation(self, mock_prpc):
        # Page 1
        page1 = {
            "verdicts": [
                {
                    "testId": "ninja://test/Test#method",
                    "partitionTime": "2026-08-14T10:00:00Z",
                    "status": "UNEXPECTED",
                    "variantHash": "var1",
                }
            ],
            "nextPageToken": "token123",
        }
        # Page 2
        page2 = {
            "verdicts": [
                {
                    "testId": "ninja://test/Test#method",
                    "partitionTime": "2026-08-13T10:00:00Z",
                    "status": "EXPECTED",
                    "variantHash": "var1",
                }
            ]
        }
        mock_prpc.side_effect = [page1, page2]

        verdicts = test_history.fetch_test_history_verdicts(
            "chromium", "ninja://test/Test#method", limit=2, verbose=False
        )
        self.assertEqual(len(verdicts), 2)

        daily = test_history.aggregate_by_day(verdicts)
        self.assertEqual(daily["2026-08-14"]["FAIL"], 1)
        self.assertEqual(daily["2026-08-14"]["FAIL_RATE"], 100.0)
        self.assertEqual(daily["2026-08-13"]["PASS"], 1)
        self.assertEqual(daily["2026-08-13"]["FAIL_RATE"], 0.0)

        table = test_history.format_summary_table(
            daily, "ninja://test/Test#method"
        )
        self.assertIn("2026-08-14", table)
        self.assertIn("Failures detected starting on 2026-08-14", table)


class TestFindClBuilds(unittest.TestCase):
    @mock.patch("find_cl_builds.run_prpc")
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
        failed = find_cl_builds.find_cl_builds("8260911", patchset=1)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["builder"], "linux-rel")

        # show_all includes SUCCESS
        all_builds = find_cl_builds.find_cl_builds(
            "8260911", patchset=1, show_all=True
        )
        self.assertEqual(len(all_builds), 2)


if __name__ == "__main__":
    unittest.main()
