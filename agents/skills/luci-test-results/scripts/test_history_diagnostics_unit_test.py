#!/usr/bin/env python3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Consolidated, high-signal unit test suite for test_history_diagnostics matching DIAGNOSTICS_SPEC.md."""

from datetime import datetime, timedelta, timezone
import unittest

from test_history_diagnostics import (
    BuildVerdict,
    evaluate_diagnostics,
    parse_iso_time,
    verdict_to_build,
)


class TestHistoryDiagnostics(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 25, 16, 0, 0, tzinfo=timezone.utc)

    # -----------------------------------------------------------------------
    # 1. Parsing and Mapping
    # -----------------------------------------------------------------------
    def test_parse_iso_time(self):
        dt = parse_iso_time("2026-08-25T16:00:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual(parse_iso_time("2026-08-25T16:00:00+00:00"), dt)
        self.assertIsNone(parse_iso_time(""))
        self.assertIsNone(parse_iso_time("invalid-time"))

    def test_verdict_to_build_mapping(self):
        # EXPECTED -> PASS
        b_pass = verdict_to_build(
            {"partitionTime": "2026-08-25T12:00:00Z", "status": "EXPECTED"}
        )
        self.assertEqual(
            (b_pass.outcome, b_pass.run_pass, b_pass.run_fail), ("PASS", 1, 0)
        )

        # FLAKY -> PASS_ON_RETRY
        b_retry = verdict_to_build(
            {"partitionTime": "2026-08-25T12:00:00Z", "status": "FLAKY"}
        )
        self.assertEqual(
            (b_retry.outcome, b_retry.run_pass, b_retry.run_fail),
            ("PASS_ON_RETRY", 1, 1),
        )

        # UNEXPECTED -> FAIL
        b_fail = verdict_to_build(
            {"partitionTime": "2026-08-25T12:00:00Z", "status": "UNEXPECTED"}
        )
        self.assertEqual(
            (b_fail.outcome, b_fail.run_pass, b_fail.run_fail), ("FAIL", 0, 1)
        )

        # SKIPPED -> SKIP
        b_skip = verdict_to_build(
            {"partitionTime": "2026-08-25T12:00:00Z", "status": "SKIPPED"}
        )
        self.assertEqual((b_skip.outcome, b_skip.run_skip), ("SKIP", 1))

    # -----------------------------------------------------------------------
    # 2. Regression Scores & Active Breakage
    # -----------------------------------------------------------------------
    def test_regression_score_formula_boundaries(self):
        """RegressionScore = TailFailStreak * PreStreakPassCount (Threshold >= 20)."""
        test_cases = [
            # (tail_fail, pre_pass, expected_status, expected_score, desc)
            (
                1,
                49,
                "ACTIVE REGRESSION",
                49,
                "Android arm dbg 1-fail regression",
            ),
            (1, 24, "ACTIVE REGRESSION", 24, "proguard 1-fail regression"),
            (2, 10, "ACTIVE REGRESSION", 20, "2-fail boundary"),
            (3, 7, "ACTIVE REGRESSION", 21, "3-fail regression"),
            (4, 5, "ACTIVE REGRESSION", 20, "4-fail regression"),
            (1, 10, "FLAKY", 10, "1-fail below threshold (waits for 2nd fail)"),
            (2, 2, "HIGHLY FLAKY", 4, "2-fail flaky oscillation"),
        ]
        for f, p, exp_status, exp_score, desc in test_cases:
            with self.subTest(desc=desc):
                builds = [
                    BuildVerdict(
                        timestamp=self.now - timedelta(hours=p + f - i),
                        outcome="PASS",
                    )
                    for i in range(p)
                ] + [
                    BuildVerdict(
                        timestamp=self.now - timedelta(hours=f - i),
                        outcome="FAIL",
                    )
                    for i in range(f)
                ]
                diag = evaluate_diagnostics(builds, now=self.now)
                self.assertEqual(diag.status, exp_status)
                self.assertEqual(diag.regression_score, exp_score)

    def test_retry_pass_breaks_clean_baseline(self):
        """PASS_ON_RETRY (flaky pass) breaks the clean baseline streak (ContextMenuTest case)."""
        builds = (
            [
                BuildVerdict(
                    timestamp=self.now - timedelta(hours=40 - i), outcome="PASS"
                )
                for i in range(30)
            ]
            + [
                BuildVerdict(
                    timestamp=self.now - timedelta(hours=9),
                    outcome="PASS_ON_RETRY",
                ),
                BuildVerdict(
                    timestamp=self.now - timedelta(hours=8),
                    outcome="PASS_ON_RETRY",
                ),
            ]
            + [
                BuildVerdict(
                    timestamp=self.now - timedelta(hours=7 - i), outcome="PASS"
                )
                for i in range(6)
            ]
            + [
                BuildVerdict(
                    timestamp=self.now - timedelta(hours=1), outcome="FAIL"
                ),
            ]
        )
        diag = evaluate_diagnostics(builds, now=self.now)
        self.assertEqual(diag.tail_fail_streak, 1)
        self.assertEqual(diag.pre_streak_pass_count, 6)
        self.assertEqual(diag.regression_score, 6)
        self.assertEqual(diag.status, "FLAKY")

    def test_old_failure_does_not_poison_long_clean_streak(self):
        """An isolated failure 4.5 days ago does not poison subsequent clean baseline (proguard case)."""
        builds = (
            [
                BuildVerdict(
                    timestamp=self.now - timedelta(days=4, hours=12),
                    outcome="FAIL",
                ),
            ]
            + [
                BuildVerdict(
                    timestamp=self.now - timedelta(days=4 - i * 0.15),
                    outcome="PASS",
                )
                for i in range(24)
            ]
            + [
                BuildVerdict(
                    timestamp=self.now - timedelta(hours=1), outcome="FAIL"
                ),
            ]
        )
        diag = evaluate_diagnostics(builds, now=self.now)
        self.assertEqual(diag.status, "ACTIVE REGRESSION")
        self.assertEqual(diag.regression_score, 24)

    def test_isolated_skip_neutrality_on_regression(self):
        """An isolated <=48h skip build does not reset the clean pass streak."""
        builds = [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=30 - i), outcome="PASS"
            )
            for i in range(10)
        ] + [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=6), outcome="SKIP"
            ),
            BuildVerdict(
                timestamp=self.now - timedelta(hours=2), outcome="FAIL"
            ),
            BuildVerdict(
                timestamp=self.now - timedelta(hours=1), outcome="FAIL"
            ),
        ]
        diag = evaluate_diagnostics(builds, now=self.now)
        self.assertEqual(diag.status, "ACTIVE REGRESSION")
        self.assertEqual(diag.pre_streak_pass_count, 10)
        self.assertEqual(diag.regression_score, 20)

    # -----------------------------------------------------------------------
    # 3. Recovery Scores & Active Recovery
    # -----------------------------------------------------------------------
    def test_recovery_score_formula_boundaries(self):
        """RecoveryScore = TailPassStreak * PreStreakFailCount (Threshold >= 16)."""
        test_cases = [
            # (tail_pass, pre_fail, expected_status, expected_score, desc)
            (
                4,
                4,
                "PASSING (RECOVERED)",
                16,
                "4 passes after 4 fails (dead zone fix)",
            ),
            (3, 6, "PASSING (RECOVERED)", 18, "3 passes after 6 fails"),
            (2, 8, "PASSING (RECOVERED)", 16, "2 passes after 8 fails"),
            (
                2,
                2,
                "HIGHLY FLAKY",
                4,
                "2 passes after 2 fails (flaky oscillation)",
            ),
        ]
        for p, f, exp_status, exp_score, desc in test_cases:
            with self.subTest(desc=desc):
                builds = [
                    BuildVerdict(
                        timestamp=self.now - timedelta(hours=p + f - i),
                        outcome="FAIL",
                    )
                    for i in range(f)
                ] + [
                    BuildVerdict(
                        timestamp=self.now - timedelta(hours=p - i),
                        outcome="PASS",
                    )
                    for i in range(p)
                ]
                diag = evaluate_diagnostics(builds, now=self.now)
                self.assertEqual(diag.status, exp_status)
                self.assertEqual(diag.recovery_score, exp_score)

    def test_recovery_with_retries_on_tail(self):
        """Tail passes containing retries trigger PASSING ON RETRY (RECOVERED)."""
        builds = [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=10 - i), outcome="FAIL"
            )
            for i in range(4)
        ] + [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=4), outcome="PASS"
            ),
            BuildVerdict(
                timestamp=self.now - timedelta(hours=3), outcome="PASS_ON_RETRY"
            ),
            BuildVerdict(
                timestamp=self.now - timedelta(hours=2), outcome="PASS"
            ),
            BuildVerdict(
                timestamp=self.now - timedelta(hours=1), outcome="PASS"
            ),
        ]
        diag = evaluate_diagnostics(builds, now=self.now)
        self.assertEqual(diag.status, "PASSING ON RETRY (RECOVERED)")
        self.assertEqual(diag.recovery_score, 16)
        self.assertIn(
            "4 tail passes (with retries) * 4 prior fails = RecoveryScore 16",
            diag.description,
        )

    def test_isolated_skip_neutrality_on_recovery(self):
        """An isolated <=48h skip build does not reset the failure streak on recovery."""
        builds = (
            [
                BuildVerdict(
                    timestamp=self.now - timedelta(hours=20 - i * 2),
                    outcome="FAIL",
                )
                for i in range(4)
            ]
            + [
                BuildVerdict(
                    timestamp=self.now - timedelta(hours=9), outcome="SKIP"
                ),
            ]
            + [
                BuildVerdict(
                    timestamp=self.now - timedelta(hours=8 - i * 2),
                    outcome="PASS",
                )
                for i in range(4)
            ]
        )
        diag = evaluate_diagnostics(builds, now=self.now)
        self.assertEqual(diag.status, "PASSING (RECOVERED)")
        self.assertEqual(diag.recovery_score, 16)

    # -----------------------------------------------------------------------
    # 4. Steady-State Failure & Flakiness
    # -----------------------------------------------------------------------
    def test_consistently_failing_classification(self):
        """100% fail rate, >= 70% fail rate, or >= 10 tail fail streak -> CONSISTENTLY FAILING."""
        # 100% fail rate (3 fails, 0 pass)
        builds_100 = [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=i + 1), outcome="FAIL"
            )
            for i in range(3)
        ]
        self.assertEqual(
            evaluate_diagnostics(builds_100, now=self.now).status,
            "CONSISTENTLY FAILING",
        )

        # >= 70% fail rate with >= 3 builds (3 fails, 1 pass = 75%)
        builds_75 = [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=12), outcome="FAIL"
            ),
            BuildVerdict(
                timestamp=self.now - timedelta(hours=8), outcome="FAIL"
            ),
            BuildVerdict(
                timestamp=self.now - timedelta(hours=4), outcome="FAIL"
            ),
            BuildVerdict(
                timestamp=self.now - timedelta(hours=1), outcome="PASS"
            ),
        ]
        self.assertEqual(
            evaluate_diagnostics(builds_75, now=self.now).status,
            "CONSISTENTLY FAILING",
        )

        # >= 10 tail fail streak
        builds_12 = [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=20 - i), outcome="FAIL"
            )
            for i in range(12)
        ]
        self.assertEqual(
            evaluate_diagnostics(builds_12, now=self.now).status,
            "CONSISTENTLY FAILING",
        )

    def test_interleaved_flakiness_rates(self):
        """Interleaved failures tier into HIGHLY FLAKY (>=20%) vs FLAKY (<20%)."""
        # >= 20% fail rate (1 fail, 4 passes = 20%)
        builds_20 = [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=i + 1), outcome="PASS"
            )
            for i in range(4)
        ] + [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=5), outcome="FAIL"
            )
        ]
        self.assertEqual(
            evaluate_diagnostics(builds_20, now=self.now).status, "HIGHLY FLAKY"
        )

        # < 20% fail rate (1 fail, 9 passes = 10%)
        builds_10 = [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=i + 1), outcome="PASS"
            )
            for i in range(9)
        ] + [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=10), outcome="FAIL"
            )
        ]
        self.assertEqual(
            evaluate_diagnostics(builds_10, now=self.now).status, "FLAKY"
        )

    # -----------------------------------------------------------------------
    # 5. Skipped, Inactive & Fallbacks
    # -----------------------------------------------------------------------
    def test_skipped_and_not_running_statuses(self):
        """ValidBuildTotal == 0 routes to SKIPPED or NOT RUNNING."""
        builds_skip = [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=2), outcome="SKIP"
            )
        ]
        self.assertEqual(
            evaluate_diagnostics(builds_skip, now=self.now).status, "SKIPPED"
        )
        self.assertEqual(
            evaluate_diagnostics([], now=self.now).status, "NOT RUNNING"
        )

    def test_fallback_date_inactive_window_cutoff(self):
        """Tests inactive >36h report NOT RUNNING; tests inactive <=36h fall back to latest date."""
        # Inactive for 3 days -> NOT RUNNING
        builds_old = [
            BuildVerdict(
                timestamp=self.now - timedelta(days=3, hours=i), outcome="FAIL"
            )
            for i in range(4)
        ]
        self.assertEqual(
            evaluate_diagnostics(builds_old, now=self.now).status, "NOT RUNNING"
        )

        # Inactive for 28h (<= 36h) -> falls back to latest date
        builds_recent = [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=28 - i), outcome="PASS"
            )
            for i in range(5)
        ]
        self.assertEqual(
            evaluate_diagnostics(builds_recent, now=self.now).status, "PASSING"
        )

    # -----------------------------------------------------------------------
    # 6. Steady-State Passing & Retry Tiers
    # -----------------------------------------------------------------------
    def test_steady_state_passing_and_retry_tiers(self):
        """0 build failures tier cleanly across PASSING, MOSTLY PASSING, and RETRIES."""
        # 100% clean passes
        builds_clean = [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=i), outcome="PASS"
            )
            for i in range(10)
        ]
        self.assertEqual(
            evaluate_diagnostics(builds_clean, now=self.now).status, "PASSING"
        )

        # Rare retries (<8% run fail rate: 14 clean, 1 retry -> 6.25%)
        builds_rare = [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=20 - i), outcome="PASS"
            )
            for i in range(14)
        ] + [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=1), outcome="PASS_ON_RETRY"
            )
        ]
        self.assertEqual(
            evaluate_diagnostics(builds_rare, now=self.now).status,
            "MOSTLY PASSING",
        )

        # Moderate retries (8% - 30% run fail rate: 8 clean, 1 retry -> 10%)
        builds_mod = [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=10 - i), outcome="PASS"
            )
            for i in range(8)
        ] + [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=1), outcome="PASS_ON_RETRY"
            )
        ]
        self.assertEqual(
            evaluate_diagnostics(builds_mod, now=self.now).status,
            "PASSING WITH RETRIES",
        )

        # Heavy retries (>= 30% run fail rate: 3 clean, 3 retry -> 33.3%)
        builds_heavy = [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=6 - i), outcome="PASS"
            )
            for i in range(3)
        ] + [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=3 - i),
                outcome="PASS_ON_RETRY",
            )
            for i in range(3)
        ]
        self.assertEqual(
            evaluate_diagnostics(builds_heavy, now=self.now).status,
            "PASSING ON HEAVY RETRY",
        )

    def test_history_glyph_generation(self):
        """Build sequence glyphs are correctly generated newest -> oldest."""
        builds = [
            BuildVerdict(
                timestamp=self.now - timedelta(hours=3), outcome="PASS"
            ),
            BuildVerdict(
                timestamp=self.now - timedelta(hours=2), outcome="PASS_ON_RETRY"
            ),
            BuildVerdict(
                timestamp=self.now - timedelta(hours=1), outcome="FAIL"
            ),
        ]
        diag = evaluate_diagnostics(builds, now=self.now)
        # Newest (FAIL) is index 0 in glyph string
        self.assertEqual(diag.history_glyphs, "FRP")


if __name__ == "__main__":
    unittest.main()
