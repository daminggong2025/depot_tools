#!/usr/bin/env python3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Reduce Git Trace2 EVENT input to phase timings safe for a report.

The parser treats every field as untrusted. It replaces the region names listed
below with fixed phase identifiers and discards raw events, paths, arguments,
messages, identifiers, and wall-clock timestamps.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime
import json
import math
import os
import stat

from collections import Counter, defaultdict
from typing import Any, BinaryIO, Optional

DEFAULT_TRACE_BYTE_LIMIT = 16 * 1024 * 1024
DEFAULT_TRACE_LINE_LIMIT = 256 * 1024
DEFAULT_TRACE_EVENT_LIMIT = 200_000
DEFAULT_TRACE_SESSION_LIMIT = 128
DEFAULT_TRACE_THREAD_LIMIT = 4096
DEFAULT_TRACE_NESTING_LIMIT = 64
DEFAULT_TRACE_STRING_LIMIT = 1024
DEFAULT_TRACE_DIAGNOSTIC_LIMIT = 32
TRACE_READ_CHUNK_BYTES = 64 * 1024
MAX_TRACE_DURATION_SECONDS = 24 * 60 * 60
# Elapsed clocks are sampled before EVENT serialization samples wall time.
# This is a disagreement threshold, not a claim of millisecond precision.
CLOCK_DISAGREEMENT_NS = 1_000_000
# The start/atexit and region contracts used here are shared by Git 2.25's
# EVENT v2, Git 2.39's v3, and Git 2.56's v4. Newer versions need review.
SUPPORTED_EVENT_VERSIONS = frozenset({"2", "3", "4"})

# Offline category and label values may contain private text. Only pairs listed
# below receive a fixed phase identifier; all other pairs count as `other`.
_PHASE_IDS = {
    ("cache_tree", "read"): "cache_tree_read",
    ("diff", "setup"): "diff_setup",
    ("diff", "write back to queue"): "diff_queue",
    ("dir", "read_directory"): "directory_scan",
    ("index", "do_read_index"): "index_read",
    ("index", "name-hash-init"): "index_name_hash",
    ("index", "preload"): "index_preload",
    ("index", "refresh"): "index_refresh",
    ("status", "index"): "status_index",
    ("status", "print"): "status_output",
    ("status", "untracked"): "status_untracked",
    ("status", "worktrees"): "status_worktrees",
    ("unpack_trees", "traverse_trees"): "tree_traversal",
    ("unpack_trees", "unpack_trees"): "unpack_trees",
}

_KNOWN_EVENTS = {
    "atexit",
    "child_exit",
    "child_ready",
    "child_start",
    "cmd_ancestry",
    "cmd_mode",
    "cmd_name",
    "cmd_path",
    "data",
    "data_json",
    "def_param",
    "def_repo",
    "error",
    "exit",
    "printf",
    "region_enter",
    "region_leave",
    "signal",
    "start",
    "thread_exit",
    "thread_start",
    "too_many_files",
    "version",
}


@dataclasses.dataclass(frozen=True)
class TraceParseResult:
    """Phase aggregates and parser state for one Trace2 stream."""

    state: str
    trace: dict[str, Any]
    diagnostics: tuple[str, ...]


@dataclasses.dataclass
class _Session:
    origin_ns: Optional[int] = None
    window_end_ns: Optional[int] = None
    format_version: Optional[str] = None


@dataclasses.dataclass
class _RegionFrame:
    start_ns: int
    nesting: int
    category: str
    label: str
    phase_id: str
    direct_child_intervals: list[tuple[int, int]] = dataclasses.field(
        default_factory=list
    )
    direct_child_elapsed_ns: int = 0


@dataclasses.dataclass
class _PhaseAggregate:
    count: int = 0
    inclusive_ns: int = 0
    exclusive_ns: int = 0
    intervals_by_session: dict[str, list[tuple[int, int]]] = dataclasses.field(
        default_factory=lambda: defaultdict(list)
    )


