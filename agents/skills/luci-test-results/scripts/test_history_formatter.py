#!/usr/bin/env python3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Formats historical test verdict aggregates into human and agent readable ASCII tables."""

from __future__ import annotations

from typing import Any


def evaluate_builder_status(
    builder_data: dict[str, Any] | dict[str, dict[str, Any]],
) -> str:
    """Formats the diagnostic status line for a single builder."""
    if "diagnostics" in builder_data:
        d = builder_data["diagnostics"]
        status = d.get("status", "UNKNOWN")
        desc = d.get("description", "")
        return f"Status: {status} ({desc})" if desc else f"Status: {status}"

    # Fallback for raw dictionary without precomputed diagnostics
    daily = builder_data.get("daily", builder_data)
    if not daily:
        return "Status: NOT RUNNING"
    sorted_dates = sorted(daily.keys(), reverse=True)
    latest_d = sorted_dates[0]
    f = daily[latest_d].get("FAIL", 0)
    r = daily[latest_d].get("PASS_ON_RETRY", 0)
    p = daily[latest_d].get("PASS", 0)
    tot = p + f + r
    b_fail_rate = (f / tot) if tot > 0 else 0.0
    tot_runs = p + f + 2 * r
    r_fail_rate = ((f + r) / tot_runs) if tot_runs > 0 else 0.0

    if tot == 0:
        return "Status: NOT RUNNING"
    elif f == 0:
        if r == 0:
            return "Status: PASSING"
        elif r_fail_rate >= 0.30:
            return "Status: PASSING ON HEAVY RETRY"
        elif r_fail_rate >= 0.08:
            return "Status: PASSING WITH RETRIES"
        else:
            return "Status: MOSTLY PASSING"
    else:
        if p == 0 or (b_fail_rate >= 0.70 and tot >= 3):
            return "Status: CONSISTENTLY FAILING"
        elif b_fail_rate >= 0.20:
            return "Status: HIGHLY FLAKY"
        else:
            return "Status: FLAKY"


