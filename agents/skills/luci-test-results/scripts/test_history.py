#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Query historical test verdicts, daily pass/fail aggregates, and regression dates."""

import argparse
from collections import defaultdict
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from luci_client import run_prpc


def query_tests(project, test_query):
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
    project,
    test_id,
    limit=1000,
    builder=None,
    bucket=None,
    subrealm=None,
    variant_hash=None,
    device_os=None,
    device_type=None,
    os_name=None,
    test_suite=None,
    verbose=True,
):
    """Queries LUCI Analysis for test history verdicts with pagination and variant filters."""
    verdicts = []
    page_token = ""
    page = 1

    variant_def = {}
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

    predicate = {}
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

    while len(verdicts) < limit:
        target_size = min(100, limit - len(verdicts))
        if verbose:
            print(
                f"  -> Page {page}: Requesting {target_size} verdicts "
                f"(fetched {len(verdicts)}/{limit} so far)...",
                file=sys.stderr,
                flush=True,
            )

        payload = {
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

    if not verdicts:
        if verbose:
            print(
                f"No test history verdicts found for exact test ID: {test_id}",
                file=sys.stderr,
            )
        search_query = test_id.split("#")[-1] if "#" in test_id else test_id
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


def aggregate_by_day(verdicts):
    """Groups verdicts by date and computes pass/fail/flake aggregates."""
    daily = defaultdict(
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

    serialized = {}
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


def format_summary_table(daily_stats, test_id, builder=None, bucket=None):
    """Formats a human/agent readable summary table of test history."""
    filter_info = []
    if builder:
        filter_info.append(f"builder={builder}")
    if bucket:
        filter_info.append(f"bucket={bucket}")
    filter_str = (
        f" ({', '.join(filter_info)})"
        if filter_info
        else " (All Builders & Buckets)"
    )

    lines = []
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
    regression_start_date = None
    in_failure_streak = True

    for d in sorted_dates:
        p = daily_stats[d].get("PASS", 0)
        f = daily_stats[d].get("FAIL", 0)
        fl = daily_stats[d].get("FLAKE", 0)
        tot = daily_stats[d].get("TOTAL", 0)
        num_variants = daily_stats[d].get("VARIANTS", 0)
        rate = daily_stats[d].get("FAIL_RATE", 0.0)

        if in_failure_streak:
            if f > 0:
                regression_start_date = d
            elif regression_start_date is not None:
                in_failure_streak = False

        row = (
            f"{d:<12} | {p:<6} | {f:<6} | {fl:<6} | {tot:<6} | "
            f"{num_variants:<8} | {rate:8.1f}%"
        )
        lines.append(row)

    lines.append("=" * 72)
    if regression_start_date:
        lines.append(
            "Regression Start Indicator: Failures detected starting on "
            f"{regression_start_date}"
        )
    else:
        lines.append(
            "Regression Start Indicator: No UNEXPECTED failures detected in "
            "fetched history."
        )
    return "\n".join(lines)


def main():
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
        default=1000,
        help="Maximum verdicts to fetch across pages (default: 1000)",
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
