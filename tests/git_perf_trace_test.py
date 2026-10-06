#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for Trace2 reduction and input limits."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

DEPOT_TOOLS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DEPOT_TOOLS_ROOT)

import git_perf_lib  # noqa: E402
import git_perf_trace  # noqa: E402


def _line(event, sid="session-private", thread="main"):
    value = {"sid": sid, "thread": thread, **event}
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


def _complete_trace(*region_lines):
    return b"".join(
        [
            _line(
                {
                    "event": "version",
                    "time": "2026-01-01T00:00:00.000000Z",
                    "evt": "2",
                    "exe": "PRIVATE-EXECUTABLE-CANARY",
                }
            ),
            _line(
                {
                    "event": "start",
                    "time": "2026-01-01T00:00:00.100000Z",
                    "t_abs": 0.1,
                    "argv": ["PRIVATE-ARGV-CANARY"],
                }
            ),
            *region_lines,
            _line(
                {
                    "event": "atexit",
                    "time": "2026-01-01T00:00:00.500000Z",
                    "t_abs": 0.5,
                    "code": 0,
                }
            ),
        ]
    )


class Trace2EventParserTest(unittest.TestCase):
    def test_nested_regions_have_exact_inclusive_exclusive_and_coverage(self):
        data = _complete_trace(
            _line(
                {
                    "event": "region_enter",
                    "time": "2026-01-01T00:00:00.200000Z",
                    "nesting": 1,
                    "category": "status",
                    "label": "untracked",
                }
            ),
            _line(
                {
                    "event": "region_enter",
                    "time": "2026-01-01T00:00:00.250000Z",
                    "nesting": 2,
                    "category": "dir",
                    "label": "read_directory",
                }
            ),
            _line(
                {
                    "event": "region_leave",
                    "time": "2026-01-01T00:00:00.300000Z",
                    "t_rel": 0.05,
                    "nesting": 2,
                    "category": "dir",
                    "label": "read_directory",
                }
            ),
            _line(
                {
                    "event": "region_leave",
                    "time": "2026-01-01T00:00:00.400000Z",
                    "t_rel": 0.2,
                    "nesting": 1,
                    "category": "status",
                    "label": "untracked",
                }
            ),
        )
        parser = git_perf_trace.Trace2EventParser()
        for offset in range(0, len(data), 17):
            parser.consume(data[offset : offset + 17])

        result = parser.finish()

        self.assertEqual("complete", result.state)
        phases = {phase["id"]: phase for phase in result.trace["phases"]}
        self.assertEqual(
            200_000_000, phases["status_untracked"]["inclusive_ns"]
        )
        self.assertEqual(
            150_000_000, phases["status_untracked"]["exclusive_ns"]
        )
        self.assertEqual(
            200_000_000, phases["status_untracked"]["active_wall_ns"]
        )
        self.assertEqual(50_000_000, phases["directory_scan"]["inclusive_ns"])
        self.assertEqual(
            500_000_000, result.trace["timeline"]["trace_window_ns"]
        )
        self.assertEqual(200_000_000, result.trace["timeline"]["coverage_ns"])
        self.assertEqual(0.4, result.trace["timeline"]["coverage_fraction"])
        self.assertEqual(1.0, result.trace["regions"]["matched_fraction"])
        serialized = json.dumps(result.trace)
        self.assertNotIn("PRIVATE", serialized)
        self.assertNotIn("session-private", serialized)

    def test_concurrent_intervals_use_union_for_active_wall(self):
        data = _complete_trace(
            _line(
                {
                    "event": "region_enter",
                    "time": "2026-01-01T00:00:00.200000Z",
                    "nesting": 1,
                    "category": "index",
                    "label": "refresh",
                },
                thread="worker-1",
            ),
            _line(
                {
                    "event": "region_enter",
                    "time": "2026-01-01T00:00:00.250000Z",
                    "nesting": 1,
                    "category": "index",
                    "label": "refresh",
                },
                thread="worker-2",
            ),
            _line(
                {
                    "event": "region_leave",
                    "time": "2026-01-01T00:00:00.300000Z",
                    "nesting": 1,
                    "category": "index",
                    "label": "refresh",
                },
                thread="worker-1",
            ),
            _line(
                {
                    "event": "region_leave",
                    "time": "2026-01-01T00:00:00.350000Z",
                    "nesting": 1,
                    "category": "index",
                    "label": "refresh",
                },
                thread="worker-2",
            ),
        )

        parser = git_perf_trace.Trace2EventParser()
        parser.consume(data)
        result = parser.finish()

        phase = result.trace["phases"][0]
        self.assertEqual(2, phase["count"])
        self.assertEqual(200_000_000, phase["inclusive_ns"])
        self.assertEqual(150_000_000, phase["active_wall_ns"])
        self.assertEqual(3, result.trace["threads"])

    def test_unknown_region_names_are_reduced_to_other(self):
        canary = "PRIVATE-REGION-CANARY"
        data = _complete_trace(
            _line(
                {
                    "event": "region_enter",
                    "time": "2026-01-01T00:00:00.200000Z",
                    "nesting": 1,
                    "category": canary,
                    "label": canary,
                }
            ),
            _line(
                {
                    "event": "region_leave",
                    "time": "2026-01-01T00:00:00.300000Z",
                    "nesting": 1,
                    "category": canary,
                    "label": canary,
                }
            ),
        )
        parser = git_perf_trace.Trace2EventParser()
        parser.consume(data)

        result = parser.finish()

        self.assertEqual("other", result.trace["phases"][0]["id"])
        self.assertNotIn(canary, json.dumps(result.trace))

    def test_multiple_sessions_with_same_thread_name_remain_isolated(self):
        parser = git_perf_trace.Trace2EventParser()
        for sid in ("PRIVATE-SESSION-A", "PRIVATE-SESSION-B"):
            parser.consume(
                _line(
                    {
                        "event": "version",
                        "time": "2026-01-01T00:00:00.000000Z",
                        "evt": "2",
                    },
                    sid=sid,
                )
            )
            parser.consume(
                _line(
                    {
                        "event": "start",
                        "time": "2026-01-01T00:00:00.100000Z",
                        "t_abs": 0.1,
                    },
                    sid=sid,
                )
            )
            parser.consume(
                _line(
                    {
                        "event": "atexit",
                        "time": "2026-01-01T00:00:00.200000Z",
                        "t_abs": 0.2,
                    },
                    sid=sid,
                )
            )

        result = parser.finish()

        self.assertEqual("complete", result.state)
        self.assertEqual(2, result.trace["sessions"])
        self.assertEqual(2, result.trace["threads"])
        self.assertNotIn("PRIVATE-SESSION", json.dumps(result.trace))

    def test_thread_limit_is_explicit(self):
        parser = git_perf_trace.Trace2EventParser(thread_limit=1)
        parser.consume(_line({"event": "version", "evt": "2"}, thread="first"))
        parser.consume(_line({"event": "version", "evt": "2"}, thread="second"))

        result = parser.finish()

        self.assertEqual("partial", result.state)
        self.assertIn("thread_limit_exceeded", result.trace["limits_reached"])

    def test_malformed_utf8_and_unmatched_regions_are_partial(self):
        parser = git_perf_trace.Trace2EventParser()
        parser.consume(b"\xff\n")
        parser.consume(
            _line(
                {
                    "event": "region_leave",
                    "time": "2026-01-01T00:00:00.300000Z",
                    "nesting": 1,
                    "category": "status",
                    "label": "print",
                }
            )
        )

        result = parser.finish()

        self.assertEqual("partial", result.state)
        self.assertEqual(1, result.trace["events"]["malformed"])
        self.assertIn("malformed_json_event", result.diagnostics)
        self.assertIn("unmatched_region_leave", result.diagnostics)

    def test_limits_are_bounded_and_reported(self):
        parser = git_perf_trace.Trace2EventParser(
            byte_limit=32,
            line_limit=16,
            event_limit=1,
            session_limit=1,
            thread_limit=1,
            diagnostic_limit=2,
        )
        parser.consume(b"x" * 100)

        result = parser.finish()

        self.assertEqual("failed", result.state)
        self.assertIn("byte_limit_exceeded", result.trace["limits_reached"])
        self.assertLessEqual(len(result.diagnostics), 2)

    def test_invalid_limits_and_late_consume_are_rejected(self):
        for kwargs in (
            {"event_limit": 0},
            {"thread_limit": True},
            {"line_limit": "1"},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    git_perf_trace.Trace2EventParser(**kwargs)
        parser = git_perf_trace.Trace2EventParser()
        first = parser.finish()
        self.assertIs(first, parser.finish())
        with self.assertRaises(RuntimeError):
            parser.consume(b"{}\n")


class Trace2InputTest(unittest.TestCase):
    def test_binary_stdin_is_repository_independent(self):
        result = git_perf_trace.parse_trace2_input(
            "-", stdin=io.BytesIO(_complete_trace())
        )

        self.assertEqual("complete", result.state)
        self.assertEqual(3, result.trace["events"]["total"])

    def test_regular_file_is_parsed_without_retaining_its_path(self):
        with tempfile.TemporaryDirectory(
            prefix="git-perf-trace-input-"
        ) as tempdir:
            canary = "PRIVATE-TRACE-PATH-CANARY"
            path = os.path.join(tempdir, canary)
            with open(path, "wb") as trace_file:
                trace_file.write(_complete_trace())

            report = git_perf_lib.collect_trace_input(path)

        self.assertEqual("complete", report.status)
        self.assertEqual("offline_trace", report.subject["kind"])
        self.assertNotIn(canary, git_perf_lib.report_json(report))
        self.assertNotIn(tempdir, git_perf_lib.report_json(report))

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symlinks")
    def test_symlink_input_is_rejected_without_following(self):
        with tempfile.TemporaryDirectory(
            prefix="git-perf-trace-symlink-"
        ) as tempdir:
            target = os.path.join(tempdir, "target")
            link = os.path.join(tempdir, "link")
            with open(target, "wb") as trace_file:
                trace_file.write(_complete_trace())
            os.symlink(target, link)

            result = git_perf_trace.parse_trace2_input(link)

        self.assertEqual("failed", result.state)
        self.assertEqual(("input_not_regular",), result.diagnostics)

    def test_missing_and_non_trace_input_are_failed_reports(self):
        missing = git_perf_lib.collect_trace_input(
            "/path/that/does/not/exist/PRIVATE"
        )
        malformed = git_perf_lib.collect_trace_input(
            "-", stdin=io.BytesIO(b"not trace2\n")
        )

        self.assertEqual("failed", missing.status)
        self.assertEqual("failed", malformed.status)
        self.assertNotIn("PRIVATE", git_perf_lib.report_json(missing))


@unittest.skipIf(shutil.which("git") is None, "requires Git")
class ActualGitTraceTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(
            prefix="git-perf-live-trace-"
        )
        self.addCleanup(self.tempdir.cleanup)
        self.repo = self.tempdir.name
        subprocess.run(
            ["git", "init", "--quiet", self.repo],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with open(
            os.path.join(self.repo, "PRIVATE-WORKTREE-CANARY"), "wb"
        ) as item:
            item.write(b"untracked\n")
        self.trace_target = os.path.join(self.repo, "PRIVATE-AMBIENT-TRACE")
        self.environ = os.environ.copy()
        self.environ.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TRACE2_EVENT": self.trace_target,
            }
        )

    @unittest.skipIf(
        sys.platform == "win32", "numeric-FD transport is POSIX-only"
    )
    def test_live_trace_uses_private_stream_and_discards_paths(self):
        report = git_perf_lib.collect_trace(
            cwd=self.repo,
            environ=self.environ,
            deadline_seconds=20,
        )

        self.assertEqual("complete", report.status)
        measurement = report.measurements[0]
        self.assertEqual("traced_sample", measurement["role"])
        self.assertEqual("complete", measurement["trace_state"])
        self.assertGreater(measurement["trace"]["events"]["total"], 0)
        self.assertGreater(len(measurement["trace"]["phases"]), 0)
        serialized = git_perf_lib.report_json(report)
        self.assertNotIn("PRIVATE", serialized)
        self.assertNotIn(self.repo, serialized)
        self.assertFalse(os.path.exists(self.trace_target))

    @unittest.skipIf(
        sys.platform == "win32", "numeric-FD transport is POSIX-only"
    )
    def test_diagnose_keeps_traced_sample_out_of_distribution(self):
        report = git_perf_lib.collect_diagnose(
            cwd=self.repo,
            environ=self.environ,
            samples=1,
            warmup=0,
            deadline_seconds=20,
        )

        self.assertEqual("complete", report.status)
        benchmark, trace = report.measurements
        self.assertEqual(git_perf_lib.STATUS_WORKLOAD_ID, benchmark["id"])
        self.assertEqual(
            f"{git_perf_lib.STATUS_WORKLOAD_ID}.trace2", trace["id"]
        )
        self.assertEqual(1, benchmark["summary"]["valid_n"])
        self.assertFalse(trace["execution"]["included_in_benchmark_summary"])
        self.assertIn("repository.index", {fact.id for fact in report.facts})
        self.assertEqual(
            [
                "trace_handshake",
                "benchmark",
                "trace",
                "strict_context_postflight",
            ],
            [phase["id"] for phase in report.tool["deadline"]["phases"]],
        )
        self.assertNotIn("PRIVATE", git_perf_lib.report_json(report))


if __name__ == "__main__":
    unittest.main()
