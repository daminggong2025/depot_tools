#!/usr/bin/env python3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test history diagnostic and regression evaluation module matching DIAGNOSTICS_SPEC.md."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional


def parse_iso_time(ts_str: str) -> Optional[datetime]:
    """Parses an ISO 8601/RFC 3339 timestamp into a timezone-aware datetime."""
    if not ts_str:
        return None
    try:
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None


@dataclass
class BuildVerdict:
    timestamp: datetime
    outcome: str  # 'PASS', 'PASS_ON_RETRY', 'FAIL', 'SKIP', 'NO_RUN'
    run_pass: int = 1
    run_fail: int = 0
    run_infra_fail: int = 0
    run_skip: int = 0
    build_id: str = ""

    def __post_init__(self):
        if self.outcome == "PASS_ON_RETRY" and self.run_fail == 0:
            self.run_fail = 1
        elif (
            self.outcome == "FAIL" and self.run_pass == 1 and self.run_fail == 0
        ):
            self.run_pass = 0
            self.run_fail = 1
        elif self.outcome == "SKIP" and self.run_skip == 0:
            self.run_pass = 0
            self.run_fail = 0
            self.run_skip = 1

    @property
    def is_clean(self) -> bool:
        return self.outcome in ("PASS", "PASS_ON_RETRY")

    @property
    def is_fail(self) -> bool:
        return self.outcome == "FAIL"

    @property
    def is_skip(self) -> bool:
        return self.outcome == "SKIP"

    @property
    def is_valid(self) -> bool:
        return self.outcome in ("PASS", "PASS_ON_RETRY", "FAIL")


@dataclass
class DiagnosticResult:
    status: str
    description: str
    build_clean_pass: int
    build_pass_on_retry: int
    build_pass: int
    build_fail: int
    build_skip: int
    valid_build_total: int
    build_fail_rate: float
    build_pass_rate: float
    run_pass: int
    run_fail: int
    valid_run_total: int
    run_fail_rate: float
    run_pass_rate: float
    tail_fail_streak: int
    tail_pass_streak: int
    pre_streak_pass_count: int
    pre_streak_fail_count: int
    regression_score: int
    recovery_score: int
    history_glyphs: str = ""
    streak_start: Optional[datetime] = None


def verdict_to_build(v: dict[str, Any]) -> Optional[BuildVerdict]:
    """Converts a raw LUCI Analysis test verdict dictionary to a BuildVerdict."""
    pt_str = v.get("partitionTime", "")
    if not pt_str:
        return None
    dt = parse_iso_time(pt_str)
    if dt is None:
        return None

    status = v.get("status", "UNKNOWN")
    status_v2 = v.get("statusV2", "")
    inv_id = v.get("invocationId", "")

    if status == "EXPECTED" or status_v2 == "PASSED":
        outcome = "PASS"
        r_p, r_f, r_s = 1, 0, 0
    elif status == "FLAKY" or status_v2 == "FLAKY":
        outcome = "PASS_ON_RETRY"
        r_p, r_f, r_s = 1, 1, 0
    elif status_v2 == "SKIPPED" or status in (
        "SKIPPED",
        "UNEXPECTEDLY_SKIPPED",
    ):
        outcome = "SKIP"
        r_p, r_f, r_s = 0, 0, 1
    elif status in ("UNEXPECTED", "EXONERATED") or status_v2 in (
        "FAILED",
        "EXECUTION_ERRORED",
    ):
        outcome = "FAIL"
        r_p, r_f, r_s = 0, 1, 0
    else:
        outcome = "NO_RUN"
        r_p, r_f, r_s = 0, 0, 0

    return BuildVerdict(
        timestamp=dt,
        outcome=outcome,
        run_pass=r_p,
        run_fail=r_f,
        run_skip=r_s,
        build_id=inv_id,
    )


