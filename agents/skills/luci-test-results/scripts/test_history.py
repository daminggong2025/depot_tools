#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Query historical test verdicts, daily pass/fail aggregates, and regression dates."""

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


def aggregate_by_day(
    verdicts: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Groups verdicts by date and computes pass/fail/flake aggregates."""
    daily: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "PASS": 0,
            "FAIL": 0,
            "FLAKE": 0,
            "TOTAL": 0,
            "VARIANTS": set(),
        }
    )
    for v in verdicts:
        partition_time = v.get("partitionTime", "")
        if not partition_time:
            continue
        date = partition_time[:10]
        status = v.get("status", "UNKNOWN")
        v_hash = v.get("variantHash", "default")
        daily[date]["TOTAL"] += 1
        daily[date]["VARIANTS"].add(v_hash)
        if status == "EXPECTED":
            daily[date]["PASS"] += 1
        elif status == "UNEXPECTED":
            daily[date]["FAIL"] += 1
        elif status == "FLAKY":
            daily[date]["FLAKE"] += 1
        else:
            daily[date][status] = daily[date].get(status, 0) + 1

    serialized: dict[str, dict[str, Any]] = {}
    for d, stats in daily.items():
        rate = (
            (stats["FAIL"] / stats["TOTAL"] * 100)
            if stats["TOTAL"] > 0
            else 0.0
        )
        serialized[d] = {
            "PASS": stats["PASS"],
            "FAIL": stats["FAIL"],
            "FLAKE": stats["FLAKE"],
            "TOTAL": stats["TOTAL"],
            "VARIANTS": len(stats["VARIANTS"]),
            "FAIL_RATE": round(rate, 2),
        }
    return serialized


def format_summary_table(
    daily_stats: dict[str, dict[str, Any]],
    test_id: str,
    builder: str | None = None,
    bucket: str | None = None,
) -> str:
    """Formats a human/agent readable summary table of test history."""
    filter_info: list[str] = []
    if builder:
        filter_info.append(f"builder={builder}")
    if bucket:
        filter_info.append(f"bucket={bucket}")
    filter_str = (
        f" ({', '.join(filter_info)})"
        if filter_info
        else " (All Builders & Buckets)"
    )

    lines: list[str] = []
    lines.append(f"Test History Aggregate Summary{filter_str}")
    lines.append(f"Test ID: {test_id}")
    lines.append("=" * 72)
    hdr = (
        f"{'Date':<12} | {'Pass':<6} | {'Fail':<6} | {'Flake':<6} | "
        f"{'Total':<6} | {'Variants':<8} | {'Fail Rate':<9}"
    )
    lines.append(hdr)
    lines.append("-" * 72)

    sorted_dates = sorted(daily_stats.keys(), reverse=True)
    if not sorted_dates:
        lines.append("No test history verdicts available.")
        lines.append("=" * 72)
        return "\n".join(lines)

    for d in sorted_dates:
        p = daily_stats[d].get("PASS", 0)
        f = daily_stats[d].get("FAIL", 0)
        fl = daily_stats[d].get("FLAKE", 0)
        tot = daily_stats[d].get("TOTAL", 0)
        num_variants = daily_stats[d].get("VARIANTS", 0)
        rate = daily_stats[d].get("FAIL_RATE", 0.0)

        row = (
            f"{d:<12} | {p:<6} | {f:<6} | {fl:<6} | {tot:<6} | "
            f"{num_variants:<8} | {rate:8.1f}%"
        )
        lines.append(row)

    lines.append("=" * 72)

    latest_date = sorted_dates[0]
    latest_fails = daily_stats[latest_date].get("FAIL", 0)
    latest_tot = daily_stats[latest_date].get("TOTAL", 0)
    latest_rate = daily_stats[latest_date].get("FAIL_RATE", 0.0)

    if latest_fails > 0:
        # Active failure streak starting from latest_date walking backwards
        streak_start = latest_date
        for d in sorted_dates[1:]:
            if daily_stats[d].get("FAIL", 0) > 0:
                streak_start = d
            else:
                break
        lines.append(
            f"Active Failure Streak: Failures detected starting on {streak_start} "
            f"(latest on {latest_date}: {latest_fails}/{latest_tot} failed, {latest_rate:.1f}% fail rate)"
        )
    else:
        # Latest date is passing
        past_failed_dates = [
            d for d in sorted_dates if daily_stats[d].get("FAIL", 0) > 0
        ]
        if past_failed_dates:
            most_recent_failed_date = past_failed_dates[0]
            lines.append(
                f"Past Failure Period: Most recent failure occurred on {most_recent_failed_date}. "
                f"Latest runs on {latest_date} are passing (0 failures)."
            )
        else:
            lines.append(
                "Regression Start Indicator: No UNEXPECTED failures detected in fetched history."
            )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Query LUCI Analysis test history to compute daily pass/fail/flake "
            "totals, fail rates, and detect regression start dates."
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
        daily = aggregate_by_day(verdicts)
        if args.json:
            print(json.dumps(daily, indent=2))
        else:
            print(
                format_summary_table(
                    daily,
                    args.test_id,
                    builder=args.builder,
                    bucket=args.bucket,
                )
            )


if __name__ == "__main__":
    main()
