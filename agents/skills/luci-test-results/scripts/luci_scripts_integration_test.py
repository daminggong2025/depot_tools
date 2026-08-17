#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Live integration tests for modular luci-test-results scripts.

Dynamically discovers fresh CI builds and tests against live endpoints.
Prints structured test results for LLM agent confirmation.
Requires `bb auth-login`.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_test_in_build
import fetch_log
import find_cl_builds
import list_failures
import luci_client
import test_history

SAMPLE_BUILDER = "android-13-x64-rel"
SAMPLE_CL = "8260911"
SAMPLE_TEST_QUERY = "ExampleFreshCtaTest"
SAMPLE_TEST_ID = (
    "://chrome/android\\:chrome_public_test_apk!junit:"
    "org.chromium.chrome.browser:ExampleFreshCtaTest#testStartOnBlankPage"
)


class BaseLuciIntegrationTestCase(unittest.TestCase):
    success_build_id = None
    success_build_num = None
    failure_build_id = None
    failure_build_num = None
    _discovery_done = False

    @classmethod
    def setUpClass(cls):
        if BaseLuciIntegrationTestCase._discovery_done:
            cls.success_build_id = BaseLuciIntegrationTestCase.success_build_id
            cls.success_build_num = (
                BaseLuciIntegrationTestCase.success_build_num
            )
            cls.failure_build_id = BaseLuciIntegrationTestCase.failure_build_id
            cls.failure_build_num = (
                BaseLuciIntegrationTestCase.failure_build_num
            )
            return

        print("\n" + "=" * 80)
        print(
            "[LUCI Integration Test] Starting modular live endpoint validation"
        )
        print("=" * 80)

        if not luci_client.is_authenticated():
            print("[AUTH] Not authenticated to LUCI (run 'bb auth-login')")
            return

        # Dynamically discover the latest SUCCESS CI build
        res_success = luci_client.run_prpc(
            "cr-buildbucket.appspot.com",
            "buildbucket.v2.Builds.SearchBuilds",
            {
                "predicate": {
                    "builder": {
                        "project": "chromium",
                        "bucket": "ci",
                        "builder": SAMPLE_BUILDER,
                    },
                    "status": "SUCCESS",
                },
                "pageSize": 1,
            },
        )
        if res_success and res_success.get("builds"):
            cls.success_build_id = res_success["builds"][0]["id"]
            cls.success_build_num = res_success["builds"][0]["number"]
            print(
                f"[DISCOVERY] Latest SUCCESS CI build: {SAMPLE_BUILDER}/"
                f"{cls.success_build_num} (id: {cls.success_build_id})"
            )
        else:
            cls.success_build_id = None
            cls.success_build_num = None

        # Dynamically discover the latest FAILURE CI build
        res_fail = luci_client.run_prpc(
            "cr-buildbucket.appspot.com",
            "buildbucket.v2.Builds.SearchBuilds",
            {
                "predicate": {
                    "builder": {
                        "project": "chromium",
                        "bucket": "ci",
                        "builder": SAMPLE_BUILDER,
                    },
                    "status": "FAILURE",
                },
                "pageSize": 1,
            },
        )
        if res_fail and res_fail.get("builds"):
            cls.failure_build_id = res_fail["builds"][0]["id"]
            cls.failure_build_num = res_fail["builds"][0]["number"]
            print(
                f"[DISCOVERY] Latest FAILURE CI build: {SAMPLE_BUILDER}/"
                f"{cls.failure_build_num} (id: {cls.failure_build_id})"
            )
        else:
            cls.failure_build_id = None
            cls.failure_build_num = None

        print(f"[DISCOVERY] Target Test Query: '{SAMPLE_TEST_QUERY}'")
        print("-" * 80)

        BaseLuciIntegrationTestCase.success_build_id = cls.success_build_id
        BaseLuciIntegrationTestCase.success_build_num = cls.success_build_num
        BaseLuciIntegrationTestCase.failure_build_id = cls.failure_build_id
        BaseLuciIntegrationTestCase.failure_build_num = cls.failure_build_num
        BaseLuciIntegrationTestCase._discovery_done = True

    def setUp(self):
        if not luci_client.is_authenticated():
            self.skipTest("Not authenticated to LUCI (run 'bb auth-login')")