def evaluate_diagnostics(
    builds: List[BuildVerdict],
    now: Optional[datetime] = None,
    max_lookback_days: int = 5,
) -> DiagnosticResult:
    """Evaluates overall builder diagnostic status according to DIAGNOSTICS_SPEC.md."""
    if now is None:
        now = datetime.now(timezone.utc)

    cutoff_5d = now - timedelta(days=max_lookback_days)
    recent_all = sorted(
        [b for b in builds if b.timestamp >= cutoff_5d],
        key=lambda b: b.timestamp,
    )
    valid_history = [b for b in recent_all if b.is_valid]

    # Generate glyph string (newest -> oldest)
    glyph_chars = []
    for b in reversed(recent_all):
        if b.outcome == "PASS":
            glyph_chars.append("P")
        elif b.outcome == "PASS_ON_RETRY":
            glyph_chars.append("R")
        elif b.outcome == "FAIL":
            glyph_chars.append("F")
        elif b.outcome == "SKIP":
            glyph_chars.append("-")
        else:
            glyph_chars.append(".")
    history_glyphs = "".join(glyph_chars)

    # 1. Streaks evaluation over 5-day lookback
    n_valid = len(valid_history)
    tail_fail_streak = 0
    for i in range(n_valid - 1, -1, -1):
        if valid_history[i].is_fail:
            tail_fail_streak += 1
        else:
            break

    tail_pass_streak = 0
    for i in range(n_valid - 1, -1, -1):
        if valid_history[i].is_clean:
            tail_pass_streak += 1
        else:
            break

    tail_pass_streak_retries = 0
    if tail_pass_streak > 0:
        tail_pass_streak_retries = sum(
            1
            for b in valid_history[n_valid - tail_pass_streak :]
            if b.outcome == "PASS_ON_RETRY"
        )

    pre_streak_pass_count = 0
    streak_start = None

    if tail_fail_streak > 0:
        idx = n_valid - 1 - tail_fail_streak
        for i in range(idx, -1, -1):
            if valid_history[i].outcome == "PASS":
                pre_streak_pass_count += 1
            else:
                break
        streak_start = valid_history[n_valid - tail_fail_streak].timestamp

    pre_streak_fail_count = 0
    if tail_pass_streak > 0:
        idx = n_valid - 1 - tail_pass_streak
        for i in range(idx, -1, -1):
            if valid_history[i].is_fail:
                pre_streak_fail_count += 1
            else:
                break
        if pre_streak_fail_count > 0:
            streak_start = valid_history[n_valid - tail_pass_streak].timestamp

    # 2. Sliding 24h Window evaluation
    cutoff_24h = now - timedelta(hours=24)
    w_builds = [b for b in recent_all if b.timestamp >= cutoff_24h]

    # Fallback to latest date IF within last 36 hours
    if not w_builds and recent_all:
        latest_dt = max(b.timestamp for b in recent_all)
        if latest_dt >= now - timedelta(hours=36):
            latest_date_str = latest_dt.strftime("%Y-%m-%d")
            w_builds = [
                b
                for b in recent_all
                if b.timestamp.strftime("%Y-%m-%d") == latest_date_str
            ]

    build_clean_pass = sum(1 for b in w_builds if b.outcome == "PASS")
    build_pass_on_retry = sum(
        1 for b in w_builds if b.outcome == "PASS_ON_RETRY"
    )
    build_pass = build_clean_pass + build_pass_on_retry
    build_fail = sum(1 for b in w_builds if b.is_fail)
    build_skip = sum(1 for b in w_builds if b.is_skip)
    valid_build_total = build_pass + build_fail

    run_pass = sum(b.run_pass for b in w_builds if b.is_valid)
    run_fail = sum(b.run_fail for b in w_builds if b.is_valid)
    valid_run_total = run_pass + run_fail

    build_fail_rate = (
        (build_fail / valid_build_total) if valid_build_total > 0 else 0.0
    )
    build_pass_rate = (
        (build_pass / valid_build_total) if valid_build_total > 0 else 0.0
    )
    run_fail_rate = (run_fail / valid_run_total) if valid_run_total > 0 else 0.0
    run_pass_rate = (run_pass / valid_run_total) if valid_run_total > 0 else 0.0

    # 3. Decision Tree Matching DIAGNOSTICS_SPEC.md Section 2 (Formula-based)
    regression_score = tail_fail_streak * pre_streak_pass_count
    recovery_score = tail_pass_streak * pre_streak_fail_count

    if valid_build_total == 0:
        if build_skip > 0:
            status = "SKIPPED"
            desc = f"100% of runs in recent window were skipped ({build_skip} skipped builds)."
        else:
            status = "NOT RUNNING"
            desc = "0 test runs executed in recent window."
    elif (
        regression_score >= 20
        and tail_fail_streak >= 1
        and pre_streak_pass_count >= 2
    ):
        status = "ACTIVE REGRESSION"
        fail_word = "fail" if tail_fail_streak == 1 else "fails"
        pass_word = "pass" if pre_streak_pass_count == 1 else "passes"
        desc = (
            f"Active regression starting at {streak_start.isoformat() if streak_start else 'recently'}: "
            f"{tail_fail_streak} tail {fail_word} * {pre_streak_pass_count} prior clean {pass_word} = "
            f"RegressionScore {regression_score} (threshold >= 20)."
        )
    elif (
        recovery_score >= 16
        and tail_pass_streak >= 2
        and pre_streak_fail_count >= 2
    ):
        pass_word = "pass" if tail_pass_streak == 1 else "passes"
        fail_word = "fail" if pre_streak_fail_count == 1 else "fails"
        if tail_pass_streak_retries == 0:
            status = "PASSING (RECOVERED)"
            desc = (
                f"Recovered cleanly starting at {streak_start.isoformat() if streak_start else 'recently'}: "
                f"{tail_pass_streak} tail clean {pass_word} * {pre_streak_fail_count} prior {fail_word} = "
                f"RecoveryScore {recovery_score} (threshold >= 16)."
            )
        else:
            status = "PASSING ON RETRY (RECOVERED)"
            desc = (
                f"Recovered on retry starting at {streak_start.isoformat() if streak_start else 'recently'}: "
                f"{tail_pass_streak} tail {pass_word} (with retries) * {pre_streak_fail_count} prior {fail_word} = "
                f"RecoveryScore {recovery_score} (threshold >= 16)."
            )
    elif (
        (build_pass == 0 and build_fail > 0)
        or (build_fail_rate >= 0.70 and valid_build_total >= 3)
        or tail_fail_streak >= 10
    ):
        status = "CONSISTENTLY FAILING"
        desc = f"Persistent failure rate ({build_fail_rate * 100:.1f}% build fail rate, {build_fail}/{valid_build_total} failed in recent window)."
    elif build_fail_rate >= 0.20:
        status = "HIGHLY FLAKY"
        desc = f"Frequent intermittent build failures ({build_fail_rate * 100:.1f}% build fail rate, {build_fail}/{valid_build_total} failed in recent window)."
    elif build_fail > 0:
        status = "FLAKY"
        desc = f"Low intermittent build failures ({build_fail_rate * 100:.1f}% build fail rate, {build_fail}/{valid_build_total} failed in recent window)."
    elif run_fail == 0:
        status = "PASSING"
        desc = f"100% clean in recent window across {valid_build_total} builds (0 failures, 0 retries)."
    elif run_fail_rate >= 0.30:
        status = "PASSING ON HEAVY RETRY"
        desc = f"All {valid_build_total} builds succeeded, but heavy retries ({run_fail_rate * 100:.1f}% run fail rate)."
    elif run_fail_rate >= 0.08:
        status = "PASSING WITH RETRIES"
        desc = f"All {valid_build_total} builds succeeded with moderate retries ({run_fail_rate * 100:.1f}% run fail rate)."
    else:
        status = "MOSTLY PASSING"
        desc = f"Rare retries ({run_fail_rate * 100:.1f}% run fail rate across {valid_build_total} builds, 0 build failures)."

    return DiagnosticResult(
        status=status,
        description=desc,
        build_clean_pass=build_clean_pass,
        build_pass_on_retry=build_pass_on_retry,
        build_pass=build_pass,
        build_fail=build_fail,
        build_skip=build_skip,
        valid_build_total=valid_build_total,
        build_fail_rate=build_fail_rate,
        build_pass_rate=build_pass_rate,
        run_pass=run_pass,
        run_fail=run_fail,
        valid_run_total=valid_run_total,
        run_fail_rate=run_fail_rate,
        run_pass_rate=run_pass_rate,
        tail_fail_streak=tail_fail_streak,
        tail_pass_streak=tail_pass_streak,
        pre_streak_pass_count=pre_streak_pass_count,
        pre_streak_fail_count=pre_streak_fail_count,
        regression_score=regression_score,
        recovery_score=recovery_score,
        history_glyphs=history_glyphs,
        streak_start=streak_start,
    )