class Trace2EventParser:
    """Incrementally reduce newline-delimited Trace2 EVENT JSON."""

    def __init__(
        self,
        *,
        byte_limit: int = DEFAULT_TRACE_BYTE_LIMIT,
        line_limit: int = DEFAULT_TRACE_LINE_LIMIT,
        event_limit: int = DEFAULT_TRACE_EVENT_LIMIT,
        session_limit: int = DEFAULT_TRACE_SESSION_LIMIT,
        thread_limit: int = DEFAULT_TRACE_THREAD_LIMIT,
        nesting_limit: int = DEFAULT_TRACE_NESTING_LIMIT,
        string_limit: int = DEFAULT_TRACE_STRING_LIMIT,
        diagnostic_limit: int = DEFAULT_TRACE_DIAGNOSTIC_LIMIT,
    ):
        limits = (
            byte_limit,
            line_limit,
            event_limit,
            session_limit,
            thread_limit,
            nesting_limit,
            string_limit,
            diagnostic_limit,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in limits
        ):
            raise ValueError("Trace2 parser limits must be positive integers")

        self._byte_limit = byte_limit
        self._line_limit = line_limit
        self._event_limit = event_limit
        self._session_limit = session_limit
        self._thread_limit = thread_limit
        self._nesting_limit = nesting_limit
        self._string_limit = string_limit
        self._diagnostic_limit = diagnostic_limit

        self._buffer = bytearray()
        self._discarding_line = False
        self._finished: Optional[TraceParseResult] = None
        self._bytes_seen = 0
        self._lines_seen = 0
        self._events_seen = 0
        self._recognized_events = 0
        self._unknown_events = 0
        self._malformed_events = 0
        self._event_counts: Counter[str] = Counter()
        self._limits_reached: set[str] = set()
        self._diagnostics: list[str] = []

        self._sessions: dict[str, _Session] = {}
        self._threads: set[tuple[str, str]] = set()
        self._stacks: dict[tuple[str, str], list[_RegionFrame]] = defaultdict(
            list
        )
        self._phases: dict[str, _PhaseAggregate] = defaultdict(_PhaseAggregate)
        self._all_intervals: dict[str, list[tuple[int, int]]] = defaultdict(
            list
        )
        self._format_versions: set[str] = set()
        self._region_enters = 0
        self._region_leaves = 0
        self._matched_regions = 0
        self._unmatched_enters = 0
        self._unmatched_leaves = 0
        self._clock_anomalies = 0

    def consume(self, chunk: bytes) -> None:
        """Consume raw bytes without retaining complete input events."""
        if self._finished is not None:
            raise RuntimeError("Trace2 parser is already finished")
        if not isinstance(chunk, bytes):
            raise TypeError("Trace2 parser chunks must be bytes")
        if not chunk:
            return

        previous = self._bytes_seen
        self._bytes_seen += len(chunk)
        available = max(0, self._byte_limit - previous)
        if len(chunk) > available:
            self._limit("byte_limit_exceeded")
        if available <= 0:
            return
        self._consume_bounded(chunk[:available])

    def mark_transport_limit(self, reason: str) -> None:
        """Record a read or capture limit detected outside the parser."""
        if self._finished is not None:
            raise RuntimeError("Trace2 parser is already finished")
        self._limit(reason)

    def finish(self) -> TraceParseResult:
        """Return an immutable, path-free aggregate."""
        if self._finished is not None:
            return self._finished

        if self._discarding_line or self._buffer:
            self._malformed("truncated_line")
        for stack in self._stacks.values():
            self._unmatched_enters += len(stack)
        if self._unmatched_enters:
            self._diagnose("unmatched_region_enter")

        # Git appends /<child-id> to the parent's SID. Only a stream with one
        # observed root supports one wall timeline. Unrelated offline sessions
        # still have useful process aggregates, but no combined wall duration.
        roots = [
            sid
            for sid in self._sessions
            if not any(
                sid.startswith(parent + "/")
                for parent in self._sessions
                if parent != sid
            )
        ]
        root = self._sessions[roots[0]] if len(roots) == 1 else None
        root_window = (
            (root.origin_ns, root.window_end_ns)
            if root is not None
            and root.origin_ns is not None
            and root.window_end_ns is not None
            else None
        )
        if len(roots) > 1:
            self._diagnose("multiple_session_roots")

        process_window_ns = 0
        complete_windows = 0
        clipped_by_session = {}
        for session_id, session in self._sessions.items():
            if (
                session.origin_ns is None
                or session.window_end_ns is None
                or session.window_end_ns < session.origin_ns
            ):
                continue
            complete_windows += 1
            process_window_ns += session.window_end_ns - session.origin_ns
            if root_window and (
                session.origin_ns < root_window[0] - CLOCK_DISAGREEMENT_NS
                or session.window_end_ns
                > root_window[1] + CLOCK_DISAGREEMENT_NS
            ):
                self._clock_problem("session_outside_root_window")
            clipped = []
            for start_ns, end_ns in self._all_intervals.get(session_id, ()):
                if (
                    start_ns < session.origin_ns - CLOCK_DISAGREEMENT_NS
                    or end_ns > session.window_end_ns + CLOCK_DISAGREEMENT_NS
                ):
                    self._clock_problem("region_outside_trace_window")
                start_ns = max(start_ns, session.origin_ns)
                end_ns = min(end_ns, session.window_end_ns)
                if end_ns > start_ns:
                    clipped.append((start_ns, end_ns))
            clipped_by_session[session_id] = clipped

        def root_union(intervals):
            if root_window is None:
                return None
            start, end = root_window
            return _interval_union_length(
                [
                    (max(a, start), min(b, end))
                    for a, b in intervals
                    if min(b, end) > max(a, start)
                ]
            )

        phases = []
        for phase_id in sorted(self._phases):
            aggregate = self._phases[phase_id]
            phase_intervals = []
            for sid, intervals in aggregate.intervals_by_session.items():
                session = self._sessions[sid]
                if session.origin_ns is None or session.window_end_ns is None:
                    continue
                phase_intervals.extend(
                    (max(a, session.origin_ns), min(b, session.window_end_ns))
                    for a, b in intervals
                    if min(b, session.window_end_ns) > max(a, session.origin_ns)
                )
            phases.append(
                {
                    "id": phase_id,
                    "count": aggregate.count,
                    "inclusive_ns": aggregate.inclusive_ns,
                    "exclusive_ns": aggregate.exclusive_ns,
                    "active_wall_ns": root_union(phase_intervals),
                    "aggregate_process_ns": sum(
                        _interval_union_length(intervals)
                        for intervals in aggregate.intervals_by_session.values()
                    ),
                }
            )
        trace_window_ns = root_window[1] - root_window[0] if root_window else 0
        coverage_ns = root_union(
            [
                interval
                for intervals in clipped_by_session.values()
                for interval in intervals
            ]
        )
        unsupported_format = any(
            session.format_version not in SUPPORTED_EVENT_VERSIONS
            for session in self._sessions.values()
        )
        if unsupported_format:
            self._diagnose("event_format_version_unsupported")

        coverage_fraction = (
            min(1.0, coverage_ns / trace_window_ns)
            if trace_window_ns > 0
            else None
        )
        region_denominator = self._region_enters + self._region_leaves
        matched_region_fraction = (
            2 * self._matched_regions / region_denominator
            if region_denominator
            else None
        )

        if not self._events_seen:
            state = "failed"
            integrity = "invalid"
        elif (
            self._malformed_events
            or self._limits_reached
            or self._unmatched_enters
            or self._unmatched_leaves
            or self._clock_anomalies
            or complete_windows < len(self._sessions)
            or unsupported_format
        ):
            state = "partial"
            integrity = "limited"
        else:
            state = "complete"
            integrity = "sufficient"

        temporal = (
            "sufficient"
            if complete_windows == len(self._sessions)
            and not self._clock_anomalies
            and bool(self._sessions)
            and root_window is not None
            and not unsupported_format
            else "limited"
            if self._events_seen
            else "unavailable"
        )
        coverage_quality = (
            "unavailable"
            if coverage_fraction is None
            else "sufficient"
            if temporal == "sufficient" and integrity == "sufficient"
            else "limited"
        )
        trace = {
            "format": "trace2_event",
            "format_versions": sorted(self._format_versions),
            "bytes_seen": self._bytes_seen,
            "lines_seen": self._lines_seen,
            "events": {
                "total": self._events_seen,
                "recognized": self._recognized_events,
                "unknown": self._unknown_events,
                "malformed": self._malformed_events,
                "by_type": {
                    name: self._event_counts[name]
                    for name in sorted(self._event_counts)
                },
            },
            "sessions": len(self._sessions),
            "threads": len(self._threads),
            "regions": {
                "enter": self._region_enters,
                "leave": self._region_leaves,
                "matched": self._matched_regions,
                "unmatched_enter": self._unmatched_enters,
                "unmatched_leave": self._unmatched_leaves,
                "matched_fraction": matched_region_fraction,
            },
            "timeline": {
                "scope": "root_session_tree" if root_window else "unavailable",
                "complete_session_windows": complete_windows,
                "aggregate_process_window_ns": process_window_ns or None,
                "trace_window_ns": trace_window_ns or None,
                "coverage_ns": coverage_ns if trace_window_ns else None,
                "coverage_fraction": coverage_fraction,
                "unattributed_ns": (
                    max(0, trace_window_ns - coverage_ns)
                    if trace_window_ns
                    else None
                ),
                "clock_anomalies": self._clock_anomalies,
            },
            "phases": phases,
            "limits_reached": sorted(self._limits_reached),
            "quality": {
                "integrity": {
                    "state": integrity,
                    "claim_level": (
                        "single_execution_attribution"
                        if integrity == "sufficient" and root_window
                        else "partial_process_attribution"
                    ),
                },
                "temporal_consistency": {
                    "state": temporal,
                    "claim_level": "single_execution_timeline"
                    if temporal == "sufficient"
                    else "no_wall_timeline_claim",
                },
                "attribution_coverage": {
                    "state": coverage_quality,
                    "claim_level": "instrumented_wall_coverage"
                    if coverage_quality == "sufficient"
                    else "partial_coverage_only",
                },
                "probe_effect": {
                    "state": "unavailable",
                    "claim_level": "no_overhead_claim",
                },
            },
        }
        self._finished = TraceParseResult(
            state=state,
            trace=trace,
            diagnostics=tuple(self._diagnostics),
        )
        return self._finished

    def _consume_bounded(self, chunk: bytes) -> None:
        offset = 0
        while offset < len(chunk):
            if self._discarding_line:
                newline = chunk.find(b"\n", offset)
                if newline < 0:
                    return
                self._discarding_line = False
                self._lines_seen += 1
                offset = newline + 1
                continue

            newline = chunk.find(b"\n", offset)
            end = len(chunk) if newline < 0 else newline
            self._buffer.extend(chunk[offset:end])
            if len(self._buffer) > self._line_limit:
                self._buffer.clear()
                self._discarding_line = newline < 0
                self._malformed("line_limit_exceeded")
                if newline >= 0:
                    self._lines_seen += 1
                    offset = newline + 1
                    continue
                return
            if newline < 0:
                return

            line = bytes(self._buffer)
            self._buffer.clear()
            self._lines_seen += 1
            self._consume_line(line)
            offset = newline + 1

    def _consume_line(self, line: bytes) -> None:
        if self._events_seen >= self._event_limit:
            self._limit("event_limit_exceeded")
            return
        if not line:
            self._malformed("empty_line")
            return
        try:
            text = line.decode("utf-8", "strict")
            event = json.loads(text)
        except (UnicodeDecodeError, ValueError, RecursionError):
            self._malformed("malformed_json_event")
            return
        if not isinstance(event, dict):
            self._malformed("event_is_not_an_object")
            return

        event_name = self._bounded_string(event.get("event"), maximum=64)
        session_id = self._bounded_string(event.get("sid"))
        thread_id = self._bounded_string(event.get("thread"))
        if event_name is None or session_id is None or thread_id is None:
            self._malformed("missing_or_oversized_event_identity")
            return
        if session_id not in self._sessions:
            if len(self._sessions) >= self._session_limit:
                self._limit("session_limit_exceeded")
                return
            self._sessions[session_id] = _Session()
        thread_key = (session_id, thread_id)
        if thread_key not in self._threads:
            if len(self._threads) >= self._thread_limit:
                self._limit("thread_limit_exceeded")
                return
            self._threads.add(thread_key)

        self._events_seen += 1
        if event_name not in _KNOWN_EVENTS:
            self._unknown_events += 1
            return
        self._recognized_events += 1
        self._event_counts[event_name] += 1

        if event_name == "version":
            version = self._bounded_string(event.get("evt"), maximum=16)
            if version is not None and version.isdigit():
                self._format_versions.add(version)
                session = self._sessions[session_id]
                if session.format_version is not None:
                    self._malformed("duplicate_version")
                session.format_version = version
            else:
                self._diagnose("event_format_version_unavailable")
        elif event_name == "start":
            self._consume_start(session_id, event)
        elif event_name == "atexit":
            self._consume_atexit(session_id, event)
        elif event_name == "region_enter":
            self._consume_region_enter(session_id, thread_id, event)
        elif event_name == "region_leave":
            self._consume_region_leave(session_id, thread_id, event)

    def _consume_start(self, session_id: str, event: dict[str, Any]) -> None:
        wall_ns = _timestamp_ns(event.get("time"))
        absolute_ns = _duration_ns(event.get("t_abs"))
        if wall_ns is None or absolute_ns is None:
            self._clock_problem("start_time_unavailable")
            return
        session = self._sessions[session_id]
        origin_ns = wall_ns - absolute_ns
        if session.origin_ns is not None:
            self._clock_problem("duplicate_start")
            return
        session.origin_ns = origin_ns

    def _consume_atexit(self, session_id: str, event: dict[str, Any]) -> None:
        wall_ns = _timestamp_ns(event.get("time"))
        absolute_ns = _duration_ns(event.get("t_abs"))
        session = self._sessions[session_id]
        if session.window_end_ns is not None:
            self._clock_problem("duplicate_atexit")
            return
        if wall_ns is None or absolute_ns is None or session.origin_ns is None:
            self._clock_problem("atexit_time_unavailable")
            return
        window_end_ns = session.origin_ns + absolute_ns
        if abs(window_end_ns - wall_ns) > CLOCK_DISAGREEMENT_NS:
            self._clock_problem("atexit_anchor_disagrees")
            return
        if window_end_ns < session.origin_ns:
            self._clock_problem("negative_trace_window")
            return
        session.window_end_ns = window_end_ns

    def _consume_region_enter(
        self, session_id: str, thread_id: str, event: dict[str, Any]
    ) -> None:
        self._region_enters += 1
        timestamp_ns = _timestamp_ns(event.get("time"))
        nesting = event.get("nesting")
        category = self._bounded_string(
            event.get("category", ""), allow_empty=True
        )
        label = self._bounded_string(event.get("label", ""), allow_empty=True)
        if (
            timestamp_ns is None
            or isinstance(nesting, bool)
            or not isinstance(nesting, int)
            or not 1 <= nesting <= self._nesting_limit
            or category is None
            or label is None
        ):
            self._unmatched_enters += 1
            self._diagnose("malformed_region_enter")
            return
        key = (session_id, thread_id)
        stack = self._stacks[key]
        if nesting != len(stack) + 1:
            self._unmatched_enters += len(stack) + 1
            stack.clear()
            self._diagnose("region_nesting_disagrees")
            if nesting != 1:
                return
        stack.append(
            _RegionFrame(
                start_ns=timestamp_ns,
                nesting=nesting,
                category=category,
                label=label,
                phase_id=_PHASE_IDS.get((category, label), "other"),
            )
        )

    def _consume_region_leave(
        self, session_id: str, thread_id: str, event: dict[str, Any]
    ) -> None:
        self._region_leaves += 1
        timestamp_ns = _timestamp_ns(event.get("time"))
        nesting = event.get("nesting")
        category = self._bounded_string(
            event.get("category", ""), allow_empty=True
        )
        label = self._bounded_string(event.get("label", ""), allow_empty=True)
        key = (session_id, thread_id)
        stack = self._stacks[key]
        if (
            timestamp_ns is None
            or isinstance(nesting, bool)
            or not isinstance(nesting, int)
            or category is None
            or label is None
            or not stack
        ):
            self._unmatched_leaves += 1
            self._diagnose("unmatched_region_leave")
            return
        frame = stack[-1]
        if (
            nesting != frame.nesting
            or (category and frame.category and category != frame.category)
            or (label and frame.label and label != frame.label)
        ):
            self._unmatched_leaves += 1
            self._unmatched_enters += len(stack)
            stack.clear()
            self._diagnose("region_leave_disagrees")
            return
        stack.pop()
        if timestamp_ns < frame.start_ns:
            self._clock_problem("region_clock_regression")
            return

        interval = (frame.start_ns, timestamp_ns)
        inclusive_ns = _duration_ns(event.get("t_rel"))
        if inclusive_ns is None:
            self._clock_problem("region_duration_unavailable")
            return
        if (
            abs(inclusive_ns - (timestamp_ns - frame.start_ns))
            > CLOCK_DISAGREEMENT_NS
        ):
            self._clock_problem("region_duration_disagrees")
        for child_start_ns, child_end_ns in frame.direct_child_intervals:
            if child_start_ns < frame.start_ns or child_end_ns > timestamp_ns:
                self._clock_problem("child_region_outside_parent")
        child_ns = frame.direct_child_elapsed_ns
        if child_ns > inclusive_ns + CLOCK_DISAGREEMENT_NS:
            self._clock_problem("child_duration_exceeds_parent")
        aggregate = self._phases[frame.phase_id]
        aggregate.count += 1
        aggregate.inclusive_ns += inclusive_ns
        aggregate.exclusive_ns += max(0, inclusive_ns - child_ns)
        aggregate.intervals_by_session[session_id].append(interval)
        self._all_intervals[session_id].append(interval)
        self._matched_regions += 1
        if stack:
            stack[-1].direct_child_intervals.append(interval)
            stack[-1].direct_child_elapsed_ns += inclusive_ns

    def _bounded_string(
        self,
        value: Any,
        *,
        maximum: Optional[int] = None,
        allow_empty: bool = False,
    ) -> Optional[str]:
        maximum = self._string_limit if maximum is None else maximum
        if not isinstance(value, str) or len(value) > maximum:
            return None
        if not value and not allow_empty:
            return None
        return value

    def _limit(self, reason: str) -> None:
        self._limits_reached.add(reason)
        self._diagnose(reason)

    def _malformed(self, reason: str) -> None:
        self._malformed_events += 1
        self._diagnose(reason)

    def _clock_problem(self, reason: str) -> None:
        self._clock_anomalies += 1
        self._diagnose(reason)

    def _diagnose(self, reason: str) -> None:
        if reason in self._diagnostics:
            return
        if len(self._diagnostics) < self._diagnostic_limit:
            self._diagnostics.append(reason)
        else:
            self._limits_reached.add("diagnostic_limit_exceeded")


