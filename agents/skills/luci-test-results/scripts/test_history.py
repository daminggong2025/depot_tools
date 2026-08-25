#!/usr/bin/env python3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Query historical test verdicts by builder, daily pass/fail aggregates, and diagnostic statuses."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from luci_client import run_prpc  # noqa: E402
from test_history_diagnostics import (  # noqa: E402
    BuildVerdict,
    evaluate_diagnostics,
    verdict_to_build,
)
from test_history_formatter import format_summary_table  # noqa: E402

DEFAULT_LIMIT = 50  # Default depth when zooming or querying a single builder
DEFAULT_SURVEY_LIMIT = (
    10  # Initial sample size per builder for multi-builder discovery
)
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
    """Queries LUCI Analysis for variantHash -> project/bucket/builder mapping."""
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
            def_map = v.get("variant", {}).get("def", {})
            builder = def_map.get("builder")
            bucket = def_map.get("bucket", "ci")
            if v_hash and builder:
                mapping[v_hash] = f"{project}/{bucket}/{builder}"
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
        predicate["subRealm"] = subrealm

    if verbose:
        filter_parts = []
        if builder:
            filter_parts.append(f"builder={builder}")
        if bucket:
            filter_parts.append(f"bucket={bucket}")
        filter_desc = (
            f" [filters: {', '.join(filter_parts)}]" if filter_parts else ""
        )
        print(
            f"[LUCI Analysis] Fetching test history for '{test_id}'{filter_desc} (limit: {limit})...",
            file=sys.stderr,
            flush=True,
        )

    while len(verdicts) < limit and page <= max_pages:
        remaining = limit - len(verdicts)
        batch_size = min(100, remaining)
        req_payload: dict[str, Any] = {
            "project": project,
            "testId": test_id,
            "pageSize": batch_size,
            "predicate": predicate,
        }
        if page_token:
            req_payload["pageToken"] = page_token

        if verbose:
            print(
                f"  -> Page {page}: Requesting {batch_size} verdicts "
                f"(fetched {len(verdicts)}/{limit} so far)...",
                file=sys.stderr,
                flush=True,
            )

        res = run_prpc(
            "analysis.api.luci.app",
            "luci.analysis.v1.TestHistory.Query",
            req_payload,
        )
        if res is None:
            if verbose:
                print(
                    f"[LUCI Analysis] Error: RPC returned empty or failed on page {page}.",
                    file=sys.stderr,
                )
            break

        batch = res.get("verdicts", [])
        if not batch:
            if verbose:
                print(
                    f"[LUCI Analysis] Page {page} returned 0 verdicts.",
                    file=sys.stderr,
                )
            break

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


