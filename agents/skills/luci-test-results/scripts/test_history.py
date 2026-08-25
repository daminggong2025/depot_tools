#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Query historical test verdicts by builder, daily pass/fail aggregates, and regression dates."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from luci_client import run_prpc

DEFAULT_LIMIT = 500
MAX_PAGES = (
    25  # Safety cap (up to 2500 raw verdicts) to prevent excessive pRPC calls
)


def query_tests(project: str, test_query: str) -> list[str]:
    """Queries LUCI Analysis for test IDs matching a substring."""
    res = run_prpc(
        "analysis.api.luci.app",
        "luci.analysis.v1.TestHistory.QueryTests",
        {
            "project": project,
            "testIdSubstring": test_query,
        },
    )
    return res.get("testIds", []) if res else []


def query_variant_builders(
    project: str,
    test_id: str,
    predicate: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Queries LUCI Analysis for variantHash -> builder name mapping."""
    payload: dict[str, Any] = {
        "project": project,
        "testId": test_id,
    }
    if predicate:
        payload["predicate"] = predicate
    res = run_prpc(
        "analysis.api.luci.app",
        "luci.analysis.v1.TestHistory.QueryVariants",
        payload,
    )
    mapping = {}
    if res and "variants" in res:
        for v in res["variants"]:
            v_hash = v.get("variantHash")
            builder = v.get("variant", {}).get("def", {}).get("builder")
            if v_hash and builder:
                mapping[v_hash] = builder
    return mapping


def fetch_test_history_verdicts(
    project: str,
    test_id: str,
    limit: int = DEFAULT_LIMIT,
    builder: str | None = None,
    bucket: str | None = None,
    subrealm: str | None = None,
    variant_hash: str | None = None,
    device_os: str | None = None,
    device_type: str | None = None,
    os_name: str | None = None,
    test_suite: str | None = None,
    verbose: bool = True,
    max_pages: int = MAX_PAGES,
) -> list[dict[str, Any]]:
    """Queries LUCI Analysis for test history verdicts with pagination and variant filters."""
    verdicts: list[dict[str, Any]] = []
    page_token = ""
    page = 1

    variant_def: dict[str, str] = {}
    if builder:
        variant_def["builder"] = builder
    if bucket:
        variant_def["bucket"] = bucket
    if device_os:
        variant_def["device_os"] = device_os
    if device_type:
        variant_def["device_type"] = device_type
    if os_name:
        variant_def["os"] = os_name
    if test_suite:
        variant_def["test_suite"] = test_suite

    predicate: dict[str, Any] = {}
    if variant_def:
        predicate["variantPredicate"] = {"contains": {"def": variant_def}}
    if subrealm:
        predicate["subrealm"] = subrealm

    if verbose:
        print(
            f"[LUCI Analysis] Fetching test history for '{test_id}' "
            f"(limit: {limit})...",
            file=sys.stderr,
        )

    while len(verdicts) < limit and page <= max_pages:
        target_size = min(100, limit - len(verdicts))
        if verbose:
            print(
                f"  -> Page {page}: Requesting {target_size} verdicts "
                f"(fetched {len(verdicts)}/{limit} so far)...",
                file=sys.stderr,
                flush=True,
            )

        payload: dict[str, Any] = {
            "project": project,
            "testId": test_id,
            "predicate": predicate,
            "pageSize": target_size,
        }
        if page_token:
            payload["pageToken"] = page_token

        res = run_prpc(
            "analysis.api.luci.app",
            "luci.analysis.v1.TestHistory.Query",
            payload,
        )
        if not res or "verdicts" not in res:
            if verbose and res is not None:
                print(
                    "  -> No more verdicts available from LUCI API.",
                    file=sys.stderr,
                )
            break

        batch = res.get("verdicts", [])
        if not batch:
            if verbose:
                print("  -> Empty verdict batch received.", file=sys.stderr)
            break

        if variant_hash:
            batch = [v for v in batch if v.get("variantHash") == variant_hash]

        verdicts.extend(batch)
        if verbose:
            print(
                f"  -> Page {page}: Received {len(batch)} verdicts. "
                f"Total: {len(verdicts)}.",
                file=sys.stderr,
                flush=True,
            )

        page_token = res.get("nextPageToken", "")
        if not page_token:
            if verbose:
                print(
                    "[LUCI Analysis] Reached last page of results. "
                    f"Total verdicts fetched: {len(verdicts)}.",
                    file=sys.stderr,
                )
            break
        page += 1

    if verbose and len(verdicts) < limit and page > max_pages:
        print(
            f"  -> Reached safety cap of {max_pages} pages. "
            f"Total verdicts fetched: {len(verdicts)}.",
            file=sys.stderr,
        )

    if not verdicts:
        if verbose:
            print(
                f"No test history verdicts found for exact test ID: {test_id}",
                file=sys.stderr,
            )
        if "#" in test_id:
            search_query = test_id.split("#")[-1]
        else:
            cleaned = test_id.split("!")[-1].split(":")[-1].split("/")[-1]
            search_query = cleaned if cleaned else test_id

        candidates = query_tests(project, search_query)
        if candidates and verbose:
            print(
                "\nDid you mean one of these matching Test IDs in LUCI "
                "Analysis?",
                file=sys.stderr,
            )
            for tid in candidates[:10]:
                print(f"  {tid}", file=sys.stderr)

    return verdicts


def aggregate_by_builder_and_day(
    verdicts: list[dict[str, Any]],
    variant_builders: dict[str, str] | None = None,
    default_builder: str | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Groups verdicts by builder, then by date, computing pass/fail/flake aggregates."""
    mapping = variant_builders or {}
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(
            lambda: {
                "PASS": 0,
                "FAIL": 0,
                "FLAKE": 0,
                "TOTAL": 0,
            }
        )
    )

    for v in verdicts:
        partition_time = v.get("partitionTime", "")
        if not partition_time:
            continue
        date = partition_time[:10]
        status = v.get("status", "UNKNOWN")
        v_hash = v.get("variantHash", "default")
        builder = (
            default_builder
            or mapping.get(v_hash)
            or v.get("variant", {}).get("def", {}).get("builder")
            or v_hash
        )

        grouped[builder][date]["TOTAL"] += 1
        if status == "EXPECTED":
            grouped[builder][date]["PASS"] += 1
        elif status == "UNEXPECTED":
            grouped[builder][date]["FAIL"] += 1
        elif status == "FLAKY":
            grouped[builder][date]["FLAKE"] += 1
        else:
            grouped[builder][date][status] = (
                grouped[builder][date].get(status, 0) + 1
            )

    serialized: dict[str, dict[str, dict[str, Any]]] = {}
    for builder, daily in grouped.items():
        serialized[builder] = {}
        for d, stats in daily.items():
            rate = (
                (stats["FAIL"] / stats["TOTAL"] * 100)
                if stats["TOTAL"] > 0
                else 0.0
            )
            serialized[builder][d] = {
                "PASS": stats["PASS"],
                "FAIL": stats["FAIL"],
                "FLAKE": stats["FLAKE"],
                "TOTAL": stats["TOTAL"],
                "FAIL_RATE": round(rate, 2),
            }
    return serialized


