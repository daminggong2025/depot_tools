#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for modular luci-test-results scripts."""

import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_test
import fetch_log
import find_cl_builds
import list_failures
import luci_client
import test_history

SAMPLE_BUILD_ID = "8000000000000000001"
SAMPLE_BUILD_ID_SUCCESS = "8000000000000000002"
SAMPLE_BUILD_ID_2 = "8000000000000000003"
SAMPLE_BUILD_ID_3 = "8000000000000000004"
SAMPLE_BUILD_NUM = 12345
SAMPLE_BUILDER = "android-13-x64-rel"
SAMPLE_CL = "1234567"
SAMPLE_TASK_ID = "chromium-swarm.appspot.com-sampletaskid123"
SAMPLE_TEST_QUERY = "JSONReaderTest"
SAMPLE_TEST_ID = (
    "://base\\:base_unittests!gtest::JSONReaderTest#ASCIIControlCodes"
)
SAMPLE_RESULT_NAME = (
    f"invocations/task-{SAMPLE_TASK_ID}/tests/"
    ":%2F%2Fbase%5C:base_unittests%21gtest::"
    "JSONReaderTest%23ASCIIControlCodes/results/sample-result-id"
)


class TestLuciClient(unittest.TestCase):
    @mock.patch("luci_client.subprocess.Popen")
    def test_run_prpc_success(self, mock_popen):
        mock_proc = mock.MagicMock()
        mock_proc.communicate.return_value = ('{"foo": "bar"}', "")
        mock_proc.returncode = 0
        mock_popen.return_value.__enter__.return_value = mock_proc

        res = luci_client.run_prpc("service.example.com", "Method", {"k": "v"})
        self.assertEqual(res, {"foo": "bar"})

    @mock.patch("sys.stderr", new_callable=io.StringIO)
    @mock.patch("luci_client.subprocess.Popen")
    def test_run_prpc_error(self, mock_popen, mock_stderr):
        mock_proc = mock.MagicMock()
        mock_proc.communicate.return_value = ("", "rpc error")
        mock_proc.returncode = 1
        mock_popen.return_value.__enter__.return_value = mock_proc

        res = luci_client.run_prpc("service.example.com", "Method", {"k": "v"})
        self.assertIsNone(res)

    @mock.patch("luci_client.subprocess.run")
    def test_is_authenticated(self, mock_run):
        mock_run.return_value.returncode = 0
        self.assertTrue(luci_client.is_authenticated())

        mock_run.return_value.returncode = 1
        self.assertFalse(luci_client.is_authenticated())

        mock_run.side_effect = FileNotFoundError()
        self.assertFalse(luci_client.is_authenticated())

    @mock.patch("luci_client.run_prpc")
    def test_resolve_build_id(self, mock_prpc):
        mock_prpc.return_value = {"id": SAMPLE_BUILD_ID_SUCCESS}
        build_id = luci_client.resolve_build_id(
            "chromium", "ci", SAMPLE_BUILDER, SAMPLE_BUILD_NUM
        )
        self.assertEqual(build_id, SAMPLE_BUILD_ID_SUCCESS)
        mock_prpc.assert_called_once_with(
            "cr-buildbucket.appspot.com",
            "buildbucket.v2.Builds.GetBuild",
            {
                "builder": {
                    "project": "chromium",
                    "bucket": "ci",
                    "builder": SAMPLE_BUILDER,
                },
                "buildNumber": SAMPLE_BUILD_NUM,
            },
        )

    @mock.patch("luci_client.run_prpc")
    def test_get_build(self, mock_prpc):
        mock_prpc.return_value = {
            "id": SAMPLE_BUILD_ID_SUCCESS,
            "status": "SUCCESS",
        }
        data = luci_client.get_build(f"b{SAMPLE_BUILD_ID_SUCCESS}")
        self.assertEqual(data["status"], "SUCCESS")
        mock_prpc.assert_called_once_with(
            "cr-buildbucket.appspot.com",
            "buildbucket.v2.Builds.GetBuild",
            {
                "id": SAMPLE_BUILD_ID_SUCCESS,
                "mask": {
                    "fields": "id,builder,number,status,summaryMarkdown,output"
                },
            },
        )


