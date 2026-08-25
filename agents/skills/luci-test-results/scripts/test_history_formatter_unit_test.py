#!/usr/bin/env python3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for test_history_formatter presentation module."""

import unittest

from test_history_formatter import (
    evaluate_builder_status,
    format_summary_table,
)


class TestHistoryFormatter(unittest.TestCase):
    def test_evaluate_builder_status_precomputed(self):
        data = {
            "diagnostics": {
                "status": "ACTIVE REGRESSION",
                "description": "1 tail fail * 49 prior clean passes = RegressionScore 49 (threshold >= 20).",
            }
        }
        res = evaluate_builder_status(data)
        self.assertEqual(
            res,
            "Status: ACTIVE REGRESSION (1 tail fail * 49 prior clean passes = RegressionScore 49 (threshold >= 20).)",
        )

    def test_evaluate_builder_status_fallback(self):
        data_clean = {
            "daily": {"2026-08-25": {"PASS": 10, "FAIL": 0, "PASS_ON_RETRY": 0}}
        }
        self.assertEqual(evaluate_builder_status(data_clean), "Status: PASSING")

        data_fail = {
            "daily": {"2026-08-25": {"PASS": 0, "FAIL": 5, "PASS_ON_RETRY": 0}}
        }
        self.assertEqual(
            evaluate_builder_status(data_fail), "Status: CONSISTENTLY FAILING"
        )

    def test_format_summary_table_condensed_sections(self):
        stats = {
            "chromium/ci/android-13-x64-rel": {
                "daily": {
                    "2026-08-25": {
                        "PASS": 10,
                        "FAIL": 1,
                        "PASS_ON_RETRY": 0,
                        "TOTAL_BUILDS": 11,
                        "BUILD_FAIL_RATE": 9.1,
                        "HISTORY": "FPPPPPPPPPP",
                    }
                },
                "diagnostics": {
                    "status": "ACTIVE REGRESSION",
                    "description": "Active regression starting recently.",
                },
            },
            "chromium/ci/android-code-coverage": {
                "daily": {
                    "2026-08-25": {
                        "PASS": 10,
                        "FAIL": 0,
                        "PASS_ON_RETRY": 0,
                        "TOTAL_BUILDS": 10,
                        "BUILD_FAIL_RATE": 0.0,
                        "HISTORY": "",
                    }
                },
                "diagnostics": {
                    "status": "PASSING",
                    "description": "100% clean.",
                },
            },
            "chromium/ci/android-desktop-x64-rel": {
                "daily": {
                    "2026-08-25": {
                        "PASS": 0,
                        "FAIL": 5,
                        "PASS_ON_RETRY": 0,
                        "TOTAL_BUILDS": 5,
                        "BUILD_FAIL_RATE": 100.0,
                        "HISTORY": "",
                    }
                },
                "diagnostics": {
                    "status": "CONSISTENTLY FAILING",
                    "description": "100% fail.",
                },
            },
        }

        table = format_summary_table(stats, "sample-test-id")
        self.assertIn(
            "100% PASSING Builders (1 builders - 0 out of 10 builds with failures/retries):",
            table,
        )
        self.assertIn(
            "- chromium/ci/android-code-coverage (10 clean builds)", table
        )
        self.assertIn(
            "100% FAILING Builders (1 builders - 5 out of 5 builds failed):",
            table,
        )
        self.assertIn(
            "- chromium/ci/android-desktop-x64-rel (5/5 failed)", table
        )
        self.assertIn("Builder: chromium/ci/android-13-x64-rel", table)
        self.assertIn("FPPPPPPPPPP", table)


if __name__ == "__main__":
    unittest.main()
