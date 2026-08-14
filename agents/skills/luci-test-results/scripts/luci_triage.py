#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""CLI tool for triaging LUCI test failures."""

import argparse
from collections import defaultdict
import json
import re
import subprocess
import sys


def run_prpc(service, method, payload):
    """Calls a pRPC service and returns the parsed JSON response."""
    cmd = ["prpc", "call", service, method]
    with subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        stdout, stderr = process.communicate(input=json.dumps(payload))
        if process.returncode != 0:
            print(
                f"Error calling {service}.{method}: {stderr}", file=sys.stderr
            )
            return None
        return json.loads(stdout)


def is_authenticated():
    """Checks if the user is logged into buildbucket."""
    try:
        res = subprocess.run(
            ["bb", "auth-info"], capture_output=True, text=True, check=False
        )
        return res.returncode == 0
    except FileNotFoundError:
        return False


def resolve_build_id(project, bucket, builder, build_number):
    """Resolves a builder and build number to a Buildbucket ID."""
    payload = {
        "builder": {"project": project, "bucket": bucket, "builder": builder},
        "buildNumber": int(build_number),
    }
    result = run_prpc(
        "cr-buildbucket.appspot.com", "buildbucket.v2.Builds.GetBuild", payload
    )
    return result.get("id") if result else None


def get_build(build_id):
    """Retrieves detailed information about a build."""
    if build_id.startswith("b"):
        build_id = build_id[1:]
    payload = {
        "id": build_id,
        "mask": {"fields": "id,builder,number,status,summaryMarkdown,output"},
    }
    return run_prpc(
        "cr-buildbucket.appspot.com", "buildbucket.v2.Builds.GetBuild", payload
    )


def find_cl_builds(cl_number, patchset=None, host=None, show_all=False):
    """Finds builds for a specific CL and patchset."""
    if not host:
        host = "chromium-review.googlesource.com"

    if not patchset:
        # Get latest patchset
        base_url = f"https://{host}/changes"
        cmd = ["curl", "-s", f"{base_url}/{cl_number}?o=CURRENT_REVISION"]
        try:
            output = (
                subprocess.check_output(cmd).decode("utf-8").splitlines()[1:]
            )
            data = json.loads("\n".join(output))
            patchset = data["revisions"][data["current_revision"]]["_number"]
        except Exception as e:
            print(f"Error getting latest patchset: {e}", file=sys.stderr)
            return []

    payload = {
        "predicate": {
            "gerritChanges": [
                {
                    "host": host,
                    "change": int(cl_number),
                    "patchset": int(patchset),
                }
            ]
        }
    }
    result = run_prpc(
        "cr-buildbucket.appspot.com",
        "buildbucket.v2.Builds.SearchBuilds",
        payload,
    )
    if not result or "builds" not in result:
        return []

    return [
        {
            "builder": b["builder"]["builder"],
            "status": b["status"],
            "id": b["id"],
        }
        for b in result["builds"]
        if show_all or b["status"] not in ("SUCCESS", "STARTED")
    ]


def list_failures(
    build_id, limit=None, include_exonerated=False, ignore_flaky=False
):
    """Lists failing and flaky test variants for a build, grouped by task."""
    if build_id.startswith("b"):
        build_id = build_id[1:]

    payload = {
        "invocations": [f"invocations/build-{build_id}"],
        "predicate": {"status": "UNEXPECTED_MASK"},
        "pageSize": 1000,
    }

    test_variants = []
    while True:
        result = run_prpc(
            "results.api.luci.app",
            "luci.resultdb.v1.ResultDB.QueryTestVariants",
            payload,
        )
        if not result:
            break

        test_variants.extend(result.get("testVariants", []))

        if "nextPageToken" not in result:
            break
        payload["pageToken"] = result["nextPageToken"]

    if not include_exonerated:
        test_variants = [
            tv
            for tv in test_variants
            if not (
                tv.get("status") == "EXONERATED"
                or tv.get("exonerated") is True
                or "exonerations" in tv
            )
        ]

    if ignore_flaky:
        test_variants = [
            tv for tv in test_variants if tv.get("status") != "FLAKY"
        ]

    status_order = {
        "UNEXPECTED": 0,
        "UNEXPECTEDLY_SKIPPED": 1,
        "FLAKY": 2,
        "EXONERATED": 3,
    }
    test_variants.sort(
        key=lambda tv: status_order.get(tv.get("status", ""), 99)
    )

    if limit is not None:
        test_variants = test_variants[:limit]

    tasks = {}
    for tv in test_variants:
        if "results" not in tv:
            continue

        # Find a failed result to report for this variant.
        results = [r["result"] for r in tv["results"]]
        failed_results = [r for r in results if r.get("status") != "PASS"]
        first_result = failed_results[0] if failed_results else results[0]
        res_name = first_result["name"]
        task_id = res_name.split("/")[1].replace("task-", "")

        failure = {
            "id": tv["testId"],
            "res": res_name,
            "err": first_result.get("failureReason", {}).get(
                "primaryErrorMessage", "No error"
            ),
            "status": tv.get("status", "UNEXPECTED"),
            "flaky": tv.get("status") == "FLAKY",
        }

        if task_id not in tasks:
            tasks[task_id] = []
        tasks[task_id].append(failure)

    return tasks


