#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Live integration tests for luci_triage.py.

Dynamically discovers fresh CI builds and tests against live endpoints.
Prints structured test results for LLM agent confirmation.
Requires `bb auth-login`.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import luci_triage

SAMPLE_BUILDER = "android-13-x64-rel"
SAMPLE_CL = "8260911"
SAMPLE_TEST_QUERY = "ExampleFreshCtaTest"
SAMPLE_TEST_ID = (
    "://chrome/android\\:chrome_public_test_apk!junit:"
    "org.chromium.chrome.browser:ExampleFreshCtaTest#testStartOnBlankPage"
)


class TestLuciTriageIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        print("\n" + "=" * 80)
        print("[LUCI Integration Test] Starting live endpoint validation")
        print("=" * 80)

        if not luci_triage.is_authenticated():
            print("[AUTH] Not authenticated to LUCI (run 'bb auth-login')")
            return

        # Dynamically discover the latest SUCCESS CI build
        res_success = luci_triage.run_prpc(
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
        res_fail = luci_triage.run_prpc(
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

    def setUp(self):
        if not luci_triage.is_authenticated():
            self.skipTest("Not authenticated to LUCI (run 'bb auth-login')")
        if not self.success_build_id:
            self.skipTest("Could not discover latest CI build for builder")

    def test_resolve_build_id(self):
        build_id = luci_triage.resolve_build_id(
            "chromium", "ci", SAMPLE_BUILDER, self.success_build_num
        )
        self.assertEqual(build_id, self.success_build_id)
        print(
            f"[PASS] resolve_build_id: {SAMPLE_BUILDER}/{self.success_build_num}"
            f" -> {build_id}"
        )

    def test_get_build(self):
        data = luci_triage.get_build(f"b{self.success_build_id}")
        self.assertEqual(data.get("status"), "SUCCESS")
        self.assertEqual(data.get("id"), self.success_build_id)
        print(
            f"[PASS] get_build: Build {self.success_build_id} status = "
            f"{data.get('status')}"
        )

    def test_find_cl_builds(self):
        builds = luci_triage.find_cl_builds(SAMPLE_CL, show_all=True)
        self.assertGreater(len(builds), 0)
        self.assertTrue(all("builder" in b and "id" in b for b in builds))
        sample_builder = builds[0]["builder"]
        print(
            f"[PASS] find_cl_builds: Found {len(builds)} builds for CL "
            f"{SAMPLE_CL} (sample builder: {sample_builder})"
        )

    def test_check_test_in_build(self):
        res = luci_triage.check_test(
            f"b{self.success_build_id}", SAMPLE_TEST_QUERY
        )
        self.assertGreater(len(res), 0)
        self.assertTrue(any(SAMPLE_TEST_QUERY in r.get("id", "") for r in res))
        print(
            f"[PASS] check_test: Found {len(res)} test result(s) for "
            f"'{SAMPLE_TEST_QUERY}' in build {self.success_build_id}:"
        )
        for tr in res:
            print(f"       - {tr.get('id')} ({tr.get('status')})")

    def test_test_history(self):
        verdicts = luci_triage.test_history(
            "chromium",
            SAMPLE_TEST_ID,
            limit=5,
            builder=SAMPLE_BUILDER,
            test_suite="chrome_public_test_apk",
        )
        self.assertGreaterEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0].get("testId"), SAMPLE_TEST_ID)
        print(
            f"[PASS] test_history: Queried {len(verdicts)} verdict(s) for "
            f"{SAMPLE_TEST_QUERY} on {SAMPLE_BUILDER}"
        )

    def test_list_failures_and_fetch_log(self):
        if not self.failure_build_id:
            self.skipTest("No recent failing build found for builder")

        res = luci_triage.list_failures(f"b{self.failure_build_id}")
        self.assertGreater(len(res), 0)
        first_task = next(iter(res))
        self.assertIn("id", res[first_task][0])
        self.assertIn("res", res[first_task][0])

        first_res_name = res[first_task][0]["res"]
        first_test_id = res[first_task][0]["id"]
        output = luci_triage.fetch_log_snippet(first_res_name)
        self.assertGreater(len(output), 0)

        preview = output.strip().split("\n")[0][:100]
        print(
            f"[PASS] list_failures_and_fetch_log: Build {self.failure_build_id} "
            f"has {len(res)} failing task(s).\n"
            f"       Fetched log snippet for {first_test_id} ({len(output)} bytes).\n"
            f"       Log preview: {preview}..."
        )


if __name__ == "__main__":
    unittest.main()