class TestFindClBuilds(unittest.TestCase):
    @mock.patch("find_cl_builds.run_prpc")
    def test_find_cl_builds_with_patchset(self, mock_prpc):
        mock_prpc.return_value = {
            "builds": [
                {
                    "builder": {"builder": "linux-rel"},
                    "status": "FAILURE",
                    "id": SAMPLE_BUILD_ID,
                },
                {
                    "builder": {"builder": "mac-rel"},
                    "status": "STARTED",
                    "id": SAMPLE_BUILD_ID_2,
                },
                {
                    "builder": {"builder": "android-arm64-rel"},
                    "status": "SUCCESS",
                    "id": SAMPLE_BUILD_ID_3,
                },
            ]
        }
        failed = find_cl_builds.find_cl_builds(SAMPLE_CL, patchset=1)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["builder"], "linux-rel")

        all_builds = find_cl_builds.find_cl_builds(
            SAMPLE_CL, patchset=1, show_all=True
        )
        self.assertEqual(len(all_builds), 3)

    @mock.patch("find_cl_builds.subprocess.check_output")
    @mock.patch("find_cl_builds.run_prpc")
    def test_find_cl_builds_default_patchset(
        self, mock_prpc, mock_check_output
    ):
        gerrit_json = (
            ")]}'\n"
            '{"current_revision": "rev1", "revisions": {"rev1": {"_number": 3}}}'
        )
        mock_check_output.return_value = gerrit_json.encode("utf-8")
        mock_prpc.return_value = {
            "builds": [
                {
                    "builder": {"builder": "linux-rel"},
                    "status": "FAILURE",
                    "id": SAMPLE_BUILD_ID,
                }
            ]
        }
        builds = find_cl_builds.find_cl_builds(SAMPLE_CL)
        self.assertEqual(len(builds), 1)
        mock_prpc.assert_called_once_with(
            "cr-buildbucket.appspot.com",
            "buildbucket.v2.Builds.SearchBuilds",
            {
                "predicate": {
                    "gerritChanges": [
                        {
                            "host": "chromium-review.googlesource.com",
                            "change": int(SAMPLE_CL),
                            "patchset": 3,
                        }
                    ]
                }
            },
        )


class TestListFailures(unittest.TestCase):
    @mock.patch("list_failures.run_prpc")
    def test_list_failures_grouped_by_task(self, mock_prpc):
        mock_prpc.return_value = {
            "testVariants": [
                {
                    "testId": SAMPLE_TEST_ID,
                    "status": "UNEXPECTED",
                    "results": [
                        {
                            "result": {
                                "name": SAMPLE_RESULT_NAME,
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
                                "name": f"invocations/task-{SAMPLE_TASK_ID}/tests/testB/results/1",
                                "status": "FAIL",
                            }
                        }
                    ],
                },
                {
                    "testId": "ninja://test/Class#methodC",
                    "status": "EXONERATED",
                    "results": [
                        {
                            "result": {
                                "name": f"invocations/task-{SAMPLE_TASK_ID}/tests/testC/results/1",
                                "status": "FAIL",
                            }
                        }
                    ],
                },
            ]
        }

        res = list_failures.list_failures(f"b{SAMPLE_BUILD_ID}")
        self.assertIn(SAMPLE_TASK_ID, res)
        self.assertEqual(len(res[SAMPLE_TASK_ID]), 2)
        self.assertEqual(res[SAMPLE_TASK_ID][0]["id"], SAMPLE_TEST_ID)
        self.assertEqual(res[SAMPLE_TASK_ID][0]["err"], "AssertionError")

        res_no_flake = list_failures.list_failures(
            SAMPLE_BUILD_ID, ignore_flaky=True
        )
        self.assertEqual(len(res_no_flake[SAMPLE_TASK_ID]), 1)
        self.assertEqual(res_no_flake[SAMPLE_TASK_ID][0]["id"], SAMPLE_TEST_ID)

        res_exonerated = list_failures.list_failures(
            SAMPLE_BUILD_ID, include_exonerated=True
        )
        self.assertEqual(len(res_exonerated[SAMPLE_TASK_ID]), 3)

        res_limit = list_failures.list_failures(SAMPLE_BUILD_ID, limit=1)
        self.assertEqual(len(res_limit[SAMPLE_TASK_ID]), 1)

    @mock.patch("list_failures.run_prpc")
    def test_list_failures_pagination(self, mock_prpc):
        mock_prpc.side_effect = [
            {
                "testVariants": [
                    {
                        "testId": "ninja://test/Class#method1",
                        "status": "UNEXPECTED",
                        "results": [
                            {
                                "result": {
                                    "name": "invocations/task-task1/tests/test1/results/1",
                                    "status": "FAIL",
                                }
                            }
                        ],
                    }
                ],
                "nextPageToken": "token123",
            },
            {
                "testVariants": [
                    {
                        "testId": "ninja://test/Class#method2",
                        "status": "UNEXPECTED",
                        "results": [
                            {
                                "result": {
                                    "name": "invocations/task-task2/tests/test2/results/1",
                                    "status": "FAIL",
                                }
                            }
                        ],
                    }
                ]
            },
        ]
        res = list_failures.list_failures(SAMPLE_BUILD_ID)
        self.assertEqual(len(res), 2)
        self.assertIn("task1", res)
        self.assertIn("task2", res)


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
            "[ RUN      ] SomeTest.Foo\n"
            "Line 2\n"
            "org.junit.ComparisonFailure: expected:<[a]> but was:<[b]>\n"
            "  at java.lang.AssertionError\n"
            "[  FAILED  ] SomeTest.Foo\n"
        )
        mock_check_output.return_value = sample_log.encode("utf-8")

        output = fetch_log.fetch_log_snippet(SAMPLE_RESULT_NAME)
        self.assertIn("[ RUN      ]", output)

    @mock.patch("fetch_log.subprocess.check_output")
    @mock.patch("fetch_log.run_prpc")
    def test_fetch_log_snippet_raw(self, mock_prpc, mock_check_output):
        mock_prpc.return_value = {
            "artifacts": [
                {
                    "artifactId": "stdout",
                    "fetchUrl": "https://logs.example.com/stdout.txt",
                }
            ]
        }
        sample_log = "Raw log output without matches"
        mock_check_output.return_value = sample_log.encode("utf-8")

        output = fetch_log.fetch_log_snippet(SAMPLE_RESULT_NAME, raw=True)
        self.assertEqual(output, sample_log)

    @mock.patch("fetch_log.is_authenticated")
    @mock.patch("fetch_log.run_prpc")
    def test_fetch_log_snippet_no_artifacts(
        self, mock_prpc, mock_is_authenticated
    ):
        mock_is_authenticated.return_value = False

        for empty_val in ({}, {"artifacts": []}, None):
            mock_prpc.return_value = empty_val
            output = fetch_log.fetch_log_snippet(
                "invocations/task-123/tests/foo/results/1"
            )
            self.assertIn("No artifacts found", output)
            self.assertIn("bb auth-login", output)