def evaluate_builder_status(daily_stats: dict[str, dict[str, Any]]) -> str:
    """Evaluates the 4-state diagnostic status for a single builder's history."""
    sorted_dates = sorted(daily_stats.keys(), reverse=True)
    if not sorted_dates:
        return "No verdicts available"

    tot_all_fails = sum(daily_stats[d].get("FAIL", 0) for d in sorted_dates)
    tot_all_flakes = sum(daily_stats[d].get("FLAKE", 0) for d in sorted_dates)
    tot_all_runs = sum(daily_stats[d].get("TOTAL", 0) for d in sorted_dates)

    latest_date = sorted_dates[0]
    latest_fails = daily_stats[latest_date].get("FAIL", 0)
    latest_flakes = daily_stats[latest_date].get("FLAKE", 0)
    latest_tot = daily_stats[latest_date].get("TOTAL", 0)
    latest_fail_rate = daily_stats[latest_date].get("FAIL_RATE", 0.0)

    overall_fail_rate = (
        (tot_all_fails / tot_all_runs * 100) if tot_all_runs > 0 else 0.0
    )
    prior_fails = tot_all_fails - latest_fails
    prior_runs = tot_all_runs - latest_tot
    prior_fail_rate = (
        (prior_fails / prior_runs * 100) if prior_runs > 0 else 0.0
    )

    # 1. PASSING / CLEAN (Latest runs have 0 unrecovered failures)
    if latest_fails == 0:
        if latest_flakes > 0 or tot_all_flakes > 0:
            return (
                f"Status: FLAKY / RETRIED (Passed on retry / yellow runs on latest date, "
                f"0 unrecovered failures on {latest_date})."
            )
        elif tot_all_fails > 0:
            past_failed_dates = [
                d for d in sorted_dates if daily_stats[d].get("FAIL", 0) > 0
            ]
            most_recent_failed_date = past_failed_dates[0]
            return (
                f"Status: PASSING (Latest runs on {latest_date} are clean). "
                f"[Past failure occurred on {most_recent_failed_date}]."
            )
        else:
            return "Status: PASSING (100% passing across all fetched dates, no failures or retries)."

    # 2. PERSISTENTLY FAILING (High failure rate >= 80% across entire history)
    elif overall_fail_rate >= 80.0:
        return (
            f"Status: PERSISTENTLY FAILING ({overall_fail_rate:.1f}% failure rate "
            f"across all {len(sorted_dates)} fetched dates)."
        )

    # 3. ACTIVE REGRESSION (Elevated fail rate >= 20% on recent runs or jump from prior baseline)
    elif latest_fail_rate >= 20.0 or (
        latest_fail_rate - prior_fail_rate >= 20.0
    ):
        streak_start = latest_date
        for d in sorted_dates[1:]:
            if daily_stats[d].get("FAIL", 0) > 0:
                streak_start = d
            else:
                break
        return (
            f"Status: ACTIVE REGRESSION (Elevated failure rate of {latest_fail_rate:.1f}% "
            f"starting on {streak_start}; latest on {latest_date}: {latest_fails}/{latest_tot} failed)."
        )

    # 4. FLAKY / INTERMITTENT (Low failure rate < 20% on latest runs with concurrent passes)
    else:
        return (
            f"Status: FLAKY / INTERMITTENT (Low failure rate of {latest_fail_rate:.1f}% "
            f"on {latest_date} with concurrent passes)."
        )