def parse_trace2_input(
    path: str,
    *,
    stdin: Optional[BinaryIO] = None,
    byte_limit: int = DEFAULT_TRACE_BYTE_LIMIT,
) -> TraceParseResult:
    """Parse a regular file or `-` stdin without following symlinks."""
    parser = Trace2EventParser(byte_limit=byte_limit)
    if path == "-":
        if stdin is None:
            raise ValueError("stdin is required for trace input '-'")
        return _consume_stream(parser, stdin)

    descriptor = None
    try:
        initial = os.lstat(path)
        if not stat.S_ISREG(initial.st_mode):
            return _failed_input("input_not_regular")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            return _failed_input("input_not_regular")
        if _file_identity(initial) != _file_identity(opened):
            return _failed_input("input_changed_before_open")
        with os.fdopen(descriptor, "rb", closefd=True) as trace_file:
            descriptor = None
            result = _consume_stream(parser, trace_file)
        try:
            final = os.lstat(path)
        except OSError:
            return _with_input_limitation(result, "input_changed_during_read")
        if _file_identity(initial) != _file_identity(final):
            return _with_input_limitation(result, "input_changed_during_read")
        return result
    except FileNotFoundError:
        return _failed_input("input_not_found")
    except OSError:
        return _failed_input("input_open_failed")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _consume_stream(
    parser: Trace2EventParser, stream: BinaryIO
) -> TraceParseResult:
    while True:
        try:
            chunk = stream.read(TRACE_READ_CHUNK_BYTES)
        except (OSError, ValueError):
            parser.mark_transport_limit("input_read_failed")
            break
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            parser.mark_transport_limit("input_is_not_binary")
            break
        parser.consume(chunk)
        if parser._bytes_seen > parser._byte_limit:  # pylint: disable=protected-access
            break
    return parser.finish()