class TestCheckTest(unittest.TestCase):
    @mock.patch("check_test.run_prpc")
    def test_check_test(self, mock_prpc):
        mock_prpc.return_value = {
            "testResults": [
                {
                    "testId": SAMPLE_TEST_ID,
                    "status": "PASS",
                    "expected": True,
                }
            ]
        }
        res = check_test.check_test(f"b{SAMPLE_BUILD_ID}", SAMPLE_TEST_QUERY)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["status"], "PASS")
        mock_prpc.assert_called_once_with(
            "results.api.luci.app",
            "luci.resultdb.v1.ResultDB.QueryTestResults",
            {
                "invocations": [f"invocations/build-{SAMPLE_BUILD_ID}"],
                "predicate": {
                    "testIdRegexp": f".*{SAMPLE_TEST_QUERY}.*",
                    "expectancy": "ALL",
                },
                "pageSize": 1000,
            },
        )


class TestTestHistory(unittest.TestCase):
    @mock.patch("test_history.run_prpc")
    def test_pagination_and_aggregation(self, mock_prpc):
        # Page 1
        page1 = {
            "verdicts": [
                {
                    "testId": SAMPLE_TEST_ID,
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
                    "testId": SAMPLE_TEST_ID,
                    "partitionTime": "2026-08-13T10:00:00Z",
                    "status": "EXPECTED",
                    "variantHash": "var1",
                }
            ]
        }
        mock_prpc.side_effect = [page1, page2]

        verdicts = test_history.fetch_test_history_verdicts(
            "chromium", SAMPLE_TEST_ID, limit=2, verbose=False
        )
        self.assertEqual(len(verdicts), 2)

        daily = test_history.aggregate_by_day(verdicts)
        self.assertEqual(daily["2026-08-14"]["FAIL"], 1)
        self.assertEqual(daily["2026-08-14"]["FAIL_RATE"], 100.0)
        self.assertEqual(daily["2026-08-13"]["PASS"], 1)
        self.assertEqual(daily["2026-08-13"]["FAIL_RATE"], 0.0)

        table = test_history.format_summary_table(daily, SAMPLE_TEST_ID)
        self.assertIn("2026-08-14", table)
        self.assertIn("Failures detected starting on 2026-08-14", table)

    @mock.patch("test_history.run_prpc")
    def test_query_tests(self, mock_prpc):
        mock_prpc.return_value = {"testIds": [SAMPLE_TEST_ID]}
        ids = test_history.query_tests("chromium", "JSONReaderTest")
        self.assertEqual(ids, [SAMPLE_TEST_ID])
        mock_prpc.assert_called_once_with(
            "analysis.api.luci.app",
            "luci.analysis.v1.TestHistory.QueryTests",
            {
                "project": "chromium",
                "testIdSubstring": "JSONReaderTest",
            },
        )


if __name__ == "__main__":
    unittest.main()