class TestLuciClientIntegration(BaseLuciIntegrationTestCase):
    def test_resolve_build_id(self):
        if not self.success_build_num:
            self.skipTest("Could not discover latest CI build number")
        build_id = luci_client.resolve_build_id(
            "chromium", "ci", SAMPLE_BUILDER, self.success_build_num
        )
        self.assertEqual(build_id, self.success_build_id)
        print(
            f"[PASS] resolve_build_id: {SAMPLE_BUILDER}/{self.success_build_num}"
            f" -> {build_id}"
        )

    def test_get_build(self):
        if not self.success_build_id:
            self.skipTest("Could not discover latest CI build ID")
        data = luci_client.get_build(f"b{self.success_build_id}")
        self.assertEqual(data.get("status"), "SUCCESS")
        self.assertEqual(data.get("id"), self.success_build_id)
        print(
            f"[PASS] get_build: Build {self.success_build_id} status = "
            f"{data.get('status')}"
        )


class TestFindClBuildsIntegration(BaseLuciIntegrationTestCase):
    def test_find_cl_builds(self):
        builds = find_cl_builds.find_cl_builds(SAMPLE_CL, show_all=True)
        self.assertGreater(len(builds), 0)
        self.assertTrue(all("builder" in b and "id" in b for b in builds))
        sample_builder = builds[0]["builder"]
        print(
            f"[PASS] find_cl_builds: Found {len(builds)} builds for CL "
            f"{SAMPLE_CL} (sample builder: {sample_builder})"
        )


class TestListFailuresIntegration(BaseLuciIntegrationTestCase):
    def test_list_failures_and_fetch_log(self):
        if not self.failure_build_id:
            self.skipTest("No recent failing build found for builder")

        res = list_failures.list_failures(f"b{self.failure_build_id}")
        self.assertGreater(len(res), 0)
        first_task = next(iter(res))
        self.assertIn("id", res[first_task][0])
        self.assertIn("res", res[first_task][0])

        first_res_name = res[first_task][0]["res"]
        first_test_id = res[first_task][0]["id"]
        output = fetch_log.fetch_log_snippet(first_res_name)
        self.assertGreater(len(output), 0)

        preview = output.strip().split("\n")[0][:100]
        print(
            f"[PASS] list_failures_and_fetch_log: Build {self.failure_build_id} "
            f"has {len(res)} failing task(s).\n"
            f"       Fetched log snippet for {first_test_id} ({len(output)} bytes).\n"
            f"       Log preview: {preview}..."
        )


class TestCheckTestInBuildIntegration(BaseLuciIntegrationTestCase):
    def test_check_test_in_build(self):
        if not self.success_build_id:
            self.skipTest("Could not discover latest CI build ID")
        res = check_test_in_build.check_test_in_build(
            f"b{self.success_build_id}", SAMPLE_TEST_QUERY
        )
        self.assertGreater(len(res), 0)
        self.assertTrue(any(SAMPLE_TEST_QUERY in r.get("id", "") for r in res))
        print(
            f"[PASS] check_test_in_build: Found {len(res)} test result(s) for "
            f"'{SAMPLE_TEST_QUERY}' in build {self.success_build_id}:"
        )
        for tr in res:
            print(f"       - {tr.get('id')} ({tr.get('status')})")


class TestTestHistoryIntegration(BaseLuciIntegrationTestCase):
    def test_fetch_test_history_verdicts(self):
        verdicts = test_history.fetch_test_history_verdicts(
            "chromium",
            SAMPLE_TEST_ID,
            limit=5,
            builder=SAMPLE_BUILDER,
            test_suite="chrome_public_test_apk",
            verbose=False,
        )
        self.assertGreaterEqual(len(verdicts), 1)
        daily = test_history.aggregate_by_day(verdicts)
        self.assertGreaterEqual(len(daily), 1)
        table = test_history.format_summary_table(daily, SAMPLE_TEST_ID)
        self.assertIn("Test History Aggregate Summary", table)
        print(
            f"[PASS] test_history: Queried {len(verdicts)} verdict(s) for "
            f"{SAMPLE_TEST_QUERY} on {SAMPLE_BUILDER}\n"
            f"{table}"
        )


if __name__ == "__main__":
    unittest.main()
