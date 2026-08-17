#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit and integration tests for modular luci-test-results scripts.

Can be run in two modes:
1. Unit test mode (default): Uses mocks for hermetic execution.
   vpython3 luci_scripts_test.py
2. Live integration test mode: Invokes real LUCI RPCs against live endpoints.
   INTEGRATION_TEST=1 vpython3 luci_scripts_test.py
"""

import io
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

# Realistic reference fixtures used for both mock and live modes
SAMPLE_BUILD_ID = "8673534943512166561"
SAMPLE_BUILD_ID_SUCCESS = "8673194207881096913"
SAMPLE_BUILD_ID_2 = "8673446654073668161"
SAMPLE_BUILD_ID_3 = "8673446654073668177"
SAMPLE_BUILD_NUM = 20472
SAMPLE_BUILDER = "android-13-x64-rel"
SAMPLE_CL = "8260911"
SAMPLE_TASK_ID = "chromium-swarm.appspot.com-7a16c4c5be070a11"
SAMPLE_TEST_QUERY = "ReadAloudPlaybackControllerTest"
SAMPLE_TEST_ID = (
    "://chrome/test\\:unit_tests!gtest::"
    "ReadAloudPlaybackControllerTest#InitializeAudioInvalidSharedMemoryReportsBadMessage"
)
SAMPLE_RESULT_NAME = (
    f"invocations/task-{SAMPLE_TASK_ID}/tests/"
    ":%2F%2Fchrome%2Ftest%5C:unit_tests%21gtest::"
    "ReadAloudPlaybackControllerTest%23"
    "InitializeAudioInvalidSharedMemoryReportsBadMessage/results/3259653f-03474"
)

RUN_INTEGRATION = os.environ.get("INTEGRATION_TEST") in ("1", "true", "True")

# Save real functions for live passthrough
_REAL_RUN_PRPC = luci_client.run_prpc
_REAL_CHECK_OUTPUT = fetch_log.subprocess.check_output
_REAL_RUN = luci_client.subprocess.run
_REAL_POPEN = luci_client.subprocess.Popen


def patch_module_prpc(module_name, **kwargs):
    if RUN_INTEGRATION:
        return mock.patch(f"{module_name}.run_prpc", side_effect=_REAL_RUN_PRPC)
    return mock.patch(f"{module_name}.run_prpc", **kwargs)


def patch_popen(**kwargs):
    if RUN_INTEGRATION:
        return mock.patch(
            "luci_client.subprocess.Popen", side_effect=_REAL_POPEN
        )
    return mock.patch("luci_client.subprocess.Popen", **kwargs)


def patch_run(**kwargs):
    if RUN_INTEGRATION:
        return mock.patch("luci_client.subprocess.run", side_effect=_REAL_RUN)
    return mock.patch("luci_client.subprocess.run", **kwargs)


class TestLuciClient(unittest.TestCase):
    def setUp(self):
        if RUN_INTEGRATION and not luci_client.is_authenticated():
            self.skipTest("Not authenticated to LUCI (run 'bb auth-login')")

    @patch_popen()
    def test_run_prpc_success(self, mock_popen):
        if not RUN_INTEGRATION:
            mock_proc = mock.MagicMock()
            mock_proc.communicate.return_value = (
                f'{{"id": "{SAMPLE_BUILD_ID_SUCCESS}"}}',
                "",
            )
            mock_proc.returncode = 0
            mock_popen.return_value.__enter__.return_value = mock_proc

        res = luci_client.run_prpc(
            "cr-buildbucket.appspot.com",
            "buildbucket.v2.Builds.GetBuild",
            {"id": SAMPLE_BUILD_ID_SUCCESS},
        )
        self.assertIsNotNone(res)
        self.assertEqual(res.get("id"), SAMPLE_BUILD_ID_SUCCESS)

    @mock.patch("sys.stderr", new_callable=io.StringIO)
    @patch_popen()
    def test_run_prpc_error(self, mock_popen, mock_stderr):
        if not RUN_INTEGRATION:
            mock_proc = mock.MagicMock()
            mock_proc.communicate.return_value = ("", "rpc error")
            mock_proc.returncode = 1
            mock_popen.return_value.__enter__.return_value = mock_proc

        res = luci_client.run_prpc(
            "cr-buildbucket.appspot.com",
            "buildbucket.v2.Builds.GetBuild",
            {"id": "0"},
        )
        self.assertIsNone(res)

    @patch_run()
    def test_is_authenticated(self, mock_run):
        if not RUN_INTEGRATION:
            mock_run.return_value.returncode = 0
            self.assertTrue(luci_client.is_authenticated())
            mock_run.return_value.returncode = 1
            self.assertFalse(luci_client.is_authenticated())
            mock_run.side_effect = FileNotFoundError()
            self.assertFalse(luci_client.is_authenticated())
        else:
            self.assertTrue(luci_client.is_authenticated())

    @mock.patch("luci_client.run_prpc")
    def test_resolve_build_id(self, mock_prpc):
        if RUN_INTEGRATION:
            mock_prpc.side_effect = _REAL_RUN_PRPC
        else:
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
        if RUN_INTEGRATION:
            mock_prpc.side_effect = _REAL_RUN_PRPC
        else:
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
    def setUp(self):
        if RUN_INTEGRATION and not luci_client.is_authenticated():
            self.skipTest("Not authenticated to LUCI (run 'bb auth-login')")

    @patch_module_prpc("find_cl_builds")
    def test_find_cl_builds_with_patchset(self, mock_prpc):
        if not RUN_INTEGRATION:
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
        all_builds = find_cl_builds.find_cl_builds(
            SAMPLE_CL, patchset=1, show_all=True
        )
        self.assertGreater(len(all_builds), 0)
        self.assertLessEqual(len(failed), len(all_builds))
        if not RUN_INTEGRATION:
            self.assertEqual(len(failed), 1)
            self.assertEqual(failed[0]["builder"], "linux-rel")
            self.assertEqual(len(all_builds), 3)

    @mock.patch("find_cl_builds.subprocess.check_output")
    @mock.patch("find_cl_builds.run_prpc")
    def test_find_cl_builds_default_patchset(
        self, mock_prpc, mock_check_output
    ):
        if RUN_INTEGRATION:
            mock_prpc.side_effect = _REAL_RUN_PRPC
            mock_check_output.side_effect = _REAL_CHECK_OUTPUT
        else:
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
        self.assertGreaterEqual(len(builds), 0)
        if not RUN_INTEGRATION:
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
    def setUp(self):
        if RUN_INTEGRATION and not luci_client.is_authenticated():
            self.skipTest("Not authenticated to LUCI (run 'bb auth-login')")

    @patch_module_prpc("list_failures")
    def test_list_failures_grouped_by_task(self, mock_prpc):
        if not RUN_INTEGRATION:
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
        self.assertGreater(len(res), 0)
        first_task = next(iter(res))
        self.assertIn("id", res[first_task][0])
        self.assertIn("res", res[first_task][0])

        if not RUN_INTEGRATION:
            self.assertIn(SAMPLE_TASK_ID, res)
            self.assertEqual(len(res[SAMPLE_TASK_ID]), 2)
            self.assertEqual(res[SAMPLE_TASK_ID][0]["id"], SAMPLE_TEST_ID)
            self.assertEqual(res[SAMPLE_TASK_ID][0]["err"], "AssertionError")

            res_no_flake = list_failures.list_failures(
                SAMPLE_BUILD_ID, ignore_flaky=True
            )
            self.assertEqual(len(res_no_flake[SAMPLE_TASK_ID]), 1)

            res_exonerated = list_failures.list_failures(
                SAMPLE_BUILD_ID, include_exonerated=True
            )
            self.assertEqual(len(res_exonerated[SAMPLE_TASK_ID]), 3)

            res_limit = list_failures.list_failures(SAMPLE_BUILD_ID, limit=1)
            self.assertEqual(len(res_limit[SAMPLE_TASK_ID]), 1)

    @patch_module_prpc("list_failures")
    def test_list_failures_pagination(self, mock_prpc):
        if RUN_INTEGRATION:
            res = list_failures.list_failures(SAMPLE_BUILD_ID)
            self.assertGreater(len(res), 0)
        else:
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
    def setUp(self):
        if RUN_INTEGRATION and not luci_client.is_authenticated():
            self.skipTest("Not authenticated to LUCI (run 'bb auth-login')")

    @mock.patch("fetch_log.subprocess.check_output")
    @mock.patch("fetch_log.run_prpc")
    def test_fetch_log_snippet_regex(self, mock_prpc, mock_check_output):
        if RUN_INTEGRATION:
            mock_prpc.side_effect = _REAL_RUN_PRPC
            mock_check_output.side_effect = _REAL_CHECK_OUTPUT
        else:
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
        if RUN_INTEGRATION:
            mock_prpc.side_effect = _REAL_RUN_PRPC
            mock_check_output.side_effect = _REAL_CHECK_OUTPUT
        else:
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
        self.assertIsNotNone(output)
        self.assertGreater(len(output), 0)

    @mock.patch("sys.stderr", new_callable=io.StringIO)
    @mock.patch("fetch_log.is_authenticated")
    @mock.patch("fetch_log.run_prpc")
    def test_fetch_log_snippet_no_artifacts(
        self, mock_prpc, mock_is_authenticated, mock_stderr
    ):
        if RUN_INTEGRATION:
            output = fetch_log.fetch_log_snippet("invocations/nonexistent")
            self.assertIn("No artifacts found", output)
        else:
            mock_prpc.return_value = {}
            mock_is_authenticated.return_value = False
            output = fetch_log.fetch_log_snippet(
                "invocations/task-123/tests/foo/results/1"
            )
            self.assertIn("No artifacts found", output)
            self.assertIn("bb auth-login", output)


class TestCheckTestInBuild(unittest.TestCase):
    def setUp(self):
        if RUN_INTEGRATION and not luci_client.is_authenticated():
            self.skipTest("Not authenticated to LUCI (run 'bb auth-login')")

    @patch_module_prpc("check_test_in_build")
    def test_check_test_in_build(self, mock_prpc):
        if not RUN_INTEGRATION:
            mock_prpc.return_value = {
                "testResults": [
                    {
                        "testId": SAMPLE_TEST_ID,
                        "status": "PASS",
                        "expected": True,
                    }
                ]
            }

        res = check_test_in_build.check_test_in_build(
            f"b{SAMPLE_BUILD_ID}", SAMPLE_TEST_QUERY
        )
        self.assertGreater(len(res), 0)
        self.assertTrue(
            any(
                "ReadAloudPlaybackControllerTest" in r.get("id", "")
                for r in res
            )
        )
        if not RUN_INTEGRATION:
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
    def setUp(self):
        if RUN_INTEGRATION and not luci_client.is_authenticated():
            self.skipTest("Not authenticated to LUCI (run 'bb auth-login')")

    @mock.patch("test_history.run_prpc")
    def test_pagination_and_aggregation(self, mock_prpc):
        if RUN_INTEGRATION:
            mock_prpc.side_effect = _REAL_RUN_PRPC
            verdicts = test_history.fetch_test_history_verdicts(
                "chromium",
                SAMPLE_TEST_ID,
                limit=5,
                builder=SAMPLE_BUILDER,
                test_suite="unit_tests",
                verbose=False,
            )
            self.assertGreaterEqual(len(verdicts), 1)
            daily = test_history.aggregate_by_day(verdicts)
            self.assertGreaterEqual(len(daily), 1)
            table = test_history.format_summary_table(daily, SAMPLE_TEST_ID)
            self.assertIn("Test History Aggregate Summary", table)
        else:
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


if __name__ == "__main__":
    unittest.main()