def _failed_input(reason: str) -> TraceParseResult:
    return TraceParseResult(
        state="failed",
        trace={
            "format": "trace2_event",
            "events": {
                "total": 0,
                "recognized": 0,
                "unknown": 0,
                "malformed": 0,
                "by_type": {},
            },
            "phases": [],
            "limits_reached": [],
            "quality": {
                "integrity": {
                    "state": "invalid",
                    "claim_level": "no_attribution",
                }
            },
        },
        diagnostics=(reason,),
    )


def _with_input_limitation(
    result: TraceParseResult, reason: str
) -> TraceParseResult:
    diagnostics = list(result.diagnostics)
    if reason not in diagnostics:
        diagnostics.append(reason)
    state = "failed" if result.state == "failed" else "partial"
    trace = copy.deepcopy(result.trace)
    limits = set(trace.get("limits_reached", ()))
    limits.add(reason)
    trace["limits_reached"] = sorted(limits)
    trace["quality"]["integrity"] = {
        "state": "invalid" if state == "failed" else "limited",
        "claim_level": "no_attribution"
        if state == "failed"
        else "partial_process_attribution",
    }
    for dimension in ("temporal_consistency", "attribution_coverage"):
        if dimension in trace["quality"]:
            trace["quality"][dimension] = {
                "state": "limited",
                "claim_level": "partial_input_only",
            }
    return TraceParseResult(state, trace, tuple(diagnostics))


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _duration_ns(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 0 <= value <= MAX_TRACE_DURATION_SECONDS:
        return None
    numeric = float(value)
    if (
        not math.isfinite(numeric)
        or not 0 <= numeric <= MAX_TRACE_DURATION_SECONDS
    ):
        return None
    return int(numeric * 1_000_000_000)


def _timestamp_ns(value: Any) -> Optional[int]:
    if not isinstance(value, str) or not 20 <= len(value) <= 40:
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return None
        utc = parsed.astimezone(datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    delta = utc - epoch
    return (
        delta.days * 86_400 * 1_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )


def _interval_union_length(intervals: list[tuple[int, int]]) -> int:
    if not intervals:
        return 0
    total = 0
    current_start, current_end = sorted(intervals)[0]
    for start, end in sorted(intervals)[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            total += max(0, current_end - current_start)
            current_start, current_end = start, end
    return total + max(0, current_end - current_start)