def format_summary_table(
    builder_stats: dict[str, dict[str, dict[str, Any]]],
    test_id: str,
    builder: str | None = None,
    bucket: str | None = None,
) -> str:
    """Formats a human/agent readable summary table grouped by builder."""
    filter_info: list[str] = []
    if builder:
        filter_info.append(f"builder={builder}")
    if bucket:
        filter_info.append(f"bucket={bucket}")
    filter_str = f" ({', '.join(filter_info)})" if filter_info else ""

    lines: list[str] = []
    lines.append(f"Test History by Builder{filter_str}")
    lines.append(f"Test ID: {test_id}")

    if not builder_stats:
        lines.append("=" * 72)
        lines.append("No test history verdicts available.")
        lines.append("=" * 72)
        return "\n".join(lines)

    # Sort builders: failing/regressing builders first, passing builders last
    def builder_sort_key(b_name: str) -> tuple[int, str]:
        stats = builder_stats[b_name]
        status = evaluate_builder_status(stats)
        if "ACTIVE REGRESSION" in status:
            return (0, b_name)
        if "PERSISTENTLY FAILING" in status:
            return (1, b_name)
        if "FLAKY" in status:
            return (2, b_name)
        return (3, b_name)

    sorted_builders = sorted(builder_stats.keys(), key=builder_sort_key)

    if len(sorted_builders) > 1:
        failing_or_flaky = sum(
            1
            for b in sorted_builders
            if "PASSING" not in evaluate_builder_status(builder_stats[b])
        )
        passing = len(sorted_builders) - failing_or_flaky
        lines.append(
            f"Builders Summary: {failing_or_flaky} failing/flaky, {passing} passing "
            f"({len(sorted_builders)} total builders)"
        )

    for b_name in sorted_builders:
        daily = builder_stats[b_name]
        lines.append("=" * 72)
        lines.append(f"Builder: {b_name}")
        lines.append("-" * 72)
        hdr = (
            f"{'Date':<12} | {'Pass':<6} | {'Fail':<6} | {'Flake':<6} | "
            f"{'Total':<6} | {'Fail Rate':<9}"
        )
        lines.append(hdr)
        lines.append("-" * 72)

        sorted_dates = sorted(daily.keys(), reverse=True)
        for d in sorted_dates:
            p = daily[d].get("PASS", 0)
            f = daily[d].get("FAIL", 0)
            fl = daily[d].get("FLAKE", 0)
            tot = daily[d].get("TOTAL", 0)
            rate = daily[d].get("FAIL_RATE", 0.0)
            row = (
                f"{d:<12} | {p:<6} | {f:<6} | {fl:<6} | {tot:<6} | {rate:8.1f}%"
            )
            lines.append(row)

        status_line = evaluate_builder_status(daily)
        lines.append("-" * 72)
        lines.append(status_line)

    lines.append("=" * 72)

    if bucket == "try":
        lines.append(
            "Note: Results are from the 'try' bucket (CQ / presubmit runs reflect "
            "patchset-specific changes, not trunk regression history)."
        )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Query LUCI Analysis test history to compute daily pass/fail/flake "
            "totals by builder, fail rates, and detect regression start dates."
        )
    )
    parser.add_argument(
        "--project",
        default="chromium",
        help="LUCI project name ('chromium' or 'chrome', default: 'chromium')",
    )
    parser.add_argument(
        "--test-id",
        required=True,
        help="Full ResultDB test ID (e.g. ninja://...#testMethod)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum verdicts to fetch across pages (default: {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--builder",
        help="Filter verdicts by builder name (e.g., android-13-x64-rel)",
    )
    parser.add_argument(
        "--bucket",
        help="Filter verdicts by bucket (e.g., ci, try)",
    )
    parser.add_argument(
        "--subrealm",
        help="Filter verdicts by subrealm (e.g., ci, try)",
    )
    parser.add_argument(
        "--variant-hash",
        help="Filter verdicts by exact variant hash",
    )
    parser.add_argument(
        "--device-os",
        help="Filter verdicts by device OS (e.g., android-13)",
    )
    parser.add_argument(
        "--device-type",
        help="Filter verdicts by device model (e.g., pixel-7)",
    )
    parser.add_argument(
        "--os",
        dest="os_name",
        help="Filter verdicts by host OS (e.g., Linux, Windows, Mac)",
    )
    parser.add_argument(
        "--test-suite",
        help=(
            "Filter verdicts by test suite name (e.g., chrome_public_test_apk)"
        ),
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Output raw verdicts list instead of aggregated summary",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON format instead of formatted ASCII table",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress progress messages to stderr",
    )

    args = parser.parse_args()

    verdicts = fetch_test_history_verdicts(
        args.project,
        args.test_id,
        limit=args.limit,
        builder=args.builder,
        bucket=args.bucket,
        subrealm=args.subrealm,
        variant_hash=args.variant_hash,
        device_os=args.device_os,
        device_type=args.device_type,
        os_name=args.os_name,
        test_suite=args.test_suite,
        verbose=not args.quiet,
    )
    if not verdicts:
        sys.exit(1)

    if args.raw:
        print(json.dumps(verdicts, indent=2))
    else:
        variant_builders = None
        if not args.builder:
            variant_builders = query_variant_builders(
                args.project,
                args.test_id,
            )

        builder_daily = aggregate_by_builder_and_day(
            verdicts,
            variant_builders=variant_builders,
            default_builder=args.builder,
        )

        if args.json:
            print(json.dumps(builder_daily, indent=2))
        else:
            print(
                format_summary_table(
                    builder_daily,
                    args.test_id,
                    builder=args.builder,
                    bucket=args.bucket,
                )
            )


if __name__ == "__main__":
    main()