def fetch_log_snippet(res_name, raw=False):
    """Fetches a filtered snippet of the failure log."""
    payload = {"parent": res_name}
    result = run_prpc(
        "results.api.luci.app",
        "luci.resultdb.v1.ResultDB.ListArtifacts",
        payload,
    )
    if not result or "artifacts" not in result:
        msg = (
            "No artifacts found. This can happen if the build failed early "
            "or the logs were purged."
        )
        if not is_authenticated():
            msg += (
                " If the build is private, the user must run `bb auth-login` "
                "to authenticate."
            )
        return msg

    # Prefer "Test Log" or similar
    artifacts = result["artifacts"]
    target = next(
        (
            a
            for a in artifacts
            if a["artifactId"] in ("test_log", "stdout", "logs")
        ),
        artifacts[0],
    )

    url = target["fetchUrl"]
    cmd = ["curl", "-sL", url]
    try:
        output = subprocess.check_output(cmd).decode("utf-8", errors="ignore")
    except Exception as e:
        return f"Error fetching log: {e}"

    if raw:
        return output

    # Filter for interesting lines
    lines = output.splitlines()
    # Prioritize certain errors
    patterns = [
        r"AssertionError",
        r"FATAL",
        r"Exception",
        r"FAILED",
        r"FAIL",
        r"Leaking",
    ]
    combined_pattern = "|".join(patterns)

    interesting_indices = [
        i
        for i, line in enumerate(lines)
        if re.search(combined_pattern, line, re.I)
    ]

    if not interesting_indices:
        return "\n".join(lines[:100])  # Fallback to first 100 lines

    # Get windows around interesting lines
    output_lines = []
    last_idx = -1
    # If too many matches, prioritize the ones with "AssertionError" or "Exception"
    primary_indices = [
        i
        for i in interesting_indices
        if re.search(r"AssertionError|Exception", lines[i], re.I)
    ]
    if primary_indices:
        interesting_indices = primary_indices

    for idx in interesting_indices[:5]:  # limit to first 5 matches
        start = max(0, idx - 15)
        end = min(len(lines), idx + 10)
        if start > last_idx + 1 and last_idx != -1:
            output_lines.append("...")
        output_lines.extend(lines[max(last_idx + 1, start) : end])
        last_idx = end - 1

    return "\n".join(output_lines[:200])


