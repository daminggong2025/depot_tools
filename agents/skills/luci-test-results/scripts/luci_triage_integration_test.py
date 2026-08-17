#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Live integration tests for luci_triage.py.

Invokes real LUCI RPCs against live endpoints. Requires `bb auth-login`.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import luci_triage

SAMPLE_BUILD_ID = "8673534943512166561"  # CI build 20406 (failing build)
SAMPLE_BUILD_ID_SUCCESS = "8673194207881096913"  # CI build 20472
SAMPLE_BUILD_NUM = 20472
SAMPLE_BUILDER = "android-13-x64-rel"
SAMPLE_CL = "8260911"
SAMPLE_TEST_QUERY = "ReadAloudPlaybackControllerTest"
SAMPLE_TEST_ID = (
    "://chrome/test\\:unit_tests!gtest::"
    "ReadAloudPlaybackControllerTest#InitializeAudioInvalidSharedMemoryReportsBadMessage"
)
SAMPLE_RESULT_NAME = (
    "invocations/task-chromium-swarm.appspot.com-7a16c4c5be070a11/tests/"
    ":%2F%2Fchrome%2Ftest%5C:unit_tests%21gtest::"
    "ReadAloudPlaybackControllerTest%23"
    "InitializeAudioInvalidSharedMemoryReportsBadMessage/results/3259653f-03474"
)


class TestLuciTriageIntegration(unittest.TestCase):
    def setUp(self):
        if not luci_triage.is_authenticated():
            self.skipTest("Not authenticated to LUCI (run 'bb auth-login')")

    def test_resolve_build_id(self):
        build_id = luci_triage.resolve_build_id(
            "chromium", "ci", SAMPLE_BUILDER, SAMPLE_BUILD_NUM
        )
        self.assertEqual(build_id, SAMPLE_BUILD_ID_SUCCESS)

    def test_get_build(self):
        data = luci_triage.get_build(f"b{SAMPLE_BUILD_ID_SUCCESS}")
        self.assertEqual(data.get("status"), "SUCCESS")
        self.assertEqual(data.get("id"), SAMPLE_BUILD_ID_SUCCESS)

    def test_find_cl_builds(self):
        builds = luci_triage.find_cl_builds(SAMPLE_CL, show_all=True)
        self.assertGreater(len(builds), 0)
        self.assertTrue(all("builder" in b and "id" in b for b in builds))

    def test_list_failures(self):
        res = luci_triage.list_failures(f"b{SAMPLE_BUILD_ID}")
        self.assertGreater(len(res), 0)
        first_task = next(iter(res))
        self.assertIn("id", res[first_task][0])
        self.assertIn("res", res[first_task][0])

    def test_fetch_log_snippet(self):
        output = luci_triage.fetch_log_snippet(SAMPLE_RESULT_NAME)
        self.assertIn("[ RUN      ]", output)

    def test_check_test(self):
        res = luci_triage.check_test(f"b{SAMPLE_BUILD_ID}", SAMPLE_TEST_QUERY)
        self.assertGreater(len(res), 0)
        self.assertTrue(
            any(
                "ReadAloudPlaybackControllerTest" in r.get("id", "")
                for r in res
            )
        )

    def test_test_history(self):
        verdicts = luci_triage.test_history(
            "chromium",
            SAMPLE_TEST_ID,
            limit=5,
            builder=SAMPLE_BUILDER,
            test_suite="unit_tests",
        )
        self.assertGreaterEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0].get("testId"), SAMPLE_TEST_ID)


if __name__ == "__main__":
    unittest.main()