def fetch_multi_builder_verdicts(
    project: str,
    test_id: str,
    zoom_limit: int = DEFAULT_LIMIT,
    survey_limit: int = DEFAULT_SURVEY_LIMIT,
    bucket: str | None = None,
    subrealm: str | None = None,
    variant_hash: str | None = None,
    device_os: str | None = None,
    device_type: str | None = None,
    os_name: str | None = None,
    test_suite: str | None = None,
    verbose: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Discovers builders with a fast 10-verdict survey, then zooms into interesting builders."""
    var_filter: dict[str, str] = {}
    if bucket:
        var_filter["bucket"] = bucket
    if device_os:
        var_filter["device_os"] = device_os
    if device_type:
        var_filter["device_type"] = device_type
    if os_name:
        var_filter["os"] = os_name
    if test_suite:
        var_filter["test_suite"] = test_suite

    predicate: dict[str, Any] = {}
    if var_filter:
        predicate["variantPredicate"] = {"contains": {"def": var_filter}}

    if verbose:
        filter_parts = []
        if bucket:
            filter_parts.append(f"bucket={bucket}")
        filter_desc = f" [{', '.join(filter_parts)}]" if filter_parts else ""
        print(
            f"[LUCI Analysis] Discovering active builders for '{test_id}'{filter_desc}...",
            file=sys.stderr,
            flush=True,
        )

    variant_builders = query_variant_builders(
        project, test_id, predicate=predicate
    )

    distinct_targets: list[tuple[str, str]] = []
    seen = set()
    for full_id in variant_builders.values():
        parts = full_id.split("/")
        if len(parts) == 3:
            p, bkt, bld = parts
            key = (bld, bkt)
            if key not in seen:
                seen.add(key)
                distinct_targets.append(key)

    distinct_targets.sort(key=lambda t: (t[1], t[0]))

    if not distinct_targets:
        verdicts = fetch_test_history_verdicts(
            project,
            test_id,
            limit=zoom_limit,
            bucket=bucket,
            subrealm=subrealm,
            variant_hash=variant_hash,
            device_os=device_os,
            device_type=device_type,
            os_name=os_name,
            test_suite=test_suite,
            verbose=verbose,
        )
        return verdicts, variant_builders

    if verbose:
        display_names = [
            f"{project}/{bkt}/{bld}" for bld, bkt in distinct_targets
        ]
        print(
            f"[LUCI Analysis] Running Stage 1 survey ({survey_limit} verdicts/builder) "
            f"across {len(distinct_targets)} builders: "
            f"{', '.join(display_names[:4])}{'...' if len(distinct_targets) > 4 else ''}",
            file=sys.stderr,
            flush=True,
        )

    survey_verdicts_by_builder: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for bld, bkt in distinct_targets:
        b_verdicts = fetch_test_history_verdicts(
            project,
            test_id,
            limit=survey_limit,
            builder=bld,
            bucket=bkt,
            subrealm=subrealm,
            variant_hash=variant_hash,
            device_os=device_os,
            device_type=device_type,
            os_name=os_name,
            test_suite=test_suite,
            verbose=False,
        )
        survey_verdicts_by_builder[(bld, bkt)] = b_verdicts

    interesting_targets: list[tuple[str, str]] = []
    for target, v_list in survey_verdicts_by_builder.items():
        has_issue = False
        for v in v_list:
            bv = verdict_to_build(v)
            if bv and (bv.is_fail or bv.outcome == "PASS_ON_RETRY"):
                has_issue = True
                break
        if has_issue:
            interesting_targets.append(target)

    if interesting_targets and zoom_limit > survey_limit:
        if verbose:
            zoom_names = [
                f"{project}/{bkt}/{bld}" for bld, bkt in interesting_targets
            ]
            print(
                f"[LUCI Analysis] Zooming into {len(interesting_targets)} active/failing builders "
                f"({zoom_limit} verdicts): {', '.join(zoom_names[:4])}"
                f"{'...' if len(interesting_targets) > 4 else ''}",
                file=sys.stderr,
                flush=True,
            )
        for bld, bkt in interesting_targets:
            b_zoomed = fetch_test_history_verdicts(
                project,
                test_id,
                limit=zoom_limit,
                builder=bld,
                bucket=bkt,
                subrealm=subrealm,
                variant_hash=variant_hash,
                device_os=device_os,
                device_type=device_type,
                os_name=os_name,
                test_suite=test_suite,
                verbose=False,
            )
            survey_verdicts_by_builder[(bld, bkt)] = b_zoomed

    all_verdicts: list[dict[str, Any]] = []
    for v_list in survey_verdicts_by_builder.values():
        all_verdicts.extend(v_list)

    return all_verdicts, variant_builders


def aggregate_by_builder_and_day(
    verdicts: list[dict[str, Any]],
    variant_builders: dict[str, str] | None = None,
    default_builder: str | None = None,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Groups verdicts by builder and date, evaluating diagnostic statuses."""
    mapping = variant_builders or {}
    now_utc = now or datetime.now(timezone.utc)

    builds_by_builder: dict[str, list[BuildVerdict]] = defaultdict(list)
    daily_builds_by_builder: dict[str, dict[str, list[BuildVerdict]]] = (
        defaultdict(lambda: defaultdict(list))
    )

    for v in verdicts:
        bv = verdict_to_build(v)
        if bv is None:
            continue
        v_hash = v.get("variantHash", "default")
        builder_id = (
            default_builder
            or mapping.get(v_hash)
            or v.get("variant", {}).get("def", {}).get("builder")
            or v_hash
        )

        builds_by_builder[builder_id].append(bv)
        date_str = bv.timestamp.strftime("%Y-%m-%d")
        daily_builds_by_builder[builder_id][date_str].append(bv)

    serialized: dict[str, dict[str, Any]] = {}
    for builder_id, b_list in builds_by_builder.items():
        diag_res = evaluate_diagnostics(b_list, now=now_utc)

        serialized_daily = {}
        daily_builds = daily_builds_by_builder[builder_id]
        for d, d_list in daily_builds.items():
            d_sorted = sorted(d_list, key=lambda b: b.timestamp, reverse=True)
            d_glyphs = []
            for b in d_sorted:
                if b.outcome == "PASS":
                    d_glyphs.append("P")
                elif b.outcome == "PASS_ON_RETRY":
                    d_glyphs.append("R")
                elif b.outcome == "FAIL":
                    d_glyphs.append("F")
                elif b.outcome == "SKIP":
                    d_glyphs.append("-")
                else:
                    d_glyphs.append(".")

            b_tot = len(d_sorted)
            b_pass = sum(1 for b in d_sorted if b.outcome == "PASS")
            b_fail = sum(1 for b in d_sorted if b.outcome == "FAIL")
            b_retry = sum(1 for b in d_sorted if b.outcome == "PASS_ON_RETRY")
            b_skip = sum(1 for b in d_sorted if b.outcome == "SKIP")
            v_tot = b_pass + b_fail + b_retry

            types_count = (
                (1 if b_pass > 0 else 0)
                + (1 if b_fail > 0 else 0)
                + (1 if b_retry > 0 else 0)
                + (1 if b_skip > 0 else 0)
            )
            glyph_str = "".join(d_glyphs) if types_count > 1 else ""

            build_fail_rate = (b_fail / v_tot * 100) if v_tot > 0 else 0.0
            tot_runs = b_pass + b_fail + 2 * b_retry
            failed_runs = b_fail + b_retry
            run_fail_rate = (
                (failed_runs / tot_runs * 100) if tot_runs > 0 else 0.0
            )

            serialized_daily[d] = {
                "PASS": b_pass,
                "FAIL": b_fail,
                "PASS_ON_RETRY": b_retry,
                "SKIP": b_skip,
                "TOTAL_BUILDS": b_tot,
                "BUILD_FAIL_RATE": round(build_fail_rate, 2),
                "RUN_FAIL_RATE": round(run_fail_rate, 2),
                "HISTORY": glyph_str,
            }

        serialized[builder_id] = {
            "daily": serialized_daily,
            "diagnostics": {
                "status": diag_res.status,
                "description": diag_res.description,
                "build_clean_pass": diag_res.build_clean_pass,
                "build_pass_on_retry": diag_res.build_pass_on_retry,
                "build_pass": diag_res.build_pass,
                "build_fail": diag_res.build_fail,
                "build_skip": diag_res.build_skip,
                "valid_build_total": diag_res.valid_build_total,
                "build_fail_rate": round(diag_res.build_fail_rate * 100, 2),
                "build_pass_rate": round(diag_res.build_pass_rate * 100, 2),
                "run_pass": diag_res.run_pass,
                "run_fail": diag_res.run_fail,
                "valid_run_total": diag_res.valid_run_total,
                "run_fail_rate": round(diag_res.run_fail_rate * 100, 2),
                "run_pass_rate": round(diag_res.run_pass_rate * 100, 2),
                "tail_fail_streak": diag_res.tail_fail_streak,
                "tail_pass_streak": diag_res.tail_pass_streak,
                "pre_streak_pass_count": diag_res.pre_streak_pass_count,
                "pre_streak_fail_count": diag_res.pre_streak_fail_count,
                "regression_score": diag_res.regression_score,
                "recovery_score": diag_res.recovery_score,
                "history_glyphs": diag_res.history_glyphs,
                "streak_start": (
                    diag_res.streak_start.isoformat()
                    if diag_res.streak_start
                    else None
                ),
            },
        }

    return serialized


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
        help="Full LUCI test ID (e.g., ://chrome/browser...)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum verdicts to fetch when zooming into interesting builders (default: {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--survey-limit",
        type=int,
        default=DEFAULT_SURVEY_LIMIT,
        help=f"Initial sample verdicts to fetch per builder during multi-builder survey (default: {DEFAULT_SURVEY_LIMIT})",
    )
    parser.add_argument(
        "--builder",
        help="Filter verdicts by builder name (e.g., android-13-x64-rel)",
    )
    parser.add_argument(
        "--bucket",
        help="Filter verdicts by bucket name (e.g., ci, try, findit)",
    )
    parser.add_argument(
        "--subrealm",
        help="Filter verdicts by subrealm (e.g., ci, try)",
    )
    parser.add_argument(
        "--variant-hash",
        help="Filter verdicts by variant hash hex string",
    )
    parser.add_argument(
        "--device-os",
        help="Filter verdicts by device OS (e.g., Android version code)",
    )
    parser.add_argument(
        "--device-type",
        help="Filter verdicts by device model (e.g., walleye, generic_x86_64)",
    )
    parser.add_argument(
        "--os-name",
        help="Filter verdicts by host OS (e.g., Linux, Windows, Mac)",
    )
    parser.add_argument(
        "--test-suite",
        help=(
            "Filter verdicts by test suite name (e.g., chrome_public_test_apk)"
        ),
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show full detailed daily tables for all builders (disables 100% pass/fail summary list)",
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

    if args.builder:
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
        variant_builders = None
        default_builder = (
            f"{args.project}/{args.bucket or 'ci'}/{args.builder}"
            if not args.builder.startswith(f"{args.project}/")
            else args.builder
        )
    else:
        verdicts, variant_builders = fetch_multi_builder_verdicts(
            args.project,
            args.test_id,
            zoom_limit=args.limit,
            survey_limit=args.survey_limit,
            bucket=args.bucket,
            subrealm=args.subrealm,
            variant_hash=args.variant_hash,
            device_os=args.device_os,
            device_type=args.device_type,
            os_name=args.os_name,
            test_suite=args.test_suite,
            verbose=not args.quiet,
        )
        default_builder = None

    if not verdicts:
        sys.exit(1)

    if args.raw:
        print(json.dumps(verdicts, indent=2))
    else:
        builder_daily = aggregate_by_builder_and_day(
            verdicts,
            variant_builders=variant_builders,
            default_builder=default_builder,
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
                    show_all=args.all,
                )
            )


if __name__ == "__main__":
    main()