def format_summary_table(
    builder_stats: dict[str, dict[str, Any]],
    test_id: str,
    builder: str | None = None,
    bucket: str | None = None,
    show_all: bool = False,
) -> str:
    """Formats a human/agent readable summary table grouped by builder."""
    filter_info: list[str] = []
    if builder:
        filter_info.append(f"builder={builder}")
    if bucket:
        filter_info.append(f"bucket={bucket}")
    filter_str = f" ({', '.join(filter_info)})" if filter_info else ""

    hdr = (
        f"{'Date':<12} | {'Pass':<6} | {'Fail':<6} | {'Pass on Retry':<13} | "
        f"{'Total':<6} | {'Build Fail %':<12} | {'History (newest -> oldest)'}"
    )
    divider = "=" * max(len(hdr) + 20, 85)
    subdivider = "-" * max(len(hdr) + 20, 85)

    lines: list[str] = []
    lines.append(f"Test History by Builder{filter_str}")
    lines.append(f"Test ID: {test_id}")

    if not builder_stats:
        lines.append(divider)
        lines.append("No test history verdicts available.")
        lines.append(divider)
        return "\n".join(lines)

    def builder_sort_key(b_name: str) -> tuple[int, str]:
        stats = builder_stats[b_name]
        status = stats.get("diagnostics", {}).get("status", "")
        if not status:
            status_line = evaluate_builder_status(stats)
            status = status_line.replace("Status: ", "").split(" (")[0]

        if status == "ACTIVE REGRESSION":
            return (0, b_name)
        if status == "CONSISTENTLY FAILING":
            return (1, b_name)
        if status == "HIGHLY FLAKY":
            return (2, b_name)
        if status == "FLAKY":
            return (3, b_name)
        if status in ("PASSING ON HEAVY RETRY", "PASSING WITH RETRIES"):
            return (4, b_name)
        if "RECOVERED" in status:
            return (5, b_name)
        if status in ("MOSTLY PASSING", "PASSING"):
            return (6, b_name)
        return (7, b_name)

    all_sorted = sorted(builder_stats.keys(), key=builder_sort_key)

    # Categorize builders for clean condensed reporting
    detailed_builders: list[str] = []
    passing_builders: list[tuple[str, int]] = []
    failing_builders: list[tuple[str, int]] = []
    skipped_builders: list[tuple[str, str, int]] = []

    for b_name in all_sorted:
        b_data = builder_stats[b_name]
        diag = b_data.get("diagnostics", {})
        st = diag.get("status", "")
        daily = b_data.get("daily", {})
        tot_pass = sum(d.get("PASS", 0) for d in daily.values())
        tot_fail = sum(d.get("FAIL", 0) for d in daily.values())
        tot_retry = sum(d.get("PASS_ON_RETRY", 0) for d in daily.values())
        tot_skip = sum(d.get("SKIP", 0) for d in daily.values())

        if show_all or len(all_sorted) == 1:
            detailed_builders.append(b_name)
        elif st in ("SKIPPED", "NOT RUNNING") or (
            tot_pass == 0 and tot_fail == 0 and tot_retry == 0
        ):
            skipped_builders.append((b_name, st or "NOT RUNNING", tot_skip))
        elif tot_fail == 0 and tot_retry == 0 and tot_pass > 0:
            passing_builders.append((b_name, tot_pass))
        elif (
            tot_pass == 0
            and tot_retry == 0
            and tot_fail > 0
            and st != "ACTIVE REGRESSION"
        ):
            failing_builders.append((b_name, tot_fail))
        else:
            detailed_builders.append(b_name)

    if len(all_sorted) > 1:
        failing_or_flaky = len(detailed_builders) + len(failing_builders)
        passing_count = len(passing_builders)
        lines.append(
            f"Builders Summary: {failing_or_flaky} failing/flaky/active, "
            f"{passing_count} 100% passing ({len(all_sorted)} total builders)"
        )

    # 1. Print detailed tables for active, regressing, or flaky builders
    for b_name in detailed_builders:
        b_data = builder_stats[b_name]
        daily = b_data.get("daily", b_data)

        lines.append(divider)
        lines.append(f"Builder: {b_name}")
        lines.append(subdivider)
        lines.append(hdr)
        lines.append(subdivider)

        sorted_dates = sorted(daily.keys(), reverse=True)
        for d in sorted_dates:
            p = daily[d].get("PASS", 0)
            f = daily[d].get("FAIL", 0)
            retry = daily[d].get("PASS_ON_RETRY", 0)
            tot = daily[d].get("TOTAL_BUILDS", 0)
            b_rate = daily[d].get("BUILD_FAIL_RATE", 0.0)
            g_str = daily[d].get("HISTORY", "")
            row = (
                f"{d:<12} | {p:<6} | {f:<6} | {retry:<13} | {tot:<6} | "
                f"{b_rate:11.1f}% | {g_str}"
            )
            lines.append(row)

        status_line = evaluate_builder_status(b_data)
        lines.append(subdivider)
        lines.append(status_line)

    lines.append(divider)

    # 2. Print condensed section for 100% FAILING builders (if any)
    if failing_builders:
        tot_fail_builds = sum(n for _, n in failing_builders)
        lines.append(
            f"100% FAILING Builders ({len(failing_builders)} builders - "
            f"{tot_fail_builds} out of {tot_fail_builds} builds failed):"
        )
        for b_name, n_fails in failing_builders:
            lines.append(f"  - {b_name} ({n_fails}/{n_fails} failed)")
        lines.append(divider)

    # 3. Print condensed section for 100% PASSING builders (if any)
    if passing_builders:
        tot_clean_builds = sum(n for _, n in passing_builders)
        lines.append(
            f"100% PASSING Builders ({len(passing_builders)} builders - "
            f"0 out of {tot_clean_builds} builds with failures/retries):"
        )
        for b_name, n_passes in passing_builders:
            lines.append(f"  - {b_name} ({n_passes} clean builds)")
        lines.append(divider)

    # 4. Print condensed section for skipped/inactive builders (if any)
    if skipped_builders:
        lines.append(
            f"Skipped / Inactive Builders ({len(skipped_builders)} builders):"
        )
        for b_name, st, n_skips in skipped_builders:
            desc = (
                f"{n_skips} skipped runs"
                if st == "SKIPPED"
                else "0 runs executed"
            )
            lines.append(f"  - {b_name} [{st}] ({desc})")
        lines.append(divider)

    if bucket == "try":
        lines.append(
            "Note: Results are from the 'try' bucket (CQ / presubmit runs reflect "
            "patchset-specific changes, not trunk regression history)."
        )

    return "\n".join(lines)
