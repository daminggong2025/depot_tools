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

from unittest import mock

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
                    "evt": "4",
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
    @staticmethod
    def _region(*, duration=0.1, labels=True):
        enter = {
            "event": "region_enter",
            "nesting": 1,
            "time": "2026-01-01T00:00:00.200000Z",
        }
        leave = {
            "event": "region_leave",
            "nesting": 1,
            "t_rel": duration,
            "time": "2026-01-01T00:00:00.300000Z",
        }
        if labels:
            enter.update(category="index", label="refresh")
            leave.update(category="index", label="refresh")
        return enter, leave

    def test_optional_labels_and_unknown_phase_are_valid(self):
        for boundary in (None, 0, 1):
            with self.subTest(boundary=boundary):
                enter, leave = self._region(labels=False)
                if boundary is not None:
                    (enter, leave)[boundary].update(
                        category="index", label="refresh"
                    )
                result = git_perf_trace.parse_trace2_input(
                    "-",
                    stdin=io.BytesIO(
                        _complete_trace(_line(enter), _line(leave))
                    ),
                )
                self.assertEqual("complete", result.state)
                self.assertEqual(
                    100_000_000, result.trace["phases"][0]["inclusive_ns"]
                )

    def test_numeric_and_timestamp_extremes_do_not_crash(self):
        malformed = [
            b'{"number":' + b"9" * 5000 + b"}\n",
            _line(
                {
                    "event": "start",
                    "time": "2026-01-01T00:00:00Z",
                    "t_abs": 10**400,
                }
            ),
            _line(
                {
                    "event": "start",
                    "time": "0001-01-01T00:00:00+23:00",
                    "t_abs": 0,
                }
            ),
        ]
        for value in (float("nan"), float("inf"), -float("inf"), True, -1):
            malformed.append(
                _line(
                    {
                        "event": "start",
                        "time": "2026-01-01T00:00:00Z",
                        "t_abs": value,
                    }
                )
            )
        for prefix in malformed:
            with self.subTest(prefix=prefix[:40]):
                parser = git_perf_trace.Trace2EventParser()
                data = prefix + _complete_trace()
                for offset in range(0, len(data), 73):
                    parser.consume(data[offset : offset + 73])
                result = parser.finish()
                self.assertEqual("partial", result.state)
                self.assertGreaterEqual(result.trace["events"]["recognized"], 3)
                self.assertEqual(
                    "limited", result.trace["quality"]["integrity"]["state"]
                )

    def test_elapsed_duration_disagreement_limits_timeline(self):
        enter, leave = self._region(duration=0.9)
        result = git_perf_trace.parse_trace2_input(
            "-", stdin=io.BytesIO(_complete_trace(_line(enter), _line(leave)))
        )
        self.assertEqual("partial", result.state)
        self.assertIn("region_duration_disagrees", result.diagnostics)
        self.assertEqual(900_000_000, result.trace["phases"][0]["inclusive_ns"])
        self.assertEqual(
            "limited", result.trace["quality"]["temporal_consistency"]["state"]
        )

    def test_unknown_format_and_repeated_anchors_are_limited(self):
        data = _complete_trace()
        lines = data.splitlines(keepends=True)
        variants = [
            data.replace(b'"evt":"4"', b'"evt":"999"'),
            b"".join([lines[0], lines[1], lines[1], lines[-1]]),
            data + lines[-1],
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                result = git_perf_trace.parse_trace2_input(
                    "-", stdin=io.BytesIO(variant)
                )
                self.assertEqual("partial", result.state)
                self.assertEqual(
                    "limited", result.trace["quality"]["integrity"]["state"]
                )

    def test_supported_event_versions_share_region_contract(self):
        enter, leave = self._region()
        data = _complete_trace(_line(enter), _line(leave))
        for version in (b"2", b"3", b"4"):
            with self.subTest(version=version):
                result = git_perf_trace.parse_trace2_input(
                    "-",
                    stdin=io.BytesIO(
                        data.replace(b'"evt":"4"', b'"evt":"' + version + b'"')
                    ),
                )
                self.assertEqual("complete", result.state)
                self.assertEqual(
                    100_000_000, result.trace["phases"][0]["inclusive_ns"]
                )

    def test_wall_intervals_are_clipped_to_session_and_root_windows(self):
        enter, leave = self._region(duration=0.5)
        leave["time"] = "2026-01-01T00:00:00.700000Z"
        result = git_perf_trace.parse_trace2_input(
            "-", stdin=io.BytesIO(_complete_trace(_line(enter), _line(leave)))
        )
        self.assertEqual("partial", result.state)
        self.assertEqual(
            300_000_000, result.trace["phases"][0]["active_wall_ns"]
        )
        self.assertEqual(300_000_000, result.trace["timeline"]["coverage_ns"])

    def test_small_clock_sampling_difference_is_tolerated(self):
        enter, leave = self._region(duration=0.100001)
        result = git_perf_trace.parse_trace2_input(
            "-", stdin=io.BytesIO(_complete_trace(_line(enter), _line(leave)))
        )
        self.assertEqual("complete", result.state)
        self.assertEqual(100_001_000, result.trace["phases"][0]["inclusive_ns"])

    def test_parent_child_overlap_is_one_wall_interval(self):
        enter, leave = self._region()
        data = _complete_trace(_line(enter), _line(leave))
        child = data.replace(b"session-private", b"session-private/child")
        result = git_perf_trace.parse_trace2_input(
            "-", stdin=io.BytesIO(data + child)
        )
        self.assertEqual("complete", result.state)
        phase = result.trace["phases"][0]
        self.assertEqual(100_000_000, phase["active_wall_ns"])
        self.assertEqual(200_000_000, phase["aggregate_process_ns"])
        self.assertEqual(
            500_000_000, result.trace["timeline"]["trace_window_ns"]
        )
        self.assertEqual(100_000_000, result.trace["timeline"]["coverage_ns"])
        self.assertEqual(0.2, result.trace["timeline"]["coverage_fraction"])

    def test_unrelated_sessions_have_only_process_aggregates(self):
        enter, leave = self._region()
        data = _complete_trace(_line(enter), _line(leave))
        unrelated = data.replace(b"session-private", b"unrelated-private")
        report = git_perf_lib.collect_trace_input(
            "-", stdin=io.BytesIO(data + unrelated)
        )
        trace = report.measurements[0]["trace"]
        self.assertIsNone(trace["timeline"]["trace_window_ns"])
        self.assertIsNone(trace["phases"][0]["active_wall_ns"])
        self.assertEqual(
            200_000_000, trace["phases"][0]["aggregate_process_ns"]
        )
        self.assertNotIn("single_execution", str(trace["quality"]))
        self.assertNotIn("private", git_perf_lib.report_json(report))
        self.assertIn(
            "wall time unavailable", git_perf_lib.render_human(report)
        )

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
                    "t_rel": 0.1,
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
                    "t_rel": 0.1,
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
                    "t_rel": 0.1,
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
                        "evt": "4",
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
        parser.consume(_line({"event": "version", "evt": "4"}, thread="first"))
        parser.consume(_line({"event": "version", "evt": "4"}, thread="second"))

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
                    "t_rel": 0.1,
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
    def test_cli_extreme_values_from_file_and_stdin_produce_json(self):
        cases = [
            b'{"number":' + b"9" * 5000 + b"}\n",
            _line(
                {
                    "event": "start",
                    "time": "2026-01-01T00:00:00Z",
                    "t_abs": 10**400,
                }
            ),
            _line(
                {
                    "event": "start",
                    "time": "0001-01-01T00:00:00+23:00",
                    "t_abs": 0,
                }
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "PRIVATE-trace-input")
            for data in cases:
                with open(path, "wb") as stream:
                    stream.write(data + _complete_trace())
                for input_path in (path, "-"):
                    with self.subTest(input_path=input_path, data=data[:40]):
                        result = subprocess.run(
                            [
                                sys.executable,
                                "-B",
                                os.path.join(DEPOT_TOOLS_ROOT, "git_perf.py"),
                                "trace",
                                "--input",
                                input_path,
                                "--json",
                            ],
                            input=data + _complete_trace(),
                            capture_output=True,
                            timeout=5,
                        )
                        self.assertEqual(0, result.returncode, result.stderr)
                        self.assertEqual(
                            "partial", json.loads(result.stdout)["status"]
                        )
                        self.assertNotIn(b"Traceback", result.stderr)
                        self.assertNotIn(b"PRIVATE", result.stdout)

    def test_replaced_input_downgrades_nested_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "trace")
            with open(path, "wb") as stream:
                stream.write(_complete_trace())
            before = os.lstat(path)
            after = mock.Mock(
                st_dev=before.st_dev,
                st_ino=before.st_ino,
                st_size=before.st_size + 1,
                st_mtime_ns=before.st_mtime_ns,
            )
            with mock.patch(
                "git_perf_trace.os.lstat", side_effect=[before, after]
            ):
                result = git_perf_trace.parse_trace2_input(path)
        self.assertEqual("partial", result.state)
        self.assertEqual(
            "limited", result.trace["quality"]["integrity"]["state"]
        )
        self.assertIn("input_changed_during_read", result.diagnostics)

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
    def test_diagnose_detects_drift_between_individually_stable_phases(self):
        for dimension in (
            "selected_config",
            "observable_status",
            "selected_client",
            "repository",
        ):
            with self.subTest(dimension=dimension):
                actual = git_perf_lib.GitRunner.resolve(
                    "git", environ=self.environ
                )
                counts = {"config": 0, "status": 0, "version": 0}
                with tempfile.TemporaryDirectory() as other:
                    subprocess.run(
                        ["git", "init", "--quiet", other], check=True
                    )
                    with open(
                        os.path.join(other, "PRIVATE-WORKTREE-CANARY"), "wb"
                    ) as stream:
                        stream.write(b"untracked\n")

                    def run(args, **kwargs):
                        if args == ["version"]:
                            counts["version"] += 1
                            if (
                                dimension == "repository"
                                and counts["version"] == 4
                            ):
                                os.rename(
                                    os.path.join(self.repo, ".git"),
                                    os.path.join(other, "original.git"),
                                )
                                os.rename(
                                    os.path.join(other, ".git"),
                                    os.path.join(self.repo, ".git"),
                                )
                        if args == [
                            "config",
                            "--null",
                            "--get-regexp",
                            git_perf_lib._STATUS_CONFIG_PATTERN,
                        ]:
                            counts["config"] += 1
                            if (
                                dimension == "selected_config"
                                and counts["config"] == 3
                            ):
                                subprocess.run(
                                    [
                                        "git",
                                        "-C",
                                        self.repo,
                                        "config",
                                        "status.renames",
                                        "false",
                                    ],
                                    check=True,
                                )
                        if args == git_perf_lib.STATUS_WORKLOAD_ARGS:
                            counts["status"] += 1
                            if (
                                dimension == "observable_status"
                                and counts["status"] == 3
                            ):
                                os.rename(
                                    os.path.join(
                                        self.repo, "PRIVATE-WORKTREE-CANARY"
                                    ),
                                    os.path.join(
                                        self.repo, "PRIVATE-RENAMED-CANARY"
                                    ),
                                )
                        return actual.run(args, **kwargs)

                    runner = mock.Mock(executable=actual.executable, run=run)
                    identity = git_perf_lib._selected_executable_identity

                    identity_calls = 0

                    def selected_identity(selected):
                        nonlocal identity_calls
                        identity_calls += 1
                        value = identity(selected)
                        if (
                            dimension == "selected_client"
                            and identity_calls >= 3
                        ):
                            return (*value[:3], value[3] + 1)
                        return value

                    with mock.patch(
                        "git_perf_lib._selected_executable_identity",
                        side_effect=selected_identity,
                    ):
                        report = git_perf_lib.collect_diagnose(
                            cwd=self.repo,
                            environ=self.environ,
                            runner=runner,
                            samples=1,
                            warmup=0,
                            deadline_seconds=20,
                        )
                self.assertEqual("partial", report.status)
                self.assertEqual(
                    "observed_changed",
                    report.tool["phase_comparison"][dimension],
                )
                self.assertIsNotNone(report.measurements[0]["summary"])
                self.assertNotIn("PRIVATE", git_perf_lib.report_json(report))
                if dimension == "observable_status":
                    os.rename(
                        os.path.join(self.repo, "PRIVATE-RENAMED-CANARY"),
                        os.path.join(self.repo, "PRIVATE-WORKTREE-CANARY"),
                    )

    @unittest.skipIf(
        sys.platform == "win32", "numeric-FD transport is POSIX-only"
    )
    def test_diagnose_stops_every_phase_after_uncertain_handshake_cleanup(self):
        actual = git_perf_lib.GitRunner.resolve("git", environ=self.environ)
        result = git_perf_lib.GitResult(
            "success", 0, b"", False, "empty", 1, cleanup_uncertain=True
        )
        runner = mock.Mock(executable=actual.executable)
        runner.run.return_value = result
        report = git_perf_lib.collect_diagnose(
            cwd=self.repo,
            environ=self.environ,
            runner=runner,
            samples=1,
            warmup=0,
            deadline_seconds=20,
        )
        self.assertEqual(1, runner.run.call_count)
        self.assertEqual("failed", report.status)

    def test_malformed_gclient_preserves_git_facts(self):
        with open(os.path.join(self.repo, ".gclient"), "w") as config:
            config.write('solutions = [{"name": 123}]')
        report = git_perf_lib.collect_check(cwd=self.repo, environ=self.environ)
        facts = {fact.id: fact for fact in report.facts}
        self.assertEqual("partial", report.status)
        self.assertEqual("known", facts["repository.kind"].state)
        self.assertEqual("unavailable", facts["checkout.orchestrator"].state)

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
        self.assertEqual(
            "single_observation",
            measurement["quality"]["worktree_state_stability"],
        )
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
