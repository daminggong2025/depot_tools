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

    def test_format_summary_table_empty(self):
        """Empty builder stats renders a clear empty state message."""
        table = format_summary_table({}, "sample-test-id")
        self.assertIn("No test history verdicts available.", table)
        self.assertIn("Test ID: sample-test-id", table)

    def test_format_summary_table_show_all(self):
        """show_all=True renders full daily tables for passing/failing builders without condensing."""
        stats = {
            "chromium/ci/android-clean": {
                "daily": {
                    "2026-08-25": {
                        "PASS": 10,
                        "FAIL": 0,
                        "PASS_ON_RETRY": 0,
                        "TOTAL_BUILDS": 10,
                        "BUILD_FAIL_RATE": 0.0,
                    }
                },
                "diagnostics": {"status": "PASSING"},
            },
            "chromium/ci/android-fail": {
                "daily": {
                    "2026-08-25": {
                        "PASS": 0,
                        "FAIL": 5,
                        "PASS_ON_RETRY": 0,
                        "TOTAL_BUILDS": 5,
                        "BUILD_FAIL_RATE": 100.0,
                    }
                },
                "diagnostics": {"status": "CONSISTENTLY FAILING"},
            },
        }
        table = format_summary_table(stats, "sample-test-id", show_all=True)
        self.assertIn("Builder: chromium/ci/android-clean", table)
        self.assertIn("Builder: chromium/ci/android-fail", table)
        self.assertNotIn("100% PASSING Builders", table)
        self.assertNotIn("100% FAILING Builders", table)

    def test_format_summary_table_sorting_hierarchy(self):
        """Detailed builders are sorted by diagnostic severity (ACTIVE REGRESSION at top)."""
        stats = {
            "chromium/ci/builder-clean": {
                "daily": {
                    "2026-08-25": {
                        "PASS": 5,
                        "FAIL": 0,
                        "PASS_ON_RETRY": 1,
                        "TOTAL_BUILDS": 6,
                        "BUILD_FAIL_RATE": 0.0,
                    }
                },
                "diagnostics": {"status": "PASSING WITH RETRIES"},
            },
            "chromium/ci/builder-flaky": {
                "daily": {
                    "2026-08-25": {
                        "PASS": 5,
                        "FAIL": 1,
                        "PASS_ON_RETRY": 0,
                        "TOTAL_BUILDS": 6,
                        "BUILD_FAIL_RATE": 16.7,
                    }
                },
                "diagnostics": {"status": "FLAKY"},
            },
            "chromium/ci/builder-regression": {
                "daily": {
                    "2026-08-25": {
                        "PASS": 10,
                        "FAIL": 1,
                        "PASS_ON_RETRY": 0,
                        "TOTAL_BUILDS": 11,
                        "BUILD_FAIL_RATE": 9.1,
                    }
                },
                "diagnostics": {"status": "ACTIVE REGRESSION"},
            },
        }
        table = format_summary_table(stats, "sample-test-id")
        pos_reg = table.find("Builder: chromium/ci/builder-regression")
        pos_flaky = table.find("Builder: chromium/ci/builder-flaky")
        pos_clean = table.find("Builder: chromium/ci/builder-clean")
        self.assertTrue(pos_reg < pos_flaky < pos_clean)

    def test_format_summary_table_skipped_and_inactive(self):
        """SKIPPED and NOT RUNNING builders are grouped into inactive section."""
        stats = {
            "chromium/ci/builder-active": {
                "daily": {
                    "2026-08-25": {
                        "PASS": 5,
                        "FAIL": 1,
                        "PASS_ON_RETRY": 0,
                        "TOTAL_BUILDS": 6,
                        "BUILD_FAIL_RATE": 16.7,
                    }
                },
                "diagnostics": {"status": "FLAKY"},
            },
            "chromium/ci/builder-skipped": {
                "daily": {
                    "2026-08-25": {
                        "PASS": 0,
                        "FAIL": 0,
                        "PASS_ON_RETRY": 0,
                        "SKIP": 8,
                        "TOTAL_BUILDS": 8,
                        "BUILD_FAIL_RATE": 0.0,
                    }
                },
                "diagnostics": {"status": "SKIPPED"},
            },
            "chromium/ci/builder-inactive": {
                "daily": {},
                "diagnostics": {"status": "NOT RUNNING"},
            },
        }
        table = format_summary_table(stats, "sample-test-id")
        self.assertIn(
            "Skipped / Inactive Builders (2 builders):",
            table,
        )
        self.assertIn(
            "- chromium/ci/builder-skipped [SKIPPED] (8 skipped runs)", table
        )
        self.assertIn(
            "- chromium/ci/builder-inactive [NOT RUNNING] (0 runs executed)",
            table,
        )

    def test_format_summary_table_filter_header_and_try_note(self):
        """Filter args and try bucket explanatory note are rendered properly."""
        stats = {
            "android-13-x64-rel": {
                "daily": {
                    "2026-08-25": {
                        "PASS": 5,
                        "FAIL": 0,
                        "PASS_ON_RETRY": 0,
                        "TOTAL_BUILDS": 5,
                        "BUILD_FAIL_RATE": 0.0,
                    }
                },
                "diagnostics": {"status": "PASSING"},
            }
        }
        table = format_summary_table(
            stats,
            "sample-test-id",
            builder="android-13-x64-rel",
            bucket="try",
        )
        self.assertIn(
            "Test History by Builder (builder=android-13-x64-rel, bucket=try)",
            table,
        )
        self.assertIn("Note: Results are from the 'try' bucket", table)


if __name__ == "__main__":
    unittest.main()