def check_test(build_id, test_regex):
    """Checks if a test matching regex ran in the build using QueryTestResults."""
    if build_id.startswith("b"):
        build_id = build_id[1:]

    # Ensure regex matches partially by wrapping with .* if not already anchored
    if not test_regex.startswith(".*"):
        test_regex = ".*" + test_regex
    if not test_regex.endswith(".*"):
        test_regex = test_regex + ".*"

    payload = {
        "invocations": [f"invocations/build-{build_id}"],
        "predicate": {"testIdRegexp": test_regex, "expectancy": "ALL"},
        "pageSize": 1000,
    }

    test_results = []
    while True:
        result = run_prpc(
            "results.api.luci.app",
            "luci.resultdb.v1.ResultDB.QueryTestResults",
            payload,
        )
        if not result:
            print("Error: Failed to query ResultDB", file=sys.stderr)
            break

        test_results.extend(result.get("testResults", []))

        if "nextPageToken" not in result:
            break
        payload["pageToken"] = result["nextPageToken"]

    matching_tests = []
    for tr in test_results:
        matching_tests.append(
            {
                "id": tr["testId"],
                "status": tr.get("status"),
                "expected": tr.get("expected"),
            }
        )

    return matching_tests


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
    os=None,
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
    if os:
        variant_def["os"] = os
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
        res = run_prpc(
            "analysis.api.luci.app",
            "luci.analysis.v1.TestHistory.QueryTests",
            {
                "project": project,
                "testIdSubstring": search_query,
            },
        )
        if res and "testIds" in res:
            print(
                "\nDid you mean one of these matching Test IDs in LUCI "
                "Analysis?",
                file=sys.stderr,
            )
            for tid in res["testIds"][:10]:
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

    # Convert sets to lengths for JSON serialization
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
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "LUCI Test Triage & History CLI\n\n"
            "Query Buildbucket and ResultDB/LUCI Analysis for shard failures, "
            "builds, log snippets, test status, and historical verdicts."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Quick Recipes:\n"
            "  1. Resolve build ID:\n"
            "     luci_triage.py resolve-build-id --builder android-13-x64-rel "
            "--build-number 20418\n\n"
            "  2. List shard failures for a build:\n"
            "     luci_triage.py list-failures --build-id 8673466777718303649\n\n"
            "  3. Fetch filtered error log:\n"
            "     luci_triage.py fetch-log --res '<RESULT_ARTIFACT_NAME>'\n\n"
            "  4. Query test history & regression trend:\n"
            "     luci_triage.py test-history --test-id '<NINJA_TEST_ID>' "
            "--limit 1000\n"
        ),
    )
    subparsers = parser.add_subparsers(
        dest="command",
        title="Commands",
        description="Available triage and query subcommands",
        metavar="<command>",
        required=True,
    )

    # resolve-build-id
    p = subparsers.add_parser(
        "resolve-build-id",
        help="Resolve builder name and build number to a Buildbucket ID.",
        description=(
            "Resolves a project, bucket, builder, and build number to a "
            "canonical Buildbucket ID (e.g. '8673466777718303649')."
        ),
    )
    p.add_argument(
        "--project",
        default="chromium",
        help="LUCI project name (default: chromium)",
    )
    p.add_argument("--bucket", default="ci", help="LUCI bucket (default: ci)")
    p.add_argument(
        "--builder",
        required=True,
        help="Builder name (e.g. android-13-x64-rel)",
    )
    p.add_argument(
        "--build-number",
        required=True,
        help="Build number integer (e.g. 20418)",
    )

    # get-build
    p = subparsers.add_parser(
        "get-build",
        help="Retrieve detailed Buildbucket metadata and step summaries.",
        description=(
            "Retrieves metadata, output properties, and step statuses for a "
            "build ID."
        ),
    )
    p.add_argument(
        "--build-id",
        required=True,
        help="Buildbucket ID (e.g. 8673466777718303649)",
    )

    # find-cl-builds
    p = subparsers.add_parser(
        "find-cl-builds",
        help="Find recent Buildbucket builds triggered for a Gerrit CL.",
        description=(
            "Queries Buildbucket for builds associated with a Gerrit CL and "
            "patchset."
        ),
    )
    p.add_argument(
        "--cl", required=True, help="Gerrit CL number (e.g. 8260911)"
    )
    p.add_argument(
        "--patchset", help="Optional patchset number (defaults to latest)"
    )
    p.add_argument(
        "--host",
        default="chromium-review.googlesource.com",
        help="Gerrit host (default: chromium-review.googlesource.com)",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Show all builds, not just failed/running builds",
    )

    # list-failures
    p = subparsers.add_parser(
        "list-failures",
        help=(
            "List failing and flaky test variants for a build, grouped by "
            "Swarming task."
        ),
        description=(
            "Queries ResultDB for UNEXPECTED test failures in a build, "
            "grouping results by Swarming task ID to isolate shard crashes."
        ),
    )
    p.add_argument(
        "--build-id",
        required=True,
        help="Buildbucket ID (e.g. 8673466777718303649)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of failures to return",
    )
    p.add_argument(
        "--include-exonerated",
        action="store_true",
        help="Include exonerated test variants in the output",
    )
    p.add_argument(
        "--ignore-flaky",
        action="store_true",
        help=(
            "Ignore flaky test variants (only show unexonerated UNEXPECTED "
            "failures)"
        ),
    )

    # fetch-log
    p = subparsers.add_parser(
        "fetch-log",
        help=(
            "Fetch and extract filtered failure logs from a ResultDB artifact."
        ),
        description=(
            "Fetches test log artifacts from ResultDB and extracts key error "
            "lines (AssertionError, Exception, FATAL, FAIL, leaks)."
        ),
    )
    p.add_argument(
        "--res",
        required=True,
        help="Full result resource name from list-failures",
    )
    p.add_argument(
        "--raw",
        action="store_true",
        help="Return full raw log without regex filtering",
    )

    # check-test
    p = subparsers.add_parser(
        "check-test",
        help=(
            "Check if tests matching a regex ran in a build and inspect status."
        ),
        description=(
            "Queries ResultDB to determine whether test cases matching a regex "
            "pattern ran in a specific build and their final statuses."
        ),
    )
    p.add_argument(
        "--build-id",
        required=True,
        help="Buildbucket ID (e.g. 8673466777718303649)",
    )
    p.add_argument(
        "--test-regex", required=True, help="Regex pattern matching test ID"
    )

    # test-history
    p = subparsers.add_parser(
        "test-history",
        help=(
            "Query historical test verdicts, daily pass/fail aggregates, and "
            "regression dates."
        ),
        description=(
            "Paginates through LUCI Analysis test history to compute daily "
            "pass/fail/flake totals, fail rates, and detect regression start "
            "dates."
        ),
    )
    p.add_argument(
        "--project",
        default="chromium",
        help="LUCI project name ('chromium' or 'chrome', default: 'chromium')",
    )
    p.add_argument(
        "--test-id",
        required=True,
        help="Full ResultDB test ID (e.g. ninja://...#testMethod)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Maximum verdicts to fetch across pages (default: 1000)",
    )
    p.add_argument(
        "--builder",
        help="Filter verdicts by builder name (e.g., android-13-x64-rel)",
    )
    p.add_argument(
        "--bucket",
        help="Filter verdicts by bucket (e.g., ci, try)",
    )
    p.add_argument(
        "--subrealm",
        help="Filter verdicts by subrealm (e.g., ci, try)",
    )
    p.add_argument(
        "--variant-hash",
        help="Filter verdicts by exact variant hash",
    )
    p.add_argument(
        "--device-os",
        help="Filter verdicts by device OS (e.g., android-13)",
    )
    p.add_argument(
        "--device-type",
        help="Filter verdicts by device model (e.g., pixel-7)",
    )
    p.add_argument(
        "--os",
        help="Filter verdicts by host OS (e.g., Linux, Windows, Mac)",
    )
    p.add_argument(
        "--test-suite",
        help=(
            "Filter verdicts by test suite name (e.g., chrome_public_test_apk)"
        ),
    )
    p.add_argument(
        "--raw",
        action="store_true",
        help="Output raw verdicts list instead of aggregated summary",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Output JSON format instead of formatted ASCII table",
    )
    p.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress progress messages to stderr",
    )

    args = parser.parse_args()

    if args.command == "resolve-build-id":
        print(
            resolve_build_id(
                args.project, args.bucket, args.builder, args.build_number
            )
        )
    elif args.command == "get-build":
        print(json.dumps(get_build(args.build_id), indent=2))
    elif args.command == "find-cl-builds":
        print(
            json.dumps(
                find_cl_builds(args.cl, args.patchset, args.host, args.all),
                indent=2,
            )
        )
    elif args.command == "list-failures":
        print(
            json.dumps(
                list_failures(
                    args.build_id,
                    args.limit,
                    include_exonerated=args.include_exonerated,
                    ignore_flaky=args.ignore_flaky,
                ),
                indent=2,
            )
        )
    elif args.command == "fetch-log":
        print(fetch_log_snippet(args.res, args.raw))
    elif args.command == "check-test":
        print(json.dumps(check_test(args.build_id, args.test_regex), indent=2))
    elif args.command == "test-history":
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
            os=args.os,
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
