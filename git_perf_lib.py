#!/usr/bin/env python3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Inspect Git setup, time status, and summarize Trace2 events.

Reports omit raw paths and child-process output. The tool gathers evidence but
does not write Git configuration or choose a setting for the user.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import io
import json
import logging
import os
import re
import select
import shutil
import signal
import stat
import statistics
import subprocess
import sys
import textwrap
import threading
import time

from typing import Any, Callable, Mapping, Optional, Sequence

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on Windows.
    fcntl = None

import gclient_paths
import gclient_utils
import git_perf_trace

SCHEMA_VERSION = 1
SCHEMA_STABILITY = "experimental"
STRICT_EFFECT_PROFILE = "strict_observation"
NORMAL_GIT_EFFECT_PROFILE = "normal_configured_git"

DEFAULT_PROCESS_TIMEOUT_SECONDS = 5.0
DEFAULT_OUTPUT_LIMIT_BYTES = 256 * 1024
PROCESS_CLEANUP_GRACE_SECONDS = 1.0

STATUS_WORKLOAD_ID = "status.configured_worktree"
STATUS_WORKLOAD_VERSION = 1
STATUS_WORKLOAD_ARGS = (
    "status",
    "--porcelain=v2",
    "-z",
    "--branch",
)

DEFAULT_BENCHMARK_SAMPLES = 7
DEFAULT_BENCHMARK_WARMUP = 1
DEFAULT_BENCHMARK_DEADLINE_SECONDS = 60
DEFAULT_BENCHMARK_RUN_TIMEOUT_SECONDS = 30.0
DEFAULT_BENCHMARK_POSTFLIGHT_RESERVE_SECONDS = 5.0
DEFAULT_TRACE_DEADLINE_SECONDS = 30
DEFAULT_TRACE_HANDSHAKE_TIMEOUT_SECONDS = 5.0
MIN_BENCHMARK_SAMPLES = 1
MAX_BENCHMARK_SAMPLES = 100
MIN_BENCHMARK_WARMUP = 0
MAX_BENCHMARK_WARMUP = 20
MIN_BENCHMARK_DEADLINE_SECONDS = 1
MAX_BENCHMARK_DEADLINE_SECONDS = 600

STATUS_PARSE_BYTE_LIMIT = 64 * 1024 * 1024
STATUS_PARSE_RECORD_LIMIT = 1024 * 1024

# Comparing this allowlist before and after a benchmark detects changes that
# could invalidate its timings. Raw values are discarded after the comparison.
_STATUS_CONFIG_PATTERN = (
    r"^(feature\.manyFiles|core\.(fsmonitor|fsmonitorHookVersion|"
    r"untrackedCache|preloadIndex|fscache)|index\.(threads|skipHash)|"
    r"status\.(showUntrackedFiles|aheadBehind|renames|renameLimit|"
    r"relativePaths)|diff\.(renames|renameLimit|ignoreSubmodules)|"
    r"submodule\.(recurse|.*\.ignore))$"
)

# Git config can supply trace targets even when the environment does not. Set
# every target to Git's disabled spelling to prevent an inspection from writing
# an ambient trace file.
_TRACE_TARGET_VARIABLES = (
    "GIT_TRACE",
    "GIT_TRACE_FSMONITOR",
    "GIT_TRACE_PACK_ACCESS",
    "GIT_TRACE_PACKET",
    "GIT_TRACE_PERFORMANCE",
    "GIT_TRACE_SETUP",
    "GIT_TRACE_SHALLOW",
    "GIT_TRACE_CURL",
    "GIT_TRACE2",
    "GIT_TRACE2_EVENT",
    "GIT_TRACE2_PERF",
)

_PATH_BEARING_GIT_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_GLOBAL",
)

_KNOWN_CONFIG_SCOPES = {
    b"system",
    b"global",
    b"local",
    b"worktree",
    b"command",
}

_TRACE2_FD_LOCK = threading.Lock()
_LAUNCHER_ENV = "DEPOT_TOOLS_GIT_PERF_LAUNCHER"
_PYTHON_RUNNER_ENV = "DEPOT_TOOLS_GIT_PERF_PYTHON_RUNNER"


def _parse_git_version(text: str) -> tuple[int, ...]:
    """Parse numeric components without importing Git-discovery machinery."""
    match = re.match(r"git version ([0-9]+(?:\.[0-9]+)+)", text)
    return tuple(int(part) for part in match[1].split(".")) if match else ()


@dataclasses.dataclass(frozen=True)
class Fact:
    """One report observation with path-bearing data removed."""

    id: str
    domain: str
    state: str
    value: Any
    provenance: str
    applicability: str = "applicable"
    limitations: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class Finding:
    """An interpretation derived from specific report facts."""

    id: str
    kind: str
    domain: str
    priority: int
    summary: str
    evidence_fact_ids: tuple[str, ...] = ()
    applicability: str = "applicable"
    blockers: tuple[str, ...] = ()
    action: Optional[dict[str, Any]] = None
    source_ids: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class Diagnostic:
    """A reportable problem without raw child-process data."""

    id: str
    severity: str
    summary: str


@dataclasses.dataclass
class Report:
    """Data shared by the human and JSON renderers."""

    status: str
    tool: dict[str, Any]
    subject: dict[str, Any]
    facts: list[Fact] = dataclasses.field(default_factory=list)
    findings: list[Finding] = dataclasses.field(default_factory=list)
    measurements: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    diagnostics: list[Diagnostic] = dataclasses.field(default_factory=list)
    sources: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    schema_stability: str = SCHEMA_STABILITY

    def canonicalize(self) -> None:
        """Put every stable-ID collection into deterministic order."""
        self.facts.sort(key=lambda fact: fact.id)
        self.findings.sort(key=lambda finding: (finding.priority, finding.id))
        self.diagnostics.sort(key=lambda diagnostic: diagnostic.id)
        self.sources.sort(key=lambda source: source["id"])

    def to_dict(self) -> dict[str, Any]:
        facts = sorted(self.facts, key=lambda fact: fact.id)
        findings = sorted(
            self.findings, key=lambda finding: (finding.priority, finding.id)
        )
        diagnostics = sorted(
            self.diagnostics, key=lambda diagnostic: diagnostic.id
        )
        sources = sorted(self.sources, key=lambda source: source["id"])
        return {
            "schema_version": self.schema_version,
            "schema_stability": self.schema_stability,
            "status": self.status,
            "tool": self.tool,
            "subject": self.subject,
            "facts": [dataclasses.asdict(fact) for fact in facts],
            "findings": [dataclasses.asdict(finding) for finding in findings],
            "measurements": self.measurements,
            "diagnostics": [
                dataclasses.asdict(diagnostic) for diagnostic in diagnostics
            ],
            "sources": sources,
        }


@dataclasses.dataclass(frozen=True)
class GitResult:
    """One Git process result without raw stderr or its command path."""

    outcome: str
    returncode: Optional[int]
    stdout: bytes
    stdout_truncated: bool
    stderr_state: str
    elapsed_ns: int
    cleanup_uncertain: bool = False
    cleanup_elapsed_ns: int = 0
    stdout_consumer_failed: bool = False
    stdout_read_failed: bool = False
    trace_consumer_failed: bool = False
    trace_read_failed: bool = False

    @property
    def ok(self) -> bool:
        return self.outcome == "success"


def launcher_provenance(
    environ: Optional[Mapping[str, str]] = None,
) -> str:
    """Classify the wrapper/runner/module chain without retaining paths."""
    environ = os.environ if environ is None else environ
    launcher = environ.get(_LAUNCHER_ENV)
    python_runner = environ.get(_PYTHON_RUNNER_ENV)
    if not launcher or not python_runner:
        return "unknown"
    module_directory = os.path.dirname(os.path.realpath(__file__))
    expected_launcher = os.path.join(module_directory, "git-perf")
    expected_runner = os.path.join(module_directory, "python_runner.sh")
    identities = (
        _regular_file_identity(launcher),
        _regular_file_identity(python_runner),
        _regular_file_identity(expected_launcher),
        _regular_file_identity(expected_runner),
    )
    if any(identity is None for identity in identities):
        return "unknown"
    return (
        "coherent"
        if identities[0] == identities[2] and identities[1] == identities[3]
        else "path_mixed"
    )


@dataclasses.dataclass(frozen=True)
class StatusCounts:
    """Path-free counts from one complete porcelain-v2 status stream."""

    ordinary: int
    renamed_or_copied: int
    unmerged: int
    untracked: int
    ignored: int
    staged: int
    unstaged: int
    submodule_changed: int
    branch_headers: int = 0
    ahead: Optional[int] = None
    behind: Optional[int] = None


@dataclasses.dataclass(frozen=True)
class StatusParseResult:
    """Counts and parser state for one status stream."""

    state: str
    counts: Optional[StatusCounts]
    bytes_seen: int
    records_seen: int
    reason: Optional[str] = None
    status_fingerprint: Optional[bytes] = None


class PorcelainV2StatusParser:
    """Incrementally count `git status --porcelain=v2 -z` byte records."""

    def __init__(
        self,
        *,
        byte_limit: int,
        record_limit: int,
        stability_key: Optional[bytes] = None,
    ):
        if byte_limit <= 0 or record_limit <= 0:
            raise ValueError("Status parser limits must be positive")
        if stability_key is not None and (
            not isinstance(stability_key, bytes)
            or not 1 <= len(stability_key) <= 64
        ):
            raise ValueError(
                "Status stability key must contain from 1 to 64 bytes"
            )
        self._byte_limit = byte_limit
        self._record_limit = record_limit
        self._buffer = bytearray()
        self._bytes_seen = 0
        self._records_seen = 0
        self._reason: Optional[str] = None
        self._result: Optional[StatusParseResult] = None
        self._pending_rename: Optional[tuple[bytes, bool]] = None

        self._ordinary = 0
        self._renamed_or_copied = 0
        self._unmerged = 0
        self._untracked = 0
        self._ignored = 0
        self._staged = 0
        self._unstaged = 0
        self._submodule_changed = 0
        self._branch_headers = 0
        self._ahead: Optional[int] = None
        self._behind: Optional[int] = None
        self._seen_branch_ab = False
        self._status_digest = hashlib.blake2b(
            key=os.urandom(32) if stability_key is None else stability_key,
            digest_size=16,
        )

    def consume(self, chunk: bytes) -> None:
        """Consume one raw stdout chunk without retaining complete paths."""
        if self._result is not None:
            raise RuntimeError("Status parser is already finished")
        if not isinstance(chunk, bytes):
            raise TypeError("Status parser chunks must be bytes")

        self._bytes_seen += len(chunk)
        if self._reason is not None or not chunk:
            return
        if self._bytes_seen > self._byte_limit:
            self._invalidate("parse_byte_limit_exceeded")
            return

        offset = 0
        while offset < len(chunk):
            terminator = chunk.find(b"\0", offset)
            end = len(chunk) if terminator < 0 else terminator
            segment_length = end - offset
            if len(self._buffer) + segment_length > self._record_limit:
                self._invalidate("record_byte_limit_exceeded")
                return
            self._buffer.extend(chunk[offset:end])
            if terminator < 0:
                return

            record = bytes(self._buffer)
            self._buffer.clear()
            self._consume_record(record)
            if self._reason is not None:
                return
            offset = terminator + 1

    def finish(self) -> StatusParseResult:
        """Finish the stream and return one immutable path-free result."""
        if self._result is not None:
            return self._result
        if self._reason is None and (self._buffer or self._pending_rename):
            self._invalidate("truncated_record")

        counts = None
        state = "unavailable"
        if self._reason is None:
            state = "complete"
            counts = StatusCounts(
                ordinary=self._ordinary,
                renamed_or_copied=self._renamed_or_copied,
                unmerged=self._unmerged,
                untracked=self._untracked,
                ignored=self._ignored,
                staged=self._staged,
                unstaged=self._unstaged,
                submodule_changed=self._submodule_changed,
                branch_headers=self._branch_headers,
                ahead=self._ahead,
                behind=self._behind,
            )
        self._result = StatusParseResult(
            state=state,
            counts=counts,
            bytes_seen=self._bytes_seen,
            records_seen=self._records_seen,
            reason=self._reason,
            status_fingerprint=(
                self._status_digest.digest() if self._reason is None else None
            ),
        )
        return self._result

    def _consume_record(self, record: bytes) -> None:
        if self._pending_rename is not None:
            if not record:
                self._invalidate("malformed_record")
                return
            xy, submodule_changed = self._pending_rename
            self._pending_rename = None
            self._fingerprint_record(record)
            self._record_tracked("rename", xy, submodule_changed)
            return

        if not record:
            self._invalidate("malformed_record")
            return

        kind = record[:1]
        if kind == b"#":
            self._consume_header(record)
        elif kind == b"1":
            parsed = self._parse_ordinary(record)
            if parsed is not None:
                self._fingerprint_record(record)
                self._record_tracked("ordinary", *parsed)
        elif kind == b"2":
            parsed = self._parse_rename(record)
            if parsed is not None:
                self._fingerprint_record(record)
                self._pending_rename = parsed
        elif kind == b"u":
            parsed = self._parse_unmerged(record)
            if parsed is not None:
                self._fingerprint_record(record)
                _, submodule_changed = parsed
                self._records_seen += 1
                self._unmerged += 1
                self._submodule_changed += int(submodule_changed)
        elif kind == b"?" and record.startswith(b"? ") and record[2:]:
            self._fingerprint_record(record)
            self._records_seen += 1
            self._untracked += 1
        elif kind == b"!" and record.startswith(b"! ") and record[2:]:
            self._fingerprint_record(record)
            self._records_seen += 1
            self._ignored += 1
        else:
            self._invalidate("unknown_or_malformed_record")

    def _consume_header(self, record: bytes) -> None:
        """Reduce one branch header without retaining its sensitive values."""
        if not record.startswith(b"# "):
            self._invalidate("malformed_record")
            return
        key, separator, value = record[2:].partition(b" ")
        if (
            not separator
            or not key
            or len(key) > 64
            or not value
            or any(
                character
                not in b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_"
                for character in key
            )
        ):
            self._invalidate("malformed_record")
            return

        if key == b"branch.oid" and not (
            value == b"(initial)" or self._valid_object_names((value,))
        ):
            self._invalidate("malformed_record")
            return
        if key == b"branch.ab":
            if self._seen_branch_ab:
                self._invalidate("malformed_record")
                return
            fields = value.split(b" ")
            if (
                len(fields) != 2
                or not self._bounded_header_count(fields[0], b"+")
                or not self._bounded_header_count(fields[1], b"-")
            ):
                self._invalidate("malformed_record")
                return
            self._seen_branch_ab = True
            self._ahead = int(fields[0][1:])
            self._behind = int(fields[1][1:])

        self._fingerprint_record(record)
        self._branch_headers += 1
        self._records_seen += 1

    def _fingerprint_record(self, record: bytes) -> None:
        """Add one record to an invocation-local, non-exported drift token."""
        self._status_digest.update(len(record).to_bytes(8, "big"))
        self._status_digest.update(record)

    @staticmethod
    def _bounded_header_count(value: bytes, prefix: bytes) -> bool:
        digits = value[1:]
        return (
            value.startswith(prefix)
            and 1 <= len(digits) <= 20
            and digits.isdigit()
            and int(digits) <= (1 << 63) - 1
        )

    def _parse_ordinary(self, record: bytes) -> Optional[tuple[bytes, bool]]:
        fields = record.split(b" ", 8)
        if (
            len(fields) != 9
            or fields[0] != b"1"
            or not fields[8]
            or not self._valid_xy(fields[1], b".MTAD")
            or fields[1] == b".."
            or not self._valid_modes(fields[3:6])
            or not self._valid_object_names(fields[6:8])
        ):
            self._invalidate("malformed_record")
            return None
        submodule_changed = self._submodule_state(fields[2])
        if submodule_changed is None:
            self._invalidate("malformed_record")
            return None
        return fields[1], submodule_changed

    def _parse_rename(self, record: bytes) -> Optional[tuple[bytes, bool]]:
        fields = record.split(b" ", 9)
        if (
            len(fields) != 10
            or fields[0] != b"2"
            or not fields[9]
            or not self._valid_xy(fields[1], b".MTADRC")
            or not any(status in fields[1] for status in b"RC")
            or not self._valid_modes(fields[3:6])
            or not self._valid_object_names(fields[6:8])
            or not self._valid_score(fields[8])
        ):
            self._invalidate("malformed_record")
            return None
        submodule_changed = self._submodule_state(fields[2])
        if submodule_changed is None:
            self._invalidate("malformed_record")
            return None
        return fields[1], submodule_changed

    def _parse_unmerged(self, record: bytes) -> Optional[tuple[bytes, bool]]:
        fields = record.split(b" ", 10)
        if (
            len(fields) != 11
            or fields[0] != b"u"
            or not fields[10]
            or fields[1]
            not in (b"DD", b"AU", b"UD", b"UA", b"DU", b"AA", b"UU")
            or not self._valid_modes(fields[3:7])
            or not self._valid_object_names(fields[7:10])
        ):
            self._invalidate("malformed_record")
            return None
        submodule_changed = self._submodule_state(fields[2])
        if submodule_changed is None:
            self._invalidate("malformed_record")
            return None
        return fields[1], submodule_changed

    def _record_tracked(
        self, kind: str, xy: bytes, submodule_changed: bool
    ) -> None:
        self._records_seen += 1
        if kind == "ordinary":
            self._ordinary += 1
        else:
            self._renamed_or_copied += 1
        self._staged += int(xy[:1] != b".")
        self._unstaged += int(xy[1:] != b".")
        self._submodule_changed += int(submodule_changed)

    def _invalidate(self, reason: str) -> None:
        if self._reason is None:
            self._reason = reason
        self._buffer.clear()
        self._pending_rename = None

    @staticmethod
    def _valid_xy(xy: bytes, allowed: bytes) -> bool:
        return len(xy) == 2 and all(character in allowed for character in xy)

    @staticmethod
    def _submodule_state(value: bytes) -> Optional[bool]:
        if value == b"N...":
            return False
        if (
            len(value) == 4
            and value[:1] == b"S"
            and value[1] in b".C"
            and value[2] in b".M"
            and value[3] in b".U"
        ):
            return value != b"S..."
        return None

    @staticmethod
    def _valid_modes(values: Sequence[bytes]) -> bool:
        return all(
            len(value) == 6
            and all(character in b"01234567" for character in value)
            for value in values
        )

    @staticmethod
    def _valid_object_names(values: Sequence[bytes]) -> bool:
        hexadecimal = b"0123456789abcdefABCDEF"
        return all(
            4 <= len(value) <= 128
            and all(character in hexadecimal for character in value)
            for value in values
        )

    @staticmethod
    def _valid_score(value: bytes) -> bool:
        return (
            2 <= len(value) <= 4
            and value[:1] in (b"R", b"C")
            and value[1:].isdigit()
            and int(value[1:]) <= 100
        )


class _BoundedPipeReader(threading.Thread):
    """Drain a pipe completely while retaining at most `limit` bytes."""

    def __init__(
        self,
        stream,
        limit: int,
        *,
        chunk_consumer: Optional[Callable[[bytes], None]] = None,
    ):
        super().__init__(daemon=True)
        self._stream = stream
        self._limit = max(0, limit)
        self._chunk_consumer = chunk_consumer
        self._buffer = bytearray()
        self.truncated = False
        self.failed = False
        self.consumer_failed = False

    def run(self) -> None:
        try:
            while True:
                chunk = self._stream.read(64 * 1024)
                if not chunk:
                    break
                if (
                    self._chunk_consumer is not None
                    and not self.consumer_failed
                ):
                    try:
                        self._chunk_consumer(chunk)
                    except Exception:  # pylint: disable=broad-except
                        # Git can block if its output pipe fills. After a parser
                        # failure, keep draining but discard the remaining data.
                        self.consumer_failed = True
                remaining = self._limit - len(self._buffer)
                if remaining > 0:
                    self._buffer.extend(chunk[:remaining])
                if len(chunk) > max(remaining, 0):
                    self.truncated = True
        except (OSError, ValueError):
            self.failed = True

    @property
    def data(self) -> bytes:
        return bytes(self._buffer)


class _BoundedFDReader(threading.Thread):
    """Drain a raw descriptor until EOF or an external stop request."""

    _POLL_SECONDS = 0.05

    def __init__(
        self,
        descriptor: int,
        *,
        chunk_consumer: Optional[Callable[[bytes], None]] = None,
    ):
        super().__init__(daemon=True)
        self._descriptor = descriptor
        self._chunk_consumer = chunk_consumer
        self._stop_requested = threading.Event()
        self.failed = False
        self.consumer_failed = False

    def stop(self) -> None:
        self._stop_requested.set()

    def run(self) -> None:
        try:
            while not self._stop_requested.is_set():
                readable, _writable, _exceptional = select.select(
                    [self._descriptor], [], [], self._POLL_SECONDS
                )
                if not readable:
                    continue
                chunk = os.read(self._descriptor, 64 * 1024)
                if not chunk:
                    break
                if (
                    self._chunk_consumer is not None
                    and not self.consumer_failed
                ):
                    try:
                        self._chunk_consumer(chunk)
                    except Exception:  # pylint: disable=broad-except
                        self.consumer_failed = True
        except (OSError, ValueError):
            if not self._stop_requested.is_set():
                self.failed = True


@dataclasses.dataclass
class _Trace2Pipe:
    """One inherited Trace2 EVENT pipe using Git's supported FD range."""

    read_fd: int
    write_fd: int
    target_fd: int

    def close_writer(self) -> None:
        descriptors = {self.write_fd, self.target_fd}
        for descriptor in descriptors:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        self.write_fd = -1
        self.target_fd = -1

    def close(self) -> None:
        descriptors = {self.read_fd, self.write_fd, self.target_fd}
        for descriptor in descriptors:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        self.read_fd = -1
        self.write_fd = -1
        self.target_fd = -1


def _open_trace2_pipe() -> Optional[_Trace2Pipe]:
    """Create a collision-free inherited EVENT target in descriptor 3-9."""
    if os.name != "posix" or fcntl is None:
        return None
    try:
        read_fd, write_fd = os.pipe()
        # Git accepts trace targets 2 through 9. Keep the collector's read end
        # above that range so concurrent traces do not consume those targets.
        if 3 <= read_fd <= 9:
            moved_read_fd = fcntl.fcntl(read_fd, fcntl.F_DUPFD_CLOEXEC, 10)
            os.close(read_fd)
            read_fd = moved_read_fd
        os.set_inheritable(read_fd, False)
        target_fd = None
        for candidate in range(3, 10):
            if candidate == write_fd:
                target_fd = candidate
                break
            try:
                os.fstat(candidate)
            except OSError:
                target_fd = candidate
                break
        if target_fd is None:
            os.close(read_fd)
            os.close(write_fd)
            return None
        if target_fd != write_fd:
            os.dup2(write_fd, target_fd, inheritable=True)
            os.close(write_fd)
            write_fd = -1
        else:
            os.set_inheritable(target_fd, True)
        return _Trace2Pipe(read_fd, write_fd, target_fd)
    except OSError:
        for descriptor in (
            locals().get("read_fd", -1),
            locals().get("write_fd", -1),
            locals().get("target_fd", -1),
        ):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        return None


class GitRunner:
    """Run every child Git command directly, without a shell."""

    def __init__(
        self,
        executable: str,
        *,
        environ: Optional[Mapping[str, str]] = None,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ):
        if not os.path.isabs(executable):
            raise ValueError("GitRunner requires an absolute executable")
        self.executable = executable
        self._environ = dict(os.environ if environ is None else environ)
        self._clock_ns = clock_ns

    @classmethod
    def resolve(
        cls,
        executable: str = "git",
        *,
        environ: Optional[Mapping[str, str]] = None,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> Optional["GitRunner"]:
        source_environment = os.environ if environ is None else environ
        resolved = shutil.which(
            executable, path=source_environment.get("PATH", "")
        )
        if resolved is None:
            return None
        return cls(
            os.path.abspath(resolved),
            environ=source_environment,
            clock_ns=clock_ns,
        )

    def environment_for(self, effect_profile: str) -> dict[str, str]:
        """Build the environment for inspection or measurement."""
        if effect_profile not in (
            STRICT_EFFECT_PROFILE,
            NORMAL_GIT_EFFECT_PROFILE,
        ):
            raise ValueError("Unsupported Git effect profile")

        environment = self._environ.copy()
        environment.update(
            {
                "GCM_INTERACTIVE": "never",
                "GIT_ASKPASS": "",
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_PAGER": "cat",
                "GIT_TERMINAL_PROMPT": "0",
                "LC_ALL": "C",
                "PAGER": "cat",
            }
        )
        for variable in _TRACE_TARGET_VARIABLES:
            environment[variable] = "0"
        environment["GIT_TRACE2_CONFIG_PARAMS"] = ""
        environment["GIT_TRACE2_ENV_VARS"] = ""
        environment["GIT_TRACE_CURL_NO_DATA"] = "1"
        environment["GIT_TRACE_REDACT"] = "1"
        environment.pop("GIT_TRACE2_PARENT_NAME", None)
        environment.pop("GIT_TRACE2_PARENT_SID", None)
        environment.pop(_LAUNCHER_ENV, None)
        environment.pop(_PYTHON_RUNNER_ENV, None)
        if effect_profile == STRICT_EFFECT_PROFILE:
            environment["GIT_OPTIONAL_LOCKS"] = "0"
        return environment

    def strict_environment(self) -> dict[str, str]:
        """Build the environment used for metadata inspection."""
        return self.environment_for(STRICT_EFFECT_PROFILE)

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Optional[str],
        effect_profile: str = STRICT_EFFECT_PROFILE,
        timeout_seconds: float = DEFAULT_PROCESS_TIMEOUT_SECONDS,
        output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES,
        stdout_consumer: Optional[Callable[[bytes], None]] = None,
        trace2_event_consumer: Optional[Callable[[bytes], None]] = None,
    ) -> GitResult:
        """Run Git with time and output limits.

        The result omits raw arguments, cwd, stderr, and executable paths. A
        stdout consumer receives raw chunks on the reader thread and must
        remove sensitive data before retaining it.
        """
        if not args or any(
            not isinstance(arg, str) or "\0" in arg for arg in args
        ):
            raise ValueError("Git arguments must be non-empty NUL-free strings")
        if timeout_seconds <= 0:
            raise ValueError("Git timeout must be positive")
        if stdout_consumer is not None and not callable(stdout_consumer):
            raise ValueError("Git stdout consumer must be callable")
        if trace2_event_consumer is not None and not callable(
            trace2_event_consumer
        ):
            raise ValueError("Git Trace2 consumer must be callable")

        command = [self.executable, "--no-pager", *args]
        environment = self.environment_for(effect_profile)
        trace_pipe = None
        trace_lock = (
            _TRACE2_FD_LOCK
            if trace2_event_consumer is not None
            else contextlib.nullcontext()
        )
        with trace_lock:
            if trace2_event_consumer is not None:
                trace_pipe = _open_trace2_pipe()
                if trace_pipe is None:
                    return GitResult(
                        outcome="trace_transport_unavailable",
                        returncode=None,
                        stdout=b"",
                        stdout_truncated=False,
                        stderr_state="unavailable",
                        elapsed_ns=0,
                    )
                environment.update(
                    {
                        "GIT_TRACE2_EVENT": str(trace_pipe.target_fd),
                        "GIT_TRACE2_EVENT_BRIEF": "0",
                        "GIT_TRACE2_EVENT_NESTING": "20",
                    }
                )

            popen_kwargs: dict[str, Any] = {}
            if os.name == "posix":
                popen_kwargs["start_new_session"] = True
                if trace_pipe is not None:
                    popen_kwargs["pass_fds"] = (trace_pipe.target_fd,)
            elif sys.platform == "win32":
                popen_kwargs["creationflags"] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                )

            start_ns = self._clock_ns()
            try:
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                    bufsize=0,
                    close_fds=True,
                    **popen_kwargs,
                )
            except OSError:
                if trace_pipe is not None:
                    trace_pipe.close()
                return GitResult(
                    outcome="start_failed",
                    returncode=None,
                    stdout=b"",
                    stdout_truncated=False,
                    stderr_state="unavailable",
                    elapsed_ns=max(0, self._clock_ns() - start_ns),
                )
            except BaseException:
                if trace_pipe is not None:
                    trace_pipe.close()
                raise
            finally:
                if trace_pipe is not None:
                    trace_pipe.close_writer()

        trace_descriptor = None
        trace_reader = None
        if trace_pipe is not None:
            trace_descriptor = trace_pipe.read_fd
            trace_pipe.read_fd = -1
            trace_reader = _BoundedFDReader(
                trace_descriptor,
                chunk_consumer=trace2_event_consumer,
            )

        stdout_reader = _BoundedPipeReader(
            process.stdout,
            output_limit_bytes,
            chunk_consumer=stdout_consumer,
        )
        stderr_reader = _BoundedPipeReader(process.stderr, output_limit_bytes)
        stdout_reader.start()
        stderr_reader.start()
        if trace_reader is not None:
            trace_reader.start()

        outcome = "nonzero_exit"
        cleanup_uncertain = False
        measurement_end_ns: Optional[int] = None
        cleanup_start_ns: Optional[int] = None
        try:
            process.wait(timeout=timeout_seconds)
            outcome = "success" if process.returncode == 0 else "nonzero_exit"
        except subprocess.TimeoutExpired:
            outcome = "timeout"
            measurement_end_ns = self._clock_ns()
            cleanup_start_ns = measurement_end_ns
            cleanup_uncertain = self._terminate_process(process)
        except BaseException:
            self._terminate_process(process)
            raise
        finally:
            cleanup_uncertain = (
                self._finish_reader(stdout_reader, process.stdout)
                or self._finish_reader(stderr_reader, process.stderr)
                or cleanup_uncertain
            )
            if trace_reader is not None:
                cleanup_uncertain = (
                    self._finish_trace_reader(trace_reader, trace_descriptor)
                    or cleanup_uncertain
                )

        final_ns = self._clock_ns()
        if measurement_end_ns is None:
            measurement_end_ns = final_ns
        cleanup_elapsed_ns = (
            max(0, final_ns - cleanup_start_ns)
            if cleanup_start_ns is not None
            else 0
        )

        if stderr_reader.failed:
            stderr_state = "read_failed"
        elif stderr_reader.truncated:
            stderr_state = "truncated"
        elif stderr_reader.data:
            stderr_state = "present"
        else:
            stderr_state = "empty"

        return GitResult(
            outcome=outcome,
            returncode=process.returncode,
            stdout=stdout_reader.data,
            stdout_truncated=stdout_reader.truncated or stdout_reader.failed,
            stderr_state=stderr_state,
            elapsed_ns=max(0, measurement_end_ns - start_ns),
            cleanup_uncertain=cleanup_uncertain,
            cleanup_elapsed_ns=cleanup_elapsed_ns,
            stdout_consumer_failed=stdout_reader.consumer_failed,
            stdout_read_failed=stdout_reader.failed,
            trace_consumer_failed=(
                trace_reader.consumer_failed
                if trace_reader is not None
                else False
            ),
            trace_read_failed=(
                trace_reader.failed if trace_reader is not None else False
            ),
        )

    @staticmethod
    def _finish_reader(reader: _BoundedPipeReader, stream) -> bool:
        reader.join(PROCESS_CLEANUP_GRACE_SECONDS)
        if not reader.is_alive():
            stream.close()
            return False
        try:
            stream.close()
        except OSError:
            pass
        reader.join(PROCESS_CLEANUP_GRACE_SECONDS)
        return reader.is_alive()

    @staticmethod
    def _finish_trace_reader(reader: _BoundedFDReader, descriptor: int) -> bool:
        reader.join(PROCESS_CLEANUP_GRACE_SECONDS)
        forced = reader.is_alive()
        if forced:
            reader.stop()
            reader.join(PROCESS_CLEANUP_GRACE_SECONDS)
        try:
            os.close(descriptor)
        except OSError:
            pass
        if reader.is_alive():
            reader.join(PROCESS_CLEANUP_GRACE_SECONDS)
        return forced or reader.is_alive()

    @staticmethod
    def _terminate_process(process: subprocess.Popen) -> bool:
        """Terminate the controllable foreground group; return uncertainty."""
        uncertain = False
        cleanup_deadline = time.monotonic() + PROCESS_CLEANUP_GRACE_SECONDS
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            elif sys.platform == "win32":
                process.send_signal(signal.CTRL_BREAK_EVENT)
                # A Windows process group does not contain descendants like a
                # job object, so cleanup cannot account for every descendant.
                uncertain = True
            else:
                process.terminate()
                uncertain = True
        except (OSError, ProcessLookupError):
            uncertain = True

        try:
            process.wait(timeout=max(0, cleanup_deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass

        if os.name == "posix":
            while GitRunner._posix_process_group_exists(process.pid):
                remaining = cleanup_deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.01, remaining))
            if not GitRunner._posix_process_group_exists(process.pid):
                return uncertain
        elif process.poll() is not None:
            return uncertain

        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
                uncertain = True
            process.wait(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
        except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
            uncertain = True
        return uncertain

    @staticmethod
    def _posix_process_group_exists(group_id: int) -> bool:
        try:
            os.killpg(group_id, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True


class _DeadlineRunner:
    """Share one deadline across several GitRunner calls."""

    def __init__(
        self,
        runner,
        *,
        deadline_ns: int,
        clock_ns: Callable[[], int],
    ):
        self._runner = runner
        self._deadline_ns = deadline_ns
        self._clock_ns = clock_ns
        self.executable = getattr(runner, "executable", None)
        self.deadline_reached = False

    def run(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: float = DEFAULT_PROCESS_TIMEOUT_SECONDS,
        **kwargs,
    ) -> GitResult:
        remaining_ns = self._deadline_ns - self._clock_ns()
        if remaining_ns <= 0:
            self.deadline_reached = True
            return GitResult(
                outcome="operation_deadline",
                returncode=None,
                stdout=b"",
                stdout_truncated=False,
                stderr_state="unavailable",
                elapsed_ns=0,
            )
        result = self._runner.run(
            args,
            timeout_seconds=min(timeout_seconds, remaining_ns / 1_000_000_000),
            **kwargs,
        )
        if self._clock_ns() >= self._deadline_ns:
            self.deadline_reached = True
        return result


@dataclasses.dataclass(frozen=True)
class _RepositoryContext:
    cwd: str
    root: Optional[str]
    git_dir: str
    common_dir: str
    bare: bool
    kind: str


class _CheckCollector:
    """Collect repository facts without running the measured workload."""

    def __init__(
        self,
        runner,
        *,
        cwd: str,
        environ: Mapping[str, str],
    ):
        self.runner = runner
        self.cwd = cwd
        self.environ = environ
        self.facts: list[Fact] = []
        self.findings: list[Finding] = []
        self.diagnostics: list[Diagnostic] = []
        self.partial = False

    def collect(self) -> Report:
        version = self._collect_version()
        repository = self._discover_repository()
        if repository is None:
            return self._report(status="failed", subject={"kind": "none"})

        self._collect_uncertainty_axes()
        self._collect_redirects()
        self._collect_repository_shape(repository)
        self._collect_index(repository)
        self._collect_fsmonitor_config(repository)
        self._collect_checkout_context(repository)
        self._add_findings(version)

        return self._report(
            status="partial" if self.partial else "complete",
            subject={"kind": "current_repository", "shape": repository.kind},
        )

    def _report(self, *, status: str, subject: dict[str, Any]) -> Report:
        report = Report(
            status=status,
            tool={
                "name": "git-perf",
                "implementation": "strict-check-prototype",
                "launcher_provenance": "unknown",
                "operation": "check",
            },
            subject=subject,
            facts=self.facts,
            findings=self.findings,
            diagnostics=self.diagnostics,
            sources=[
                {
                    "id": "source.git-config",
                    "kind": "upstream_git",
                    "title": "Git configuration semantics",
                }
            ],
        )
        report.canonicalize()
        return report

    def _run(
        self,
        args: Sequence[str],
        *,
        cwd: Optional[str] = None,
        output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES,
    ) -> GitResult:
        result = self.runner.run(
            args,
            cwd=self.cwd if cwd is None else cwd,
            effect_profile=STRICT_EFFECT_PROFILE,
            output_limit_bytes=output_limit_bytes,
        )
        if result.cleanup_uncertain:
            self._diagnose(
                "process.cleanup_uncertain",
                "warning",
                "A Git process may have outlived its timeout. Treat the "
                "collected facts as incomplete.",
            )
        return result

    def _collect_version(self) -> tuple[int, ...]:
        result = self._run(["version"], cwd=None, output_limit_bytes=4096)
        version: tuple[int, ...] = ()
        if result.ok and not result.stdout_truncated:
            text = result.stdout.decode("ascii", "ignore")
            version = _parse_git_version(text)
        if version:
            self.facts.append(
                Fact(
                    id="git.selected.version",
                    domain="git_client",
                    state="known",
                    value={
                        "components": list(version),
                        "display": ".".join(str(part) for part in version),
                    },
                    provenance="selected_git",
                )
            )
        else:
            self.facts.append(
                Fact(
                    id="git.selected.version",
                    domain="git_client",
                    state="unavailable",
                    value=None,
                    provenance="selected_git",
                    limitations=("version_output_unrecognized",),
                )
            )
            self._diagnose(
                "git.version_unavailable",
                "warning",
                "Git returned a version string that git-perf does not "
                "understand.",
            )
        return version

    def _collect_uncertainty_axes(self) -> None:
        """Record unknown context explicitly instead of inferring it."""
        self.facts.extend(
            [
                Fact(
                    id="git.dispatcher.identity",
                    domain="git_client",
                    state="known",
                    value="unknown",
                    provenance="direct_python_prototype",
                    limitations=(
                        "the_dispatching_git_is_outside_this_direct_entrypoint",
                    ),
                ),
                Fact(
                    id="git.additional_clients",
                    domain="git_client",
                    state="known",
                    value={"detected": 0, "completeness": "unknown"},
                    provenance="not_scanned",
                    limitations=(
                        "no_whole_disk_or_private_application_scan_performed",
                    ),
                ),
                Fact(
                    id="tool.distribution",
                    domain="tool",
                    state="known",
                    value="unknown",
                    provenance="direct_python_prototype",
                    limitations=(
                        "external_vendored_and_recipe_distribution_not_classified",
                    ),
                ),
                Fact(
                    id="policy.profiles",
                    domain="policy",
                    state="known",
                    value=[
                        {
                            "id": "generic_git",
                            "activation": "always",
                            "confidence": "confirmed",
                        }
                    ],
                    provenance="product_contract",
                ),
            ]
        )

    def _discover_repository(self) -> Optional[_RepositoryContext]:
        result = self._run(
            [
                "rev-parse",
                "--is-bare-repository",
                "--is-inside-work-tree",
                "--git-dir",
                "--git-common-dir",
            ],
            output_limit_bytes=64 * 1024,
        )
        lines = _fixed_lines(result, 4)
        if lines is None:
            diagnostic_id = (
                "repository.not_found"
                if result.outcome == "nonzero_exit"
                else "repository.discovery_failed"
            )
            summary = (
                "The current directory is not a usable Git repository."
                if result.outcome == "nonzero_exit"
                else "Git did not finish identifying the current repository."
            )
            self._diagnose(
                diagnostic_id,
                "error",
                summary,
                partial=False,
            )
            return None

        bare = _parse_git_bool(lines[0])
        inside_worktree = _parse_git_bool(lines[1])
        git_dir = _path_from_git_output(lines[2], self.cwd)
        common_dir = _path_from_git_output(lines[3], self.cwd)
        if (
            bare is None
            or inside_worktree is None
            or not git_dir
            or not common_dir
        ):
            self._diagnose(
                "repository.discovery_unrecognized",
                "error",
                "Git returned repository details that git-perf does not "
                "understand.",
                partial=False,
            )
            return None

        root = None
        if not bare:
            root_result = self._run(
                ["rev-parse", "--show-toplevel"],
                output_limit_bytes=64 * 1024,
            )
            root_line = _single_line(root_result)
            if root_line is None:
                self._diagnose(
                    "repository.root_unavailable",
                    "error",
                    "Git could not identify the current worktree root.",
                    partial=False,
                )
                return None
            root = _path_from_git_output(root_line, self.cwd)
            if root is None:
                self._diagnose(
                    "repository.root_unrecognized",
                    "error",
                    "The worktree root uses an unsupported path encoding.",
                    partial=False,
                )
                return None

        if bare:
            kind = "bare"
        elif _same_path(git_dir, common_dir):
            kind = "main_worktree"
        else:
            kind = "linked_worktree"

        self.facts.append(
            Fact(
                id="repository.kind",
                domain="repository",
                state="known",
                value=kind,
                provenance="git_rev_parse",
                limitations=(
                    "repository_discovery_does_not_prove_storage_ownership",
                ),
            )
        )
        return _RepositoryContext(
            cwd=self.cwd,
            root=root,
            git_dir=git_dir,
            common_dir=common_dir,
            bare=bare,
            kind=kind,
        )

    def _collect_redirects(self) -> None:
        self.facts.append(_environment_redirects_fact(self.environ))

    def _collect_repository_shape(self, repository: _RepositoryContext) -> None:
        shallow_result = self._run(
            ["rev-parse", "--is-shallow-repository"],
            output_limit_bytes=128,
        )
        shallow = _single_git_bool(shallow_result)
        if shallow is None:
            self.facts.append(
                Fact(
                    id="repository.shallow",
                    domain="repository",
                    state="unsupported",
                    value=None,
                    provenance="git_rev_parse",
                    limitations=("selected_git_did_not_report_shallow_state",),
                )
            )
        else:
            self.facts.append(
                Fact(
                    id="repository.shallow",
                    domain="repository",
                    state="known",
                    value=shallow,
                    provenance="git_rev_parse",
                )
            )

        sparse_result = self._run(
            ["config", "--bool", "--get", "core.sparseCheckout"],
            output_limit_bytes=128,
        )
        if sparse_result.returncode == 1 and not sparse_result.stdout:
            sparse = False
            sparse_state = "known"
            sparse_limitations = ("unset_interpreted_as_disabled",)
        else:
            sparse = _single_git_bool(sparse_result)
            sparse_state = "known" if sparse is not None else "unavailable"
            sparse_limitations = (
                ()
                if sparse is not None
                else ("sparse_checkout_config_malformed_or_unavailable",)
            )
        self.facts.append(
            Fact(
                id="repository.sparse_checkout",
                domain="repository",
                state=sparse_state,
                value=sparse,
                provenance="git_config_allowlist",
                limitations=sparse_limitations,
            )
        )
        if sparse_state == "unavailable":
            self._diagnose(
                "repository.sparse_state_unavailable",
                "warning",
                "git-perf could not tell whether sparse checkout is enabled.",
            )

        partial = self._collect_partial_clone_state()
        self.facts.append(partial)
        if partial.state == "unavailable":
            self._diagnose(
                "repository.partial_clone_state_unavailable",
                "warning",
                "git-perf could not tell whether this is a partial clone.",
            )

        alternates_path = self._git_path("objects/info/alternates")
        if alternates_path is None:
            alternates_state = "unavailable"
            alternates_value = None
            alternates_limitations = ("alternates_path_unavailable",)
        else:
            alternates_state = "known"
            alternates_value = bool(
                self.environ.get("GIT_ALTERNATE_OBJECT_DIRECTORIES")
                or os.path.isfile(alternates_path)
            )
            alternates_limitations = (
                ()
                if alternates_value
                else (
                    "absence_does_not_prove_repository_local_storage_ownership",
                )
            )
        self.facts.append(
            Fact(
                id="repository.alternates",
                domain="repository",
                state=alternates_state,
                value=alternates_value,
                provenance="git_path_and_environment",
                limitations=alternates_limitations,
            )
        )
        if alternates_state == "unavailable":
            self._diagnose(
                "repository.alternates_state_unavailable",
                "warning",
                "git-perf could not tell whether this repository uses an "
                "alternate object store.",
            )

        self._collect_submodule_state(repository)
        self._collect_worktree_counts()

    def _collect_partial_clone_state(self) -> Fact:
        result = self._run(
            [
                "config",
                "--null",
                "--get-regexp",
                r"^(extensions\.partialclone|remote\..*\.promisor)$",
            ],
            output_limit_bytes=64 * 1024,
        )
        return _partial_clone_fact_from_result(result)

    def _collect_submodule_state(self, repository: _RepositoryContext) -> None:
        if repository.bare:
            self.facts.append(
                Fact(
                    id="repository.is_submodule",
                    domain="repository",
                    state="unavailable",
                    value=None,
                    provenance="git_rev_parse",
                    applicability="not_applicable_to_bare_repository",
                )
            )
            return
        result = self._run(
            ["rev-parse", "--show-superproject-working-tree"],
            output_limit_bytes=64 * 1024,
        )
        if not result.ok or result.stdout_truncated:
            state = "unsupported"
            value = None
            limitations = ("selected_git_did_not_report_superproject",)
        elif not result.stdout:
            state = "known"
            value = False
            limitations = ()
        elif _single_line(result) is not None:
            state = "known"
            value = True
            limitations = ("superproject_path_discarded",)
        else:
            state = "unavailable"
            value = None
            limitations = ("superproject_path_unrecognized",)
        self.facts.append(
            Fact(
                id="repository.is_submodule",
                domain="repository",
                state=state,
                value=value,
                provenance="git_rev_parse",
                limitations=limitations,
            )
        )
        if state == "unavailable":
            self._diagnose(
                "repository.submodule_state_unavailable",
                "warning",
                "git-perf could not tell whether this worktree is a submodule.",
            )

    def _collect_worktree_counts(self) -> None:
        result = self._run(
            ["worktree", "list", "--porcelain", "-z"],
            output_limit_bytes=1024 * 1024,
        )
        if result.ok and not result.stdout_truncated:
            count = _count_worktree_records(result.stdout, nul_delimited=True)
        else:
            result = self._run(
                ["worktree", "list", "--porcelain"],
                output_limit_bytes=1024 * 1024,
            )
            count = (
                _count_worktree_records(result.stdout, nul_delimited=False)
                if result.ok and not result.stdout_truncated
                else None
            )
        if count is None or count < 1:
            self.facts.append(
                Fact(
                    id="repository.worktrees",
                    domain="repository",
                    state="unavailable",
                    value=None,
                    provenance="git_worktree_list",
                    limitations=("worktree_list_unavailable_or_too_large",),
                )
            )
            self._diagnose(
                "repository.worktree_count_unavailable",
                "warning",
                "Git did not return a usable list of registered worktrees.",
            )
            return
        self.facts.append(
            Fact(
                id="repository.worktrees",
                domain="repository",
                state="known",
                value={
                    "registered": count,
                    "additional_linked": max(0, count - 1),
                },
                provenance="git_worktree_list",
                limitations=(
                    "paths_discarded",
                    "repository_local_config_is_a_shared_baseline",
                    "worktree_and_command_overrides_not_inspected",
                ),
            )
        )

    def _collect_index(self, repository: _RepositoryContext) -> None:
        if repository.bare:
            self.facts.append(
                Fact(
                    id="repository.index",
                    domain="index",
                    state="unavailable",
                    value=None,
                    provenance="direct_reader",
                    applicability="not_applicable_to_bare_repository",
                )
            )
            return
        index_path = self._git_path("index")
        if index_path is None:
            self.facts.append(
                Fact(
                    id="repository.index",
                    domain="index",
                    state="unavailable",
                    value=None,
                    provenance="direct_reader",
                    limitations=("index_path_unavailable",),
                )
            )
            self._diagnose(
                "index.path_unavailable",
                "warning",
                "Git did not return a usable index path, so git-perf skipped "
                "the index header.",
            )
            return

        state, value, limitations = read_index_header(index_path)
        self.facts.append(
            Fact(
                id="repository.index",
                domain="index",
                state=state,
                value=value,
                provenance="direct_reader",
                limitations=limitations,
            )
        )
        if state == "unavailable":
            self._diagnose(
                "index.read_unavailable",
                "warning",
                "The index changed concurrently or has an unsupported shape.",
            )

    def _git_path(self, name: str) -> Optional[str]:
        result = self._run(
            ["rev-parse", "--git-path", name],
            output_limit_bytes=64 * 1024,
        )
        line = _single_line(result)
        return (
            _path_from_git_output(line, self.cwd) if line is not None else None
        )

    def _collect_fsmonitor_config(self, repository: _RepositoryContext) -> None:
        del (
            repository
        )  # The runner's cwd/environment already select the subject.
        result = self._run(
            [
                "config",
                "--null",
                "--show-origin",
                "--show-scope",
                "--get-all",
                "core.fsmonitor",
            ],
            output_limit_bytes=64 * 1024,
        )
        assignments = _parse_scoped_config_assignments(result)
        if assignments is not None:
            effective = assignments[-1] if assignments else None
            effective_limitations = []
            if effective is not None:
                if effective["value_category"] in (
                    "boolean_true",
                    "empty_or_implicit_boolean",
                ):
                    effective_limitations.append(
                        "boolean_true_semantics_require_a_platform_capability_probe"
                    )
                if effective["value_category"] != "disabled":
                    effective_limitations.append(
                        "materialized_index_state_not_inspected"
                    )
            self.facts.append(
                Fact(
                    id="config.core_fsmonitor.assignments",
                    domain="worktree_scanning",
                    state="known" if assignments else "unset",
                    value=assignments,
                    provenance="git_config_allowlist",
                    limitations=(
                        ("raw_values_and_origins_discarded",)
                        if assignments
                        else ()
                    ),
                )
            )
            self.facts.append(
                Fact(
                    id="config.core_fsmonitor.effective",
                    domain="worktree_scanning",
                    state="known" if effective else "unset",
                    value=(
                        {
                            "mode": effective["value_category"],
                            "scope": effective["scope"],
                        }
                        if effective
                        else None
                    ),
                    provenance="git_config_allowlist",
                    limitations=tuple(effective_limitations),
                )
            )
            return

        fallback = self._run(
            ["config", "--null", "--get-all", "core.fsmonitor"],
            output_limit_bytes=64 * 1024,
        )
        values = _parse_unscoped_config_values(fallback)
        if values is None:
            self.facts.extend(
                [
                    Fact(
                        id="config.core_fsmonitor.assignments",
                        domain="worktree_scanning",
                        state="unavailable",
                        value=None,
                        provenance="git_config_allowlist",
                        limitations=("config_query_failed_or_too_large",),
                    ),
                    Fact(
                        id="config.core_fsmonitor.effective",
                        domain="worktree_scanning",
                        state="unavailable",
                        value=None,
                        provenance="git_config_allowlist",
                        limitations=("config_query_failed_or_too_large",),
                    ),
                ]
            )
            self._diagnose(
                "config.fsmonitor_unavailable",
                "warning",
                "Git did not return a usable value for core.fsmonitor.",
            )
            return

        self.facts.extend(
            [
                Fact(
                    id="config.core_fsmonitor.assignments",
                    domain="worktree_scanning",
                    state="unsupported" if values else "unset",
                    value=None,
                    provenance="git_config_allowlist",
                    limitations=(
                        "selected_git_did_not_correlate_scope_and_origin",
                    ),
                ),
                Fact(
                    id="config.core_fsmonitor.effective",
                    domain="worktree_scanning",
                    state="known" if values else "unset",
                    value={"mode": values[-1], "scope": "unknown"}
                    if values
                    else None,
                    provenance="git_config_allowlist",
                    limitations=(
                        (
                            "boolean_true_semantics_require_a_platform_capability_probe",
                        )
                        if values
                        and values[-1]
                        in ("boolean_true", "empty_or_implicit_boolean")
                        else ()
                    )
                    + (("effective_scope_unavailable",) if values else ()),
                ),
            ]
        )

    def _collect_checkout_context(self, repository: _RepositoryContext) -> None:
        alternates = self._fact("repository.alternates")
        if alternates and alternates.state == "known" and alternates.value:
            storage_ownership = "alternate_or_shared"
            storage_provenance = "git_path_and_environment"
        else:
            storage_ownership = "unknown"
            storage_provenance = "not_inferred_from_layout"
        self.facts.extend(
            [
                Fact(
                    id="checkout.execution_owner",
                    domain="checkout_context",
                    state="known",
                    value="unknown",
                    provenance="not_inferred_from_interactivity",
                ),
                Fact(
                    id="checkout.storage_ownership",
                    domain="checkout_context",
                    state="known",
                    value=storage_ownership,
                    provenance=storage_provenance,
                ),
                Fact(
                    id="checkout.lifecycle",
                    domain="checkout_context",
                    state="known",
                    value="unknown",
                    provenance="not_inferred_from_interactivity",
                ),
                Fact(
                    id="repository.identity",
                    domain="repository_identity",
                    state="known",
                    value={"family": "unknown", "confidence": "unknown"},
                    provenance="not_implemented",
                    limitations=(
                        "path_names_and_orchestrator_do_not_establish_identity",
                    ),
                ),
            ]
        )
        if repository.root is None:
            self.facts.extend(
                [
                    Fact(
                        id="checkout.orchestrator",
                        domain="checkout_context",
                        state="known",
                        value="unknown",
                        provenance="not_applicable",
                        applicability="bare_repository",
                    ),
                    Fact(
                        id="checkout.repository_role",
                        domain="checkout_context",
                        state="known",
                        value="unknown",
                        provenance="not_applicable",
                        applicability="bare_repository",
                    ),
                ]
            )
            return

        try:
            with _quiet_gclient_paths():
                gclient_root = gclient_paths.FindGclientRoot(repository.root)
                primary_name = (
                    gclient_paths.GetGClientPrimarySolutionName(gclient_root)
                    if gclient_root
                    else None
                )
        except Exception:  # A malformed local config is untrusted input.
            self.facts.extend(
                [
                    Fact(
                        id="checkout.orchestrator",
                        domain="checkout_context",
                        state="unavailable",
                        value=None,
                        provenance="gclient_paths",
                        limitations=("gclient_metadata_unavailable",),
                    ),
                    Fact(
                        id="checkout.repository_role",
                        domain="checkout_context",
                        state="known",
                        value="unknown",
                        provenance="gclient_paths",
                        limitations=("gclient_metadata_unavailable",),
                    ),
                ]
            )
            self._diagnose(
                "checkout.gclient_metadata_unavailable",
                "warning",
                "git-perf found gclient metadata but could not read its "
                "checkout layout.",
            )
            return

        if gclient_root:
            primary_path = os.path.join(gclient_root, primary_name or "src")
            role = (
                "primary_solution"
                if _same_path(repository.root, primary_path)
                else "unknown"
            )
            orchestrator = "gclient"
            provenance = "gclient_paths"
            role_limitations = (
                ()
                if role == "primary_solution"
                else ("non_primary_gclient_role_not_yet_classified",)
            )
        elif _find_ancestor_marker(repository.root, ".repo"):
            orchestrator = "repo"
            role = "unknown"
            provenance = "ancestor_marker"
            role_limitations = (
                "repo_marker_does_not_prove_project_or_storage_ownership",
            )
        else:
            orchestrator = "none"
            role = "standalone"
            provenance = "bounded_ancestor_discovery"
            role_limitations = ("automation_and_cache_ownership_not_inferred",)

        self.facts.extend(
            [
                Fact(
                    id="checkout.orchestrator",
                    domain="checkout_context",
                    state="known",
                    value=orchestrator,
                    provenance=provenance,
                    limitations=("orchestration_root_path_discarded",),
                ),
                Fact(
                    id="checkout.repository_role",
                    domain="checkout_context",
                    state="known",
                    value=role,
                    provenance=provenance,
                    limitations=role_limitations,
                ),
            ]
        )

    def _add_findings(self, version: tuple[int, ...]) -> None:
        if version:
            self.findings.append(
                Finding(
                    id="git.selected_client_identified",
                    kind="confirmation",
                    domain="git_client",
                    priority=40,
                    summary="git-perf recognized the selected Git version.",
                    evidence_fact_ids=("git.selected.version",),
                )
            )

        worktrees = self._fact("repository.worktrees")
        if worktrees and worktrees.state == "known":
            additional = worktrees.value["additional_linked"]
            if additional:
                self.findings.append(
                    Finding(
                        id="repository.local_scope_shared",
                        kind="information",
                        domain="repository",
                        priority=50,
                        summary=(
                            "Repository-local configuration also applies to "
                            f"{additional} linked "
                            f"{_plural(additional, 'worktree', 'worktrees')} "
                            "unless a worktree or command overrides it."
                        ),
                        evidence_fact_ids=("repository.worktrees",),
                    )
                )

        fsmonitor = self._fact("config.core_fsmonitor.effective")
        if fsmonitor and fsmonitor.state == "known":
            mode = fsmonitor.value["mode"]
            scope = fsmonitor.value["scope"]
            if scope in ("global", "system") and mode != "disabled":
                self.findings.append(
                    Finding(
                        id="config.fsmonitor_broad_scope",
                        kind="warning",
                        domain="worktree_scanning",
                        priority=10,
                        summary=(
                            "core.fsmonitor is enabled in global or system "
                            "configuration, so Git may use it in other "
                            "repositories too."
                        ),
                        evidence_fact_ids=(
                            "config.core_fsmonitor.effective",
                            "checkout.orchestrator",
                        ),
                        blockers=(
                            "This check did not measure whether FSMonitor "
                            "helps or hurts Git status.",
                        ),
                        source_ids=("source.git-config",),
                    )
                )
            if mode == "external_hook":
                self.findings.append(
                    Finding(
                        id="config.fsmonitor_external_hook",
                        kind="information",
                        domain="worktree_scanning",
                        priority=45,
                        summary=(
                            "Git status may run an external FSMonitor hook. "
                            "This check did not inspect or execute the hook."
                        ),
                        evidence_fact_ids=("config.core_fsmonitor.effective",),
                    )
                )

        self.findings.append(
            Finding(
                id="policy.no_causal_action",
                kind="information",
                domain="policy",
                priority=90,
                summary=(
                    "The setup check alone cannot identify the cause of slow "
                    "Git commands."
                ),
                blockers=(
                    "Git status was not timed.",
                    "No Trace2 sample was collected.",
                    "Repository-specific support rules were not identified.",
                ),
            )
        )

    def _fact(self, fact_id: str) -> Optional[Fact]:
        return next((fact for fact in self.facts if fact.id == fact_id), None)

    def _diagnose(
        self,
        diagnostic_id: str,
        severity: str,
        summary: str,
        *,
        partial: bool = True,
    ) -> None:
        self.partial = self.partial or partial
        if any(item.id == diagnostic_id for item in self.diagnostics):
            return
        self.diagnostics.append(Diagnostic(diagnostic_id, severity, summary))


class _BenchmarkCollector:
    """Time Git status with the worktree's current configuration."""

    def __init__(
        self,
        runner,
        *,
        cwd: str,
        samples: int,
        warmup: int,
        deadline_seconds: int,
        clock_ns: Callable[[], int],
        environ: Optional[Mapping[str, str]] = None,
        progress: Optional[Callable[[str], None]] = None,
        absolute_deadline_ns: Optional[int] = None,
    ):
        self.runner = runner
        self.cwd = cwd
        self.samples = samples
        self.warmup = warmup
        self.deadline_seconds = deadline_seconds
        self.clock_ns = clock_ns
        self.environ = dict(environ or {})
        self.progress = progress
        self.optional_locks_mode = _environment_git_bool_mode(
            self.environ, "GIT_OPTIONAL_LOCKS"
        )
        self.redirects_present = any(
            key in self.environ for key in _PATH_BEARING_GIT_VARIABLES
        )
        self.started_ns = clock_ns()
        requested_deadline_ns = (
            self.started_ns + deadline_seconds * 1_000_000_000
        )
        self.deadline_ns = (
            requested_deadline_ns
            if absolute_deadline_ns is None
            else min(requested_deadline_ns, absolute_deadline_ns)
        )
        available_ns = max(0, self.deadline_ns - self.started_ns)
        reserve_seconds = min(
            DEFAULT_BENCHMARK_POSTFLIGHT_RESERVE_SECONDS,
            available_ns / 5_000_000_000,
        )
        self.reserve_ns = int(reserve_seconds * 1_000_000_000)
        self.sampling_deadline_ns = self.deadline_ns - self.reserve_ns
        self.executions: list[dict[str, Any]] = []
        self.execution_observations: list[
            Optional[tuple[StatusCounts, bytes]]
        ] = []
        self._status_stability_key = os.urandom(32)
        self.facts: list[Fact] = []
        self.diagnostics: list[Diagnostic] = []
        self.deadline_limited = False

    def collect(self) -> Report:
        executable_before = _selected_executable_identity(self.runner)
        version_before, version = self._read_version(self.sampling_deadline_ns)
        self._record_version_fact(version)

        repository = self._discover_repository(self.sampling_deadline_ns)
        if repository is None:
            return self._report(status="failed", shape="none")
        if repository.bare:
            self._diagnose(
                "benchmark.worktree_required",
                "error",
                "The benchmark needs a working tree, but this repository is "
                "bare.",
            )
            return self._report(status="failed", shape=repository.kind)

        partial_clone = self._read_partial_clone_state(
            self.sampling_deadline_ns
        )
        self.facts.append(partial_clone)
        if partial_clone.state != "known":
            self._diagnose(
                "benchmark.partial_clone_preflight_unavailable",
                "error",
                "git-perf could not tell whether this is a partial clone. It "
                "did not run Git status because that command might fetch "
                "missing objects.",
            )
            return self._report(status="failed", shape=repository.kind)
        if partial_clone.value:
            self._diagnose(
                "benchmark.partial_clone_not_supported",
                "error",
                "git-perf did not run Git status in this partial clone. The "
                "prototype has not verified that it can prevent this Git "
                "version from fetching missing objects.",
            )
            return self._report(status="failed", shape=repository.kind)

        config_before = self._read_status_config(self.sampling_deadline_ns)
        self._record_effect_preflight(config_before)
        self._announce_workload_effects()
        first = self._run_workload("first_after_preflight")
        if first is None:
            self.deadline_limited = True
            self._diagnose(
                "benchmark.first_not_run",
                "error",
                "The deadline expired before the first Git status run.",
            )
        elif not first["valid_elapsed"]:
            self._diagnose(
                "benchmark.first_failed",
                "error",
                "The first Git status run failed or timed out.",
            )
        else:
            warmup_complete = self._run_repetitions("warmup", self.warmup)
            if warmup_complete:
                self._run_repetitions("measured", self.samples)

        version_after, _ = self._read_version(self.deadline_ns)
        config_after = self._read_status_config(self.deadline_ns)
        if self.clock_ns() < self.deadline_ns:
            executable_after = _selected_executable_identity(self.runner)
        else:
            executable_after = None
            self.deadline_limited = True

        measurement = self._build_measurement(
            version_before=version_before,
            version_after=version_after,
            executable_before=executable_before,
            executable_after=executable_after,
            config_before=config_before,
            config_after=config_after,
        )
        quality = measurement["quality"]
        first_valid = bool(
            self.executions and self.executions[0]["valid_elapsed"]
        )
        if not first_valid:
            status = "failed"
        elif (
            self.deadline_limited
            or quality["outcome_validity"] != "complete"
            or quality["client_stability"] != "observed_stable"
            or quality["config_stability"] != "observed_stable"
            or quality["worktree_state_stability"] != "observed_stable"
        ):
            status = "partial"
        else:
            status = "complete"
        return self._report(
            status=status,
            shape=repository.kind,
            measurement=measurement,
        )

    def _run_repetitions(self, role: str, count: int) -> bool:
        for _ in range(count):
            execution = self._run_workload(role)
            if execution is None:
                self.deadline_limited = True
                self._diagnose(
                    "benchmark.deadline_limited",
                    "warning",
                    "The deadline expired before all benchmark runs finished.",
                )
                return False
        return True

    def _run(
        self,
        args: Sequence[str],
        *,
        cutoff_ns: int,
        effect_profile: str,
        cwd: Optional[str] = None,
        output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES,
        stdout_consumer: Optional[Callable[[bytes], None]] = None,
        trace2_event_consumer: Optional[Callable[[bytes], None]] = None,
    ) -> Optional[GitResult]:
        remaining_ns = cutoff_ns - self.clock_ns()
        if remaining_ns <= 0:
            return None
        timeout_seconds = min(
            DEFAULT_BENCHMARK_RUN_TIMEOUT_SECONDS,
            remaining_ns / 1_000_000_000,
        )
        result = self.runner.run(
            args,
            cwd=self.cwd if cwd is None else cwd,
            effect_profile=effect_profile,
            timeout_seconds=timeout_seconds,
            output_limit_bytes=output_limit_bytes,
            stdout_consumer=stdout_consumer,
            trace2_event_consumer=trace2_event_consumer,
        )
        if result.cleanup_uncertain:
            self._diagnose(
                "process.cleanup_uncertain",
                "warning",
                "A Git process may have outlived its timeout. Do not rely on "
                "the affected timing.",
            )
        return result

    def _read_version(
        self, cutoff_ns: int
    ) -> tuple[Optional[bytes], tuple[int, ...]]:
        result = self._run(
            ["version"],
            cutoff_ns=cutoff_ns,
            effect_profile=STRICT_EFFECT_PROFILE,
            cwd=None,
            output_limit_bytes=4096,
        )
        if (
            result is None
            or not result.ok
            or result.stdout_truncated
            or result.stdout_read_failed
        ):
            return None, ()
        text = result.stdout.decode("ascii", "ignore")
        version = _parse_git_version(text)
        return result.stdout, version

    def _record_version_fact(self, version: tuple[int, ...]) -> None:
        if version:
            self.facts.append(
                Fact(
                    id="git.selected.version",
                    domain="git_client",
                    state="known",
                    value={
                        "components": list(version),
                        "display": ".".join(str(part) for part in version),
                    },
                    provenance="selected_git",
                )
            )
        else:
            self.facts.append(
                Fact(
                    id="git.selected.version",
                    domain="git_client",
                    state="unavailable",
                    value=None,
                    provenance="selected_git",
                    limitations=("version_output_unrecognized",),
                )
            )

    def _discover_repository(
        self, cutoff_ns: int
    ) -> Optional[_RepositoryContext]:
        result = self._run(
            [
                "rev-parse",
                "--is-bare-repository",
                "--is-inside-work-tree",
                "--git-dir",
                "--git-common-dir",
            ],
            cutoff_ns=cutoff_ns,
            effect_profile=STRICT_EFFECT_PROFILE,
            output_limit_bytes=64 * 1024,
        )
        if result is None:
            self._diagnose(
                "benchmark.preflight_deadline",
                "error",
                "The deadline expired while git-perf was checking the "
                "repository before the benchmark.",
            )
            return None
        lines = _fixed_lines(result, 4)
        if lines is None:
            diagnostic_id = (
                "repository.not_found"
                if result.outcome == "nonzero_exit"
                else "repository.discovery_failed"
            )
            self._diagnose(
                diagnostic_id,
                "error",
                "The current directory is not a usable Git repository.",
            )
            return None

        bare = _parse_git_bool(lines[0])
        inside_worktree = _parse_git_bool(lines[1])
        git_dir = _path_from_git_output(lines[2], self.cwd)
        common_dir = _path_from_git_output(lines[3], self.cwd)
        if (
            bare is None
            or inside_worktree is None
            or not git_dir
            or not common_dir
            or (not bare and not inside_worktree)
        ):
            self._diagnose(
                "repository.discovery_unrecognized",
                "error",
                "Git returned repository details that git-perf does not "
                "understand.",
            )
            return None

        kind = (
            "bare"
            if bare
            else "main_worktree"
            if _same_path(git_dir, common_dir)
            else "linked_worktree"
        )
        self.facts.append(
            Fact(
                id="repository.kind",
                domain="repository",
                state="known",
                value=kind,
                provenance="git_rev_parse",
            )
        )
        return _RepositoryContext(
            cwd=self.cwd,
            root=None,
            git_dir=git_dir,
            common_dir=common_dir,
            bare=bare,
            kind=kind,
        )

    def _read_status_config(self, cutoff_ns: int) -> Optional[bytes]:
        result = self._run(
            [
                "config",
                "--null",
                "--get-regexp",
                _STATUS_CONFIG_PATTERN,
            ],
            cutoff_ns=cutoff_ns,
            effect_profile=STRICT_EFFECT_PROFILE,
            output_limit_bytes=DEFAULT_OUTPUT_LIMIT_BYTES,
        )
        if (
            result is None
            or result.stdout_truncated
            or result.stdout_read_failed
        ):
            return None
        if result.ok:
            return result.stdout
        if (
            result.returncode == 1
            and not result.stdout
            and result.stderr_state == "empty"
        ):
            return b""
        return None

    def _read_partial_clone_state(self, cutoff_ns: int) -> Fact:
        result = self._run(
            [
                "config",
                "--null",
                "--get-regexp",
                r"^(extensions\.partialclone|remote\..*\.promisor)$",
            ],
            cutoff_ns=cutoff_ns,
            effect_profile=STRICT_EFFECT_PROFILE,
            output_limit_bytes=64 * 1024,
        )
        if result is None:
            return Fact(
                id="repository.partial_clone",
                domain="repository",
                state="unavailable",
                value=None,
                provenance="git_config_allowlist",
                limitations=("preflight_deadline_exceeded",),
            )
        return _partial_clone_fact_from_result(result)

    def _record_effect_preflight(self, config: Optional[bytes]) -> None:
        fsmonitor = _status_fsmonitor_mode(config)
        self.facts.extend(
            [
                Fact(
                    id="workload.effects.fsmonitor",
                    domain="workload",
                    state=(
                        "unavailable" if fsmonitor == "unknown" else "known"
                    ),
                    value=None if fsmonitor == "unknown" else fsmonitor,
                    provenance="workload_config_preflight",
                    limitations=(
                        ("status_config_unavailable",)
                        if fsmonitor == "unknown"
                        else ()
                    ),
                ),
                Fact(
                    id="workload.effects.configured_status",
                    domain="workload",
                    state="known",
                    value={
                        "external_hook_may_run": fsmonitor
                        in ("external_hook", "ambiguous", "unknown"),
                        "built_in_daemon_may_start": fsmonitor
                        in ("built_in", "ambiguous", "unknown"),
                        "initialized_submodules_may_be_inspected": True,
                        "derived_state_may_be_updated": True,
                        "optional_locks": self.optional_locks_mode,
                    },
                    provenance="declared_workload_contract",
                    limitations=("arbitrary_hook_effects_are_not_contained",),
                ),
                _environment_redirects_fact(self.environ),
            ]
        )

    def _announce_workload_effects(self) -> None:
        if self.progress is None:
            return
        maximum_runs = 1 + self.warmup + self.samples
        message = (
            f"About to run Git status up to {maximum_runs} "
            f"{_plural(maximum_runs, 'time', 'times')} within "
            f"{self.deadline_seconds}s, using the repository's current "
            "configuration.\n"
            "Possible effects: FSMonitor, hooks, and initialized submodules "
            "may run, and Git may update optional caches. A hook can make any "
            "change allowed to it.\n"
            "git-perf itself does not edit Git configuration."
        )
        if self.redirects_present:
            message += (
                "\nGit path overrides are set in the environment, so these "
                "effects may apply outside the current checkout."
            )
        try:
            self.progress(message)
        except (OSError, ValueError):
            # Losing the progress stream does not invalidate completed timings.
            pass

    def _run_workload(self, role: str) -> Optional[dict[str, Any]]:
        parser = PorcelainV2StatusParser(
            byte_limit=STATUS_PARSE_BYTE_LIMIT,
            record_limit=STATUS_PARSE_RECORD_LIMIT,
            stability_key=self._status_stability_key,
        )
        result = self._run(
            STATUS_WORKLOAD_ARGS,
            cutoff_ns=self.sampling_deadline_ns,
            effect_profile=NORMAL_GIT_EFFECT_PROFILE,
            output_limit_bytes=0,
            stdout_consumer=parser.consume,
        )
        if result is None:
            return None

        parsed = parser.finish()
        if result.stdout_consumer_failed:
            parsed = StatusParseResult(
                state="unavailable",
                counts=None,
                bytes_seen=parsed.bytes_seen,
                records_seen=parsed.records_seen,
                reason="status_consumer_failed",
            )
        elif result.stdout_read_failed:
            parsed = StatusParseResult(
                state="unavailable",
                counts=None,
                bytes_seen=parsed.bytes_seen,
                records_seen=parsed.records_seen,
                reason="stdout_read_failed",
            )
        elif not result.ok:
            parsed = StatusParseResult(
                state="unavailable",
                counts=None,
                bytes_seen=parsed.bytes_seen,
                records_seen=parsed.records_seen,
                reason="workload_did_not_complete",
            )

        execution = {
            "order": len(self.executions) + 1,
            "role": role,
            "outcome": result.outcome,
            "returncode": result.returncode,
            "elapsed_ns": result.elapsed_ns,
            "valid_elapsed": (
                result.ok
                and not result.stdout_read_failed
                and not result.cleanup_uncertain
            ),
            "included_in_summary": False,
            "stderr_state": result.stderr_state,
            "cleanup_uncertain": result.cleanup_uncertain,
            "cleanup_elapsed_ns": result.cleanup_elapsed_ns,
            "status": {
                "state": parsed.state,
                "counts": (
                    dataclasses.asdict(parsed.counts)
                    if parsed.counts is not None
                    else None
                ),
                "bytes_seen": parsed.bytes_seen,
                "records_seen": parsed.records_seen,
                "reason": parsed.reason,
            },
        }
        self.executions.append(execution)
        self.execution_observations.append(
            (
                (parsed.counts, parsed.status_fingerprint)
                if parsed.counts is not None
                and parsed.status_fingerprint is not None
                else None
            )
        )
        return execution

    def _build_measurement(
        self,
        *,
        version_before: Optional[bytes],
        version_after: Optional[bytes],
        executable_before: Optional[tuple[int, int, int, int]],
        executable_after: Optional[tuple[int, int, int, int]],
        config_before: Optional[bytes],
        config_after: Optional[bytes],
    ) -> dict[str, Any]:
        client_stability = _snapshot_stability(
            (version_before, executable_before),
            (version_after, executable_after),
        )
        config_stability = _snapshot_stability(config_before, config_after)
        if not self.execution_observations or any(
            observation is None for observation in self.execution_observations
        ):
            worktree_stability = "unavailable"
        elif all(
            observation == self.execution_observations[0]
            for observation in self.execution_observations[1:]
        ):
            worktree_stability = "observed_stable"
        else:
            worktree_stability = "observed_changed"

        measured = [
            execution
            for execution in self.executions
            if execution["role"] == "measured"
        ]
        valid_measured = [
            execution for execution in measured if execution["valid_elapsed"]
        ]
        for execution in valid_measured:
            execution["included_in_summary"] = True

        expected_runs = 1 + self.warmup + self.samples
        invalid_runs = sum(
            not execution["valid_elapsed"] for execution in self.executions
        )
        if not self.executions or not self.executions[0]["valid_elapsed"]:
            outcome_validity = "failed"
        elif len(self.executions) == expected_runs and invalid_runs == 0:
            outcome_validity = "complete"
        else:
            outcome_validity = "limited"

        summary_allowed = (
            bool(valid_measured)
            and client_stability == "observed_stable"
            and config_stability == "observed_stable"
            and worktree_stability == "observed_stable"
        )
        summary = None
        if summary_allowed:
            values = [execution["elapsed_ns"] for execution in valid_measured]
            median_ns = statistics.median(values)
            deviations = [abs(value - median_ns) for value in values]
            spread = (
                {
                    "kind": "raw_mad",
                    "value_ns": statistics.median(deviations),
                }
                if len(values) >= 2
                else {
                    "kind": "unavailable",
                    "reason": "fewer_than_two_valid_samples",
                }
            )
            summary = {
                "estimand": "external_elapsed_ns",
                "condition": (
                    "all_scheduled_runs"
                    if invalid_runs == 0
                    else "successful_completion_only"
                ),
                "estimator": "median",
                "median_ns": median_ns,
                "spread": spread,
                "minimum_ns": min(values),
                "maximum_ns": max(values),
                "valid_n": len(values),
                "target_n": self.samples,
            }

        limitations = []
        if self.warmup == 0:
            limitations.append("no_declared_warmup")
        else:
            limitations.append("warmup_does_not_prove_steady_state")
        if self.deadline_limited:
            limitations.append("deadline_limited_sample_count")
        if invalid_runs:
            limitations.append("failed_or_censored_executions_present")
        for name, state in (
            ("selected_client", client_stability),
            ("workload_config", config_stability),
            ("observable_worktree_state", worktree_stability),
        ):
            if state != "observed_stable":
                limitations.append(f"{name}_{state}")
        limitations.extend(
            [
                "single_machine_single_session",
                "environment_load_encountered_not_controlled",
                "serial_samples_may_be_dependent",
                "system_load_not_measured_in_this_slice",
            ]
        )

        elapsed_ns = max(0, self.clock_ns() - self.started_ns)
        return {
            "id": STATUS_WORKLOAD_ID,
            "workload_version": STATUS_WORKLOAD_VERSION,
            "effect_profile": NORMAL_GIT_EFFECT_PROFILE,
            "command": ["git", "--no-pager", *STATUS_WORKLOAD_ARGS],
            "estimand": {
                "metric": "external_elapsed_ns",
                "population_scope": (
                    "this invocation and encountered machine state"
                ),
                "claim_level": "descriptive_diagnostic",
            },
            "semantic_inputs": {
                "untracked": "configured",
                "ahead_behind": "configured",
                "renames": "configured",
                "submodules": "configured",
                "optional_locks": self.optional_locks_mode,
                "fsmonitor": "configured",
                "untracked_cache": "configured",
            },
            "clock": "monotonic_ns",
            "cache_regime": (
                "post_declared_warmup" if self.warmup else "uncontrolled"
            ),
            "environment_regime": "encountered",
            "controls": {
                "target_samples": self.samples,
                "warmup_runs": self.warmup,
                "maximum_workload_runs": expected_runs,
                "deadline_seconds": self.deadline_seconds,
                "per_run_timeout_seconds": (
                    DEFAULT_BENCHMARK_RUN_TIMEOUT_SECONDS
                ),
                "postflight_reserve_ns": self.reserve_ns,
            },
            "deadline": {
                "state": "limited" if self.deadline_limited else "within",
                "elapsed_ns": elapsed_ns,
            },
            "executions": self.executions,
            "summary": summary,
            "quality": {
                "outcome_validity": outcome_validity,
                "client_stability": client_stability,
                "config_stability": config_stability,
                "worktree_state_stability": worktree_stability,
                "temporal_stability": "limited",
                "external_validity": "limited",
                "limitations": limitations,
            },
        }

    def _report(
        self,
        *,
        status: str,
        shape: str,
        measurement: Optional[dict[str, Any]] = None,
    ) -> Report:
        report = Report(
            status=status,
            tool={
                "name": "git-perf",
                "implementation": "benchmark-prototype",
                "launcher_provenance": "unknown",
                "operation": "bench",
            },
            subject=(
                {"kind": "none"}
                if shape == "none"
                else {"kind": "current_repository", "shape": shape}
            ),
            facts=self.facts,
            measurements=[] if measurement is None else [measurement],
            diagnostics=self.diagnostics,
            sources=[
                {
                    "id": "source.git-status",
                    "kind": "upstream_git",
                    "title": "Git status porcelain format",
                }
            ],
        )
        report.canonicalize()
        return report

    def _diagnose(
        self, diagnostic_id: str, severity: str, summary: str
    ) -> None:
        if any(item.id == diagnostic_id for item in self.diagnostics):
            return
        self.diagnostics.append(Diagnostic(diagnostic_id, severity, summary))


@dataclasses.dataclass(frozen=True)
class _TraceHandshake:
    usable: bool
    state: str
    diagnostics: tuple[str, ...]


def _trace2_transport_handshake(
    runner,
    *,
    cutoff_ns: int,
    clock_ns: Callable[[], int],
) -> _TraceHandshake:
    """Check that Git can write a complete Trace2 stream to a numeric FD."""
    remaining_ns = cutoff_ns - clock_ns()
    if remaining_ns <= 0:
        return _TraceHandshake(False, "unavailable", ("handshake_deadline",))
    parser = git_perf_trace.Trace2EventParser(
        byte_limit=min(git_perf_trace.DEFAULT_TRACE_BYTE_LIMIT, 1024 * 1024),
        event_limit=10_000,
    )
    result = runner.run(
        ["version"],
        cwd=None,
        effect_profile=STRICT_EFFECT_PROFILE,
        timeout_seconds=min(
            DEFAULT_TRACE_HANDSHAKE_TIMEOUT_SECONDS,
            remaining_ns / 1_000_000_000,
        ),
        output_limit_bytes=4096,
        trace2_event_consumer=parser.consume,
    )
    if result.trace_consumer_failed:
        parser.mark_transport_limit("trace_consumer_failed")
    if result.trace_read_failed:
        parser.mark_transport_limit("trace_read_failed")
    parsed = parser.finish()
    event_types = parsed.trace.get("events", {}).get("by_type", {})
    has_envelope = all(
        event_types.get(name, 0) >= 1 for name in ("version", "start", "atexit")
    )
    diagnostics = list(parsed.diagnostics)
    if result.outcome == "trace_transport_unavailable":
        diagnostics.append("trace_transport_unavailable")
    elif not result.ok:
        diagnostics.append("handshake_git_failed")
    if result.cleanup_uncertain:
        diagnostics.append("handshake_cleanup_uncertain")
    if not has_envelope:
        diagnostics.append("handshake_envelope_missing")
    usable = (
        result.ok
        and not result.cleanup_uncertain
        and parsed.state == "complete"
        and has_envelope
    )
    return _TraceHandshake(
        usable,
        "available" if usable else "unavailable",
        tuple(dict.fromkeys(diagnostics)),
    )


class _LiveTraceCollector(_BenchmarkCollector):
    """Capture Trace2 events from one Git status run."""

    def __init__(
        self,
        runner,
        *,
        cwd: str,
        deadline_seconds: int,
        clock_ns: Callable[[], int],
        environ: Mapping[str, str],
        progress: Optional[Callable[[str], None]],
        handshake: Optional[_TraceHandshake] = None,
        absolute_deadline_ns: Optional[int] = None,
    ):
        super().__init__(
            runner,
            cwd=cwd,
            samples=0,
            warmup=0,
            deadline_seconds=deadline_seconds,
            clock_ns=clock_ns,
            environ=environ,
            progress=progress,
            absolute_deadline_ns=absolute_deadline_ns,
        )
        self.handshake = handshake

    def collect(self) -> Report:
        executable_before = _selected_executable_identity(self.runner)
        version_before, version = self._read_version(self.sampling_deadline_ns)
        self._record_version_fact(version)

        handshake = self.handshake or _trace2_transport_handshake(
            self.runner,
            cutoff_ns=self.sampling_deadline_ns,
            clock_ns=self.clock_ns,
        )
        self.facts.append(
            Fact(
                id="trace2.transport",
                domain="trace",
                state="known" if handshake.usable else "unsupported",
                value="numeric_fd_stream" if handshake.usable else None,
                provenance="selected_git_runtime_handshake",
                limitations=() if handshake.usable else handshake.diagnostics,
            )
        )

        repository = self._discover_repository(self.sampling_deadline_ns)
        if repository is None:
            return self._report(status="failed", shape="none")
        if repository.bare:
            self._diagnose(
                "trace.worktree_required",
                "error",
                "A live trace needs a working tree, but this repository is "
                "bare.",
            )
            return self._report(status="failed", shape=repository.kind)

        partial_clone = self._read_partial_clone_state(
            self.sampling_deadline_ns
        )
        self.facts.append(partial_clone)
        if partial_clone.state != "known" or partial_clone.value:
            self._diagnose(
                "trace.partial_clone_preflight_blocked",
                "error",
                "git-perf did not run Git status because this may be a "
                "partial clone. The prototype has not verified that it can "
                "prevent this Git version from fetching missing objects.",
            )
            return self._report(status="failed", shape=repository.kind)

        config_before = self._read_status_config(self.sampling_deadline_ns)
        self._record_effect_preflight(config_before)
        if not handshake.usable:
            self._diagnose(
                "trace.transport_unavailable",
                "warning",
                "This Git version did not produce a complete Trace2 test "
                "stream on this platform, so git-perf did not run Git status.",
            )
            return self._report(status="partial", shape=repository.kind)

        self._announce_trace_effects()
        execution, parsed = self._run_traced_workload()
        if execution is None:
            self.deadline_limited = True
            self._diagnose(
                "trace.deadline_limited",
                "error",
                "The deadline expired before the traced Git status run.",
            )

        version_after, _ = self._read_version(self.deadline_ns)
        config_after = self._read_status_config(self.deadline_ns)
        executable_after = (
            _selected_executable_identity(self.runner)
            if self.clock_ns() < self.deadline_ns
            else None
        )
        if executable_after is None:
            self.deadline_limited = True

        measurement = self._build_trace_measurement(
            execution=execution,
            parsed=parsed,
            version_before=version_before,
            version_after=version_after,
            executable_before=executable_before,
            executable_after=executable_after,
            config_before=config_before,
            config_after=config_after,
        )
        if execution is None or not execution["valid_elapsed"]:
            status = "failed"
        elif (
            self.deadline_limited
            or parsed is None
            or parsed.state != "complete"
            or execution["cleanup_uncertain"]
            or measurement["quality"]["client_stability"] != "observed_stable"
            or measurement["quality"]["config_stability"] != "observed_stable"
        ):
            status = "partial"
        else:
            status = "complete"
        return self._report(
            status=status,
            shape=repository.kind,
            measurement=measurement,
        )

    def _announce_trace_effects(self) -> None:
        if self.progress is None:
            return
        message = (
            "About to run Git status once with Trace2 enabled, using the "
            "repository's current configuration and a "
            f"{self.deadline_seconds}s deadline.\n"
            "Possible effects: FSMonitor, hooks, and initialized submodules "
            "may run, and Git may update optional caches. A hook can make any "
            "change allowed to it.\n"
            "git-perf itself does not edit Git configuration."
        )
        if self.redirects_present:
            message += (
                "\nGit path overrides are set in the environment, so these "
                "effects may apply outside the current checkout."
            )
        try:
            self.progress(message)
        except (OSError, ValueError):
            pass

    def _run_traced_workload(
        self,
    ) -> tuple[
        Optional[dict[str, Any]], Optional[git_perf_trace.TraceParseResult]
    ]:
        status_parser = PorcelainV2StatusParser(
            byte_limit=STATUS_PARSE_BYTE_LIMIT,
            record_limit=STATUS_PARSE_RECORD_LIMIT,
        )
        trace_parser = git_perf_trace.Trace2EventParser()
        result = self._run(
            STATUS_WORKLOAD_ARGS,
            cutoff_ns=self.sampling_deadline_ns,
            effect_profile=NORMAL_GIT_EFFECT_PROFILE,
            output_limit_bytes=0,
            stdout_consumer=status_parser.consume,
            trace2_event_consumer=trace_parser.consume,
        )
        if result is None:
            return None, None

        status = status_parser.finish()
        if result.stdout_consumer_failed:
            status = _unavailable_status(status, "status_consumer_failed")
        elif result.stdout_read_failed:
            status = _unavailable_status(status, "stdout_read_failed")
        elif not result.ok:
            status = _unavailable_status(status, "workload_did_not_complete")

        if result.trace_consumer_failed:
            trace_parser.mark_transport_limit("trace_consumer_failed")
        if result.trace_read_failed:
            trace_parser.mark_transport_limit("trace_read_failed")
        if result.cleanup_uncertain:
            trace_parser.mark_transport_limit("capture_lifetime_forced_closed")
        parsed = trace_parser.finish()
        for reason in parsed.diagnostics:
            self._diagnose(
                f"trace.parse.{reason}",
                "warning",
                _trace_diagnostic_summary(reason),
            )

        execution = {
            "role": "traced_sample",
            "outcome": result.outcome,
            "returncode": result.returncode,
            "elapsed_ns": result.elapsed_ns,
            "valid_elapsed": (result.ok and not result.stdout_read_failed),
            "included_in_benchmark_summary": False,
            "stderr_state": result.stderr_state,
            "cleanup_uncertain": result.cleanup_uncertain,
            "cleanup_elapsed_ns": result.cleanup_elapsed_ns,
            "status": {
                "state": status.state,
                "counts": (
                    dataclasses.asdict(status.counts)
                    if status.counts is not None
                    else None
                ),
                "bytes_seen": status.bytes_seen,
                "records_seen": status.records_seen,
                "reason": status.reason,
            },
        }
        return execution, parsed

    def _build_trace_measurement(
        self,
        *,
        execution: Optional[dict[str, Any]],
        parsed: Optional[git_perf_trace.TraceParseResult],
        version_before: Optional[bytes],
        version_after: Optional[bytes],
        executable_before: Optional[tuple[int, int, int, int]],
        executable_after: Optional[tuple[int, int, int, int]],
        config_before: Optional[bytes],
        config_after: Optional[bytes],
    ) -> dict[str, Any]:
        client_stability = _snapshot_stability(
            (version_before, executable_before),
            (version_after, executable_after),
        )
        config_stability = _snapshot_stability(config_before, config_after)
        limitations = [
            "single_traced_execution",
            "trace_probe_effect_not_measured",
            "phase_attribution_is_not_a_latency_distribution",
        ]
        if self.deadline_limited:
            limitations.append("deadline_limited")
        if parsed is None or parsed.state != "complete":
            limitations.append("trace_integrity_limited")
        if execution is not None and execution["cleanup_uncertain"]:
            limitations.append("capture_or_process_cleanup_uncertain")
        for name, state in (
            ("selected_client", client_stability),
            ("workload_config", config_stability),
        ):
            if state != "observed_stable":
                limitations.append(f"{name}_{state}")
        return {
            "id": f"{STATUS_WORKLOAD_ID}.trace2",
            "workload_id": STATUS_WORKLOAD_ID,
            "workload_version": STATUS_WORKLOAD_VERSION,
            "effect_profile": NORMAL_GIT_EFFECT_PROFILE,
            "command": ["git", "--no-pager", *STATUS_WORKLOAD_ARGS],
            "semantic_inputs": {
                "untracked": "configured",
                "ahead_behind": "configured",
                "renames": "configured",
                "submodules": "configured",
                "optional_locks": self.optional_locks_mode,
                "fsmonitor": "configured",
                "untracked_cache": "configured",
            },
            "role": "traced_sample",
            "execution": execution,
            "trace_state": "failed" if parsed is None else parsed.state,
            "trace": None if parsed is None else parsed.trace,
            "capture_controls": {
                "transport": "numeric_fd_stream",
                "event_brief": False,
                "nesting_limit": 20,
                "byte_limit": git_perf_trace.DEFAULT_TRACE_BYTE_LIMIT,
                "deadline_seconds": self.deadline_seconds,
            },
            "quality": {
                "outcome_validity": (
                    "complete"
                    if execution is not None and execution["valid_elapsed"]
                    else "failed"
                ),
                "client_stability": client_stability,
                "config_stability": config_stability,
                "worktree_state_stability": (
                    "single_observation"
                    if execution is not None
                    and execution["status"]["state"] == "known"
                    else "unavailable"
                ),
                "semantic_stability": (
                    "observed_stable"
                    if client_stability == "observed_stable"
                    and config_stability == "observed_stable"
                    else "limited"
                ),
                "probe_effect": "unavailable",
                "external_validity": "limited",
                "limitations": limitations,
            },
        }

    def _report(
        self,
        *,
        status: str,
        shape: str,
        measurement: Optional[dict[str, Any]] = None,
    ) -> Report:
        report = Report(
            status=status,
            tool={
                "name": "git-perf",
                "implementation": "trace2-prototype",
                "launcher_provenance": "unknown",
                "operation": "trace",
            },
            subject=(
                {"kind": "none"}
                if shape == "none"
                else {"kind": "current_repository", "shape": shape}
            ),
            facts=self.facts,
            measurements=[] if measurement is None else [measurement],
            diagnostics=self.diagnostics,
            sources=[
                {
                    "id": "source.git-status",
                    "kind": "upstream_git",
                    "title": "Git status porcelain format",
                },
                {
                    "id": "source.git-trace2",
                    "kind": "upstream_git",
                    "title": "Git Trace2 API",
                },
            ],
        )
        report.canonicalize()
        return report


def collect_check(
    *,
    cwd: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    git_executable: str = "git",
    runner=None,
) -> Report:
    """Inspect selected repository metadata without running Git status."""
    cwd = os.path.abspath(os.getcwd() if cwd is None else cwd)
    environ = dict(os.environ if environ is None else environ)

    if gclient_utils.IsEnvCog():
        report = Report(
            status="failed",
            tool={
                "name": "git-perf",
                "implementation": "strict-check-prototype",
                "launcher_provenance": "unknown",
                "operation": "check",
            },
            subject={"kind": "none"},
            diagnostics=[
                Diagnostic(
                    "environment.non_git",
                    "error",
                    "This environment does not provide a live Git checkout to "
                    "inspect.",
                )
            ],
        )
        report.canonicalize()
        return report

    if runner is None:
        runner = GitRunner.resolve(git_executable, environ=environ)
    if runner is None:
        report = Report(
            status="failed",
            tool={
                "name": "git-perf",
                "implementation": "strict-check-prototype",
                "launcher_provenance": "unknown",
                "operation": "check",
            },
            subject={"kind": "none"},
            diagnostics=[
                Diagnostic(
                    "git.not_found",
                    "error",
                    "git-perf could not find a Git executable to inspect the "
                    "repository.",
                )
            ],
        )
        report.canonicalize()
        return report

    return _CheckCollector(runner, cwd=cwd, environ=environ).collect()


def collect_benchmark(
    *,
    cwd: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    git_executable: str = "git",
    samples: int = DEFAULT_BENCHMARK_SAMPLES,
    warmup: int = DEFAULT_BENCHMARK_WARMUP,
    deadline_seconds: int = DEFAULT_BENCHMARK_DEADLINE_SECONDS,
    runner=None,
    clock_ns: Callable[[], int] = time.monotonic_ns,
    progress: Optional[Callable[[str], None]] = None,
) -> Report:
    """Benchmark the fixed Git status command in the current worktree."""
    _validate_benchmark_controls(samples, warmup, deadline_seconds)
    cwd = os.path.abspath(os.getcwd() if cwd is None else cwd)
    environ = dict(os.environ if environ is None else environ)

    if gclient_utils.IsEnvCog():
        return _benchmark_unavailable_report(
            "environment.non_git",
            "This environment does not provide a live Git checkout to "
            "benchmark.",
        )

    if runner is None:
        runner = GitRunner.resolve(
            git_executable,
            environ=environ,
            clock_ns=clock_ns,
        )
    if runner is None:
        return _benchmark_unavailable_report(
            "git.not_found",
            "git-perf could not find a Git executable for the benchmark.",
        )

    return _BenchmarkCollector(
        runner,
        cwd=cwd,
        samples=samples,
        warmup=warmup,
        deadline_seconds=deadline_seconds,
        clock_ns=clock_ns,
        environ=environ,
        progress=progress,
    ).collect()


def collect_trace(
    *,
    cwd: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    git_executable: str = "git",
    deadline_seconds: int = DEFAULT_TRACE_DEADLINE_SECONDS,
    runner=None,
    clock_ns: Callable[[], int] = time.monotonic_ns,
    progress: Optional[Callable[[str], None]] = None,
    handshake: Optional[_TraceHandshake] = None,
) -> Report:
    """Capture Trace2 events from one Git status run."""
    _validate_deadline(deadline_seconds)
    cwd = os.path.abspath(os.getcwd() if cwd is None else cwd)
    environ = dict(os.environ if environ is None else environ)
    if gclient_utils.IsEnvCog():
        return _trace_unavailable_report(
            "environment.non_git",
            "This environment does not provide a live Git checkout to trace.",
        )
    if runner is None:
        runner = GitRunner.resolve(
            git_executable,
            environ=environ,
            clock_ns=clock_ns,
        )
    if runner is None:
        return _trace_unavailable_report(
            "git.not_found",
            "git-perf could not find a Git executable for the live trace.",
        )
    return _LiveTraceCollector(
        runner,
        cwd=cwd,
        deadline_seconds=deadline_seconds,
        clock_ns=clock_ns,
        environ=environ,
        progress=progress,
        handshake=handshake,
    ).collect()


def collect_trace_input(path: str, *, stdin=None) -> Report:
    """Summarize Trace2 EVENT input without retaining raw events."""
    if not isinstance(path, str) or not path or "\0" in path:
        raise ValueError("trace input must be a non-empty NUL-free path or '-'")
    parsed = git_perf_trace.parse_trace2_input(path, stdin=stdin)
    diagnostics = [
        Diagnostic(
            id=f"trace.input.{reason}",
            severity="error" if parsed.state == "failed" else "warning",
            summary=_trace_diagnostic_summary(reason),
        )
        for reason in parsed.diagnostics
    ]
    report = Report(
        status=parsed.state,
        tool={
            "name": "git-perf",
            "implementation": "trace2-prototype",
            "launcher_provenance": "unknown",
            "operation": "trace_input",
        },
        subject={"kind": "offline_trace"},
        measurements=[
            {
                "id": "trace2.offline_attribution",
                "role": "offline_trace",
                "trace_state": parsed.state,
                "trace": parsed.trace,
                "quality": {
                    "repository_context": "unavailable",
                    "selected_client_context": "unavailable",
                    "policy_actionability": "unavailable",
                    "limitations": [
                        "untrusted_offline_input",
                        "repository_context_not_joined",
                        "trace_probe_effect_unknown",
                    ],
                },
            }
        ],
        diagnostics=diagnostics,
        sources=[
            {
                "id": "source.git-trace2",
                "kind": "upstream_git",
                "title": "Git Trace2 API",
            }
        ],
    )
    report.canonicalize()
    return report


def collect_diagnose(
    *,
    cwd: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    git_executable: str = "git",
    samples: int = DEFAULT_BENCHMARK_SAMPLES,
    warmup: int = DEFAULT_BENCHMARK_WARMUP,
    deadline_seconds: int = DEFAULT_BENCHMARK_DEADLINE_SECONDS,
    runner=None,
    clock_ns: Callable[[], int] = time.monotonic_ns,
    progress: Optional[Callable[[str], None]] = None,
) -> Report:
    """Benchmark Git status and trace one additional run."""
    _validate_benchmark_controls(samples, warmup, deadline_seconds)
    cwd = os.path.abspath(os.getcwd() if cwd is None else cwd)
    environ = dict(os.environ if environ is None else environ)
    if gclient_utils.IsEnvCog():
        return _diagnose_unavailable_report(
            "environment.non_git",
            "This environment does not provide a live Git checkout to "
            "diagnose.",
        )
    if runner is None:
        runner = GitRunner.resolve(
            git_executable,
            environ=environ,
            clock_ns=clock_ns,
        )
    if runner is None:
        return _diagnose_unavailable_report(
            "git.not_found",
            "git-perf could not find a Git executable for the diagnosis.",
        )

    operation_start_ns = clock_ns()
    operation_deadline_ns = (
        operation_start_ns + deadline_seconds * 1_000_000_000
    )
    handshake_budget_ns = min(
        int(DEFAULT_TRACE_HANDSHAKE_TIMEOUT_SECONDS * 1_000_000_000),
        max(1, (operation_deadline_ns - operation_start_ns) // 10),
    )
    handshake_start_ns = clock_ns()
    handshake = _trace2_transport_handshake(
        runner,
        cutoff_ns=min(
            operation_deadline_ns, handshake_start_ns + handshake_budget_ns
        ),
        clock_ns=clock_ns,
    )
    handshake_elapsed_ns = max(0, clock_ns() - handshake_start_ns)

    if progress is not None:
        maximum_untraced = 1 + warmup + samples
        run_plan = (
            f"About to run Git status up to {maximum_untraced} "
            f"{_plural(maximum_untraced, 'time', 'times')} without tracing"
        )
        if handshake.usable:
            run_plan += ", then one separate traced run"
        run_plan += (
            f", all within {deadline_seconds}s and using the repository's "
            "current configuration."
        )
        if not handshake.usable:
            run_plan += " Trace2 is unavailable, so git-perf will skip it."
        try:
            progress(
                run_plan + "\nPossible effects: FSMonitor, "
                "hooks, and initialized submodules may run, and Git may "
                "update optional caches. A hook can make any change allowed "
                "to it.\n"
                "git-perf itself does not edit Git configuration."
                + (
                    "\nGit path overrides are set in the environment, so "
                    "these effects may apply outside the current checkout."
                    if any(
                        key in environ for key in _PATH_BEARING_GIT_VARIABLES
                    )
                    else ""
                )
            )
        except (OSError, ValueError):
            pass

    remaining_after_handshake_ns = max(0, operation_deadline_ns - clock_ns())
    context_reserve_ns = max(1, remaining_after_handshake_ns // 10)
    trace_reserve_ns = (
        max(1, remaining_after_handshake_ns // 4) if handshake.usable else 0
    )
    benchmark_deadline_ns = (
        operation_deadline_ns - trace_reserve_ns - context_reserve_ns
    )
    trace_deadline_ns = operation_deadline_ns - context_reserve_ns
    benchmark_start_ns = clock_ns()
    benchmark_budget_seconds = max(
        1,
        (max(0, benchmark_deadline_ns - benchmark_start_ns) + 999_999_999)
        // 1_000_000_000,
    )
    benchmark_report = _BenchmarkCollector(
        runner,
        cwd=cwd,
        samples=samples,
        warmup=warmup,
        deadline_seconds=benchmark_budget_seconds,
        clock_ns=clock_ns,
        environ=environ,
        progress=None,
        absolute_deadline_ns=benchmark_deadline_ns,
    ).collect()
    benchmark_elapsed_ns = max(0, clock_ns() - benchmark_start_ns)

    trace_start_ns = clock_ns()
    if not handshake.usable:
        trace_report = _diagnose_trace_skipped_report(
            benchmark_report.subject,
            "trace.transport_unavailable",
            "git-perf skipped the trace because this Git version did not "
            "produce a complete Trace2 test stream on this platform.",
            handshake,
        )
    elif benchmark_report.status == "failed":
        trace_report = _diagnose_trace_skipped_report(
            benchmark_report.subject,
            "diagnose.trace_skipped_after_benchmark_failure",
            "git-perf skipped the trace because the benchmark did not "
            "complete.",
            handshake,
        )
    elif trace_start_ns >= trace_deadline_ns:
        trace_report = _diagnose_trace_skipped_report(
            benchmark_report.subject,
            "diagnose.trace_deadline",
            "The deadline expired before the separate traced Git status run.",
            handshake,
        )
    else:
        trace_budget_seconds = max(
            1,
            (trace_deadline_ns - trace_start_ns + 999_999_999) // 1_000_000_000,
        )
        trace_report = _LiveTraceCollector(
            runner,
            cwd=cwd,
            deadline_seconds=trace_budget_seconds,
            clock_ns=clock_ns,
            environ=environ,
            progress=None,
            handshake=handshake,
            absolute_deadline_ns=trace_deadline_ns,
        ).collect()
    trace_elapsed_ns = max(0, clock_ns() - trace_start_ns)

    context_start_ns = clock_ns()
    context_runner = _DeadlineRunner(
        runner,
        deadline_ns=operation_deadline_ns,
        clock_ns=clock_ns,
    )
    context_report = _CheckCollector(
        context_runner,
        cwd=cwd,
        environ=environ,
    ).collect()
    context_elapsed_ns = max(0, clock_ns() - context_start_ns)
    context_subject_stable = context_report.subject == benchmark_report.subject
    if context_runner.deadline_reached:
        context_report.diagnostics.append(
            Diagnostic(
                "diagnose.context_deadline",
                "warning",
                "The deadline expired while git-perf was checking whether "
                "the repository changed during the diagnosis.",
            )
        )
    if not context_subject_stable:
        context_report.diagnostics.append(
            Diagnostic(
                "diagnose.subject_changed",
                "warning",
                "The repository changed during the diagnosis, so git-perf "
                "did not combine its results into one conclusion.",
            )
        )
    context_complete = (
        context_report.status == "complete"
        and not context_runner.deadline_reached
        and context_subject_stable
    )

    if benchmark_report.status == "failed":
        status = "failed"
    elif (
        benchmark_report.status == "complete"
        and trace_report.status == "complete"
        and context_complete
    ):
        status = "complete"
    else:
        status = "partial"

    phase_budget = {
        "operation_work_deadline_seconds": deadline_seconds,
        "elapsed_ns": max(0, clock_ns() - operation_start_ns),
        "phases": [
            {
                "id": "trace_handshake",
                "budget_ns": handshake_budget_ns,
                "elapsed_ns": handshake_elapsed_ns,
                "state": handshake.state,
            },
            {
                "id": "benchmark",
                "budget_ns": max(0, benchmark_deadline_ns - benchmark_start_ns),
                "elapsed_ns": benchmark_elapsed_ns,
                "state": benchmark_report.status,
            },
            {
                "id": "trace",
                "budget_ns": max(0, trace_deadline_ns - trace_start_ns),
                "minimum_reserved_ns": trace_reserve_ns,
                "elapsed_ns": trace_elapsed_ns,
                "state": trace_report.status,
            },
            {
                "id": "strict_context_postflight",
                "budget_ns": max(0, operation_deadline_ns - context_start_ns),
                "minimum_reserved_ns": context_reserve_ns,
                "elapsed_ns": context_elapsed_ns,
                "state": "complete" if context_complete else "partial",
            },
        ],
    }
    findings = [
        Finding(
            id="diagnose.no_persistent_action",
            kind="information",
            domain="policy",
            priority=90,
            summary=("Do not change Git configuration from this result alone."),
            blockers=(
                "The diagnosis contains one traced run.",
                "git-perf did not identify which settings are supported for "
                "this repository.",
            ),
        )
    ]
    report = Report(
        status=status,
        tool={
            "name": "git-perf",
            "implementation": "integrated-diagnose-prototype",
            "launcher_provenance": "unknown",
            "operation": "diagnose",
            "deadline": phase_budget,
        },
        subject=benchmark_report.subject,
        facts=_merge_diagnostic_facts(
            benchmark_report.facts,
            trace_report.facts,
            context_report.facts,
        ),
        findings=[*context_report.findings, *findings],
        measurements=[
            *benchmark_report.measurements,
            *trace_report.measurements,
        ],
        diagnostics=_merge_diagnostics(
            benchmark_report.diagnostics,
            trace_report.diagnostics,
            context_report.diagnostics,
        ),
        sources=_merge_sources(
            benchmark_report.sources,
            trace_report.sources,
            context_report.sources,
        ),
    )
    report.canonicalize()
    return report


def _validate_benchmark_controls(
    samples: int, warmup: int, deadline_seconds: int
) -> None:
    controls = (
        ("samples", samples, MIN_BENCHMARK_SAMPLES, MAX_BENCHMARK_SAMPLES),
        ("warmup", warmup, MIN_BENCHMARK_WARMUP, MAX_BENCHMARK_WARMUP),
        (
            "deadline_seconds",
            deadline_seconds,
            MIN_BENCHMARK_DEADLINE_SECONDS,
            MAX_BENCHMARK_DEADLINE_SECONDS,
        ),
    )
    for name, value, minimum, maximum in controls:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            raise ValueError(
                f"{name} must be an integer from {minimum} to {maximum}"
            )


def _validate_deadline(deadline_seconds: int) -> None:
    if (
        isinstance(deadline_seconds, bool)
        or not isinstance(deadline_seconds, int)
        or not MIN_BENCHMARK_DEADLINE_SECONDS
        <= deadline_seconds
        <= MAX_BENCHMARK_DEADLINE_SECONDS
    ):
        raise ValueError(
            "deadline_seconds must be an integer from "
            f"{MIN_BENCHMARK_DEADLINE_SECONDS} to "
            f"{MAX_BENCHMARK_DEADLINE_SECONDS}"
        )


def _benchmark_unavailable_report(diagnostic_id: str, summary: str) -> Report:
    report = Report(
        status="failed",
        tool={
            "name": "git-perf",
            "implementation": "benchmark-prototype",
            "launcher_provenance": "unknown",
            "operation": "bench",
        },
        subject={"kind": "none"},
        diagnostics=[Diagnostic(diagnostic_id, "error", summary)],
    )
    report.canonicalize()
    return report


def _trace_unavailable_report(diagnostic_id: str, summary: str) -> Report:
    report = Report(
        status="failed",
        tool={
            "name": "git-perf",
            "implementation": "trace2-prototype",
            "launcher_provenance": "unknown",
            "operation": "trace",
        },
        subject={"kind": "none"},
        diagnostics=[Diagnostic(diagnostic_id, "error", summary)],
    )
    report.canonicalize()
    return report


def _diagnose_unavailable_report(diagnostic_id: str, summary: str) -> Report:
    report = Report(
        status="failed",
        tool={
            "name": "git-perf",
            "implementation": "integrated-diagnose-prototype",
            "launcher_provenance": "unknown",
            "operation": "diagnose",
        },
        subject={"kind": "none"},
        diagnostics=[Diagnostic(diagnostic_id, "error", summary)],
    )
    report.canonicalize()
    return report


def _diagnose_trace_skipped_report(
    subject: Mapping[str, Any],
    diagnostic_id: str,
    summary: str,
    handshake: _TraceHandshake,
) -> Report:
    report = Report(
        status="partial",
        tool={
            "name": "git-perf",
            "implementation": "trace2-prototype",
            "launcher_provenance": "unknown",
            "operation": "trace",
        },
        subject=dict(subject),
        facts=[
            Fact(
                id="trace2.transport",
                domain="trace",
                state="known" if handshake.usable else "unsupported",
                value="numeric_fd_stream" if handshake.usable else None,
                provenance="selected_git_runtime_handshake",
                limitations=() if handshake.usable else handshake.diagnostics,
            )
        ],
        diagnostics=[Diagnostic(diagnostic_id, "warning", summary)],
        sources=[
            {
                "id": "source.git-trace2",
                "kind": "upstream_git",
                "title": "Git Trace2 API",
            }
        ],
    )
    report.canonicalize()
    return report


def _merge_diagnostic_facts(*collections: Sequence[Fact]) -> list[Fact]:
    merged: dict[str, Fact] = {}
    for fact in (fact for collection in collections for fact in collection):
        previous = merged.get(fact.id)
        if previous is None or previous == fact:
            merged[fact.id] = fact
            continue
        if (
            previous.domain == fact.domain
            and previous.state == fact.state
            and previous.value == fact.value
            and previous.applicability == fact.applicability
        ):
            merged[fact.id] = Fact(
                id=fact.id,
                domain=fact.domain,
                state=fact.state,
                value=fact.value,
                provenance=(
                    previous.provenance
                    if previous.provenance == fact.provenance
                    else "multiple_diagnostic_phases"
                ),
                applicability=fact.applicability,
                limitations=tuple(
                    sorted({*previous.limitations, *fact.limitations})
                ),
            )
            continue
        merged[fact.id] = Fact(
            id=fact.id,
            domain=previous.domain,
            state="unavailable",
            value=None,
            provenance="diagnostic_phase_comparison",
            limitations=("changed_between_diagnostic_phases",),
        )
    return list(merged.values())


def _merge_diagnostics(
    *collections: Sequence[Diagnostic],
) -> list[Diagnostic]:
    merged: dict[str, Diagnostic] = {}
    for diagnostic in (
        diagnostic for collection in collections for diagnostic in collection
    ):
        merged.setdefault(diagnostic.id, diagnostic)
    return list(merged.values())


def _merge_sources(
    *collections: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for source in (
        source for collection in collections for source in collection
    ):
        merged.setdefault(source["id"], dict(source))
    return list(merged.values())


def _unavailable_status(
    parsed: StatusParseResult, reason: str
) -> StatusParseResult:
    return StatusParseResult(
        state="unavailable",
        counts=None,
        bytes_seen=parsed.bytes_seen,
        records_seen=parsed.records_seen,
        reason=reason,
    )


_TRACE_DIAGNOSTIC_SUMMARIES = {
    "input_not_regular": "Trace input must be a regular file or binary stdin.",
    "input_changed_before_open": (
        "The trace file changed between the initial check and opening it."
    ),
    "input_changed_during_read": "Trace input changed while it was being read.",
    "input_not_found": "Trace input was not found.",
    "input_open_failed": "git-perf could not open the trace input.",
    "input_read_failed": "Trace input could not be read completely.",
    "input_is_not_binary": "Trace stdin must provide bytes.",
    "byte_limit_exceeded": "Trace input exceeded the parser's byte limit.",
    "line_limit_exceeded": "A Trace2 event exceeded the parser's line limit.",
    "event_limit_exceeded": "Trace input exceeded the parser's event limit.",
    "session_limit_exceeded": "Trace input exceeded the parser's session limit.",
    "thread_limit_exceeded": "Trace input exceeded the parser's thread limit.",
    "diagnostic_limit_exceeded": (
        "The report omitted additional Trace2 errors after reaching its "
        "diagnostic limit."
    ),
    "malformed_json_event": "Trace input contains malformed UTF-8 or JSON events.",
    "event_is_not_an_object": "Trace input contains a JSON value that is not a Trace2 event object.",
    "missing_or_oversized_event_identity": "A Trace2 event has a missing or oversized identity field.",
    "truncated_line": "Trace input ended with an incomplete event.",
    "empty_line": "Trace input contains an empty event line.",
    "unmatched_region_enter": "One or more Trace2 regions did not have a matching leave event.",
    "unmatched_region_leave": "A Trace2 region leave did not have a matching enter event.",
    "malformed_region_enter": "A Trace2 region-enter event was malformed.",
    "region_nesting_disagrees": "Trace2 region nesting was inconsistent.",
    "region_leave_disagrees": "A Trace2 region leave did not match its active region.",
    "region_clock_regression": "A Trace2 region ended before it began.",
    "child_region_outside_parent": "A nested Trace2 region extended outside its parent interval.",
    "region_outside_trace_window": (
        "A Trace2 region extended beyond its session's start or end."
    ),
    "start_time_unavailable": "A Trace2 session had no usable start time.",
    "atexit_time_unavailable": "A Trace2 session had no usable end time.",
    "atexit_anchor_disagrees": "Trace2 session wall and relative clocks disagreed.",
    "negative_trace_window": "A Trace2 session reported a negative time window.",
    "duplicate_start_disagrees": "Duplicate Trace2 session starts disagreed.",
    "event_format_version_unavailable": "The Trace2 EVENT format version was unavailable.",
    "trace_consumer_failed": "The Trace2 parser stopped accepting events.",
    "trace_read_failed": "git-perf could not read the complete Trace2 stream.",
    "capture_lifetime_forced_closed": (
        "git-perf stopped Trace2 capture because a writer remained open after "
        "Git status ended."
    ),
    "trace_transport_unavailable": (
        "This Git and platform did not provide the Trace2 stream that git-perf "
        "expects."
    ),
    "handshake_git_failed": "Git failed during the Trace2 setup test.",
    "handshake_envelope_missing": (
        "The Trace2 setup test did not contain its expected start and end "
        "events."
    ),
    "handshake_cleanup_uncertain": (
        "A writer remained open after the Trace2 setup test ended."
    ),
    "handshake_deadline": "The deadline expired during the Trace2 setup test.",
}


def _trace_diagnostic_summary(reason: str) -> str:
    return _TRACE_DIAGNOSTIC_SUMMARIES.get(
        reason,
        "The Trace2 stream has a problem that git-perf does not recognize.",
    )


def _regular_file_identity(path: str) -> Optional[tuple[int, int]]:
    if not isinstance(path, str) or not path or "\0" in path:
        return None
    try:
        current = os.stat(os.path.abspath(path))
    except OSError:
        return None
    if not stat.S_ISREG(current.st_mode):
        return None
    return current.st_dev, current.st_ino


def _selected_executable_identity(
    runner,
) -> Optional[tuple[int, int, int, int]]:
    executable = getattr(runner, "executable", None)
    if not isinstance(executable, str) or not os.path.isabs(executable):
        return None
    try:
        current = os.stat(executable)
    except OSError:
        return None
    if not stat.S_ISREG(current.st_mode):
        return None
    return (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
    )


def _snapshot_stability(before, after) -> str:

    def missing(value) -> bool:
        if value is None:
            return True
        if isinstance(value, tuple):
            return any(missing(item) for item in value)
        return False

    if missing(before) or missing(after):
        return "unavailable"
    return "observed_stable" if before == after else "observed_changed"


def read_index_header(
    path: str,
) -> tuple[str, Optional[dict[str, Any]], tuple[str, ...]]:
    """Read a bounded Git index header without locking or repairing it."""
    initial = None
    try:
        initial = os.lstat(path)
        if not stat.S_ISREG(initial.st_mode):
            return "unavailable", None, ("index_is_not_a_regular_file",)
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as index_file:
            before = os.fstat(index_file.fileno())
            if not stat.S_ISREG(before.st_mode):
                return "unavailable", None, ("index_is_not_a_regular_file",)
            header = index_file.read(12)
            after = os.fstat(index_file.fileno())
        try:
            final = os.lstat(path)
        except OSError:
            return "unavailable", None, ("index_changed_during_read",)
    except FileNotFoundError:
        if initial is not None:
            return "unavailable", None, ("index_changed_during_read",)
        return "unset", {"present": False}, ()
    except OSError:
        return "unavailable", None, ("index_could_not_be_opened",)

    identity_initial = (
        initial.st_dev,
        initial.st_ino,
        initial.st_size,
        initial.st_mtime_ns,
    )
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    identity_final = (
        final.st_dev,
        final.st_ino,
        final.st_size,
        final.st_mtime_ns,
    )
    if (
        identity_initial != identity_before
        or identity_before != identity_after
        or identity_after != identity_final
    ):
        return "unavailable", None, ("index_changed_during_read",)
    if len(header) != 12 or header[:4] != b"DIRC":
        return "unavailable", None, ("index_header_unrecognized",)

    version = int.from_bytes(header[4:8], "big")
    if version not in (2, 3, 4):
        return (
            "unsupported",
            {"present": True},
            ("index_format_version_unsupported",),
        )
    return (
        "known",
        {
            "present": True,
            "format_version": version,
            "entry_count": int.from_bytes(header[8:12], "big"),
            "size_bytes": before.st_size,
        },
        (
            "header_entry_count_may_not_equal_tracked_files_for_split_or_sparse_indexes",
        ),
    )


def report_json(report: Report) -> str:
    """Render a deterministic JSON document."""
    return json.dumps(
        report.to_dict(),
        ensure_ascii=False,
        indent=2,
        separators=(",", ": "),
    )


def render_human(
    report: Report, *, verbose: bool = False, width: int = 100
) -> str:
    """Render a useful path-free report without initiating new probes."""
    operation = report.tool.get("operation")
    if operation == "bench":
        return _render_benchmark_human(report, verbose=verbose, width=width)
    if operation in ("trace", "trace_input"):
        return _render_trace_human(report, verbose=verbose, width=width)
    if operation == "diagnose":
        return _render_diagnose_human(report, verbose=verbose, width=width)

    width = max(40, width)
    findings = sorted(
        report.findings, key=lambda finding: (finding.priority, finding.id)
    )
    diagnostics = sorted(
        report.diagnostics, key=lambda diagnostic: diagnostic.id
    )
    facts = sorted(report.facts, key=lambda fact: fact.id)
    review_findings = [
        finding for finding in findings if finding.kind in ("error", "warning")
    ]
    action_findings = [
        finding for finding in findings if finding.kind == "action"
    ]
    hidden_finding_ids = {
        "git.selected_client_identified",
        "policy.no_causal_action",
    }
    note_findings = [
        finding
        for finding in findings
        if finding.kind in ("confirmation", "information")
        and finding.id not in hidden_finding_ids
    ]
    action_count = len(action_findings)
    lines = ["Git performance check", "", "Result"]
    if report.status == "failed":
        _append_wrapped(
            lines, "  ", "Could not inspect this repository.", width
        )
    else:
        if review_findings and action_count:
            review_count = len(review_findings)
            result = (
                f"{review_count} {_plural(review_count, 'item', 'items')} need "
                f"review, and the check found {action_count} possible "
                f"{_plural(action_count, 'change', 'changes')}."
            )
        elif review_findings:
            count = len(review_findings)
            result = f"{count} {_plural(count, 'item', 'items')} need review."
        elif action_count:
            result = (
                f"The check found {action_count} possible "
                f"{_plural(action_count, 'change', 'changes')}."
            )
        else:
            result = "Nothing in the collected setup matched a warning."
        _append_wrapped(lines, "  ", result, width)
        if report.status == "partial":
            _append_wrapped(
                lines,
                "  ",
                "Some setup details were unavailable, so this result is "
                "incomplete.",
                width,
            )
        _append_wrapped(
            lines,
            "  ",
            (
                "The check reads repository metadata and selected settings. "
                "It does not time Git commands or edit Git configuration."
            ),
            width,
        )

    if review_findings or action_findings:
        lines.extend(["", "Review"])
        labels = {
            "error": "ERROR",
            "warning": "WARN",
            "action": "ACTION",
        }
        for finding in [*review_findings, *action_findings]:
            _append_wrapped(
                lines,
                f"  {labels[finding.kind]:<6} ",
                finding.summary,
                width,
            )
            if finding.blockers and (
                verbose or finding.kind in ("warning", "action")
            ):
                _append_wrapped(
                    lines,
                    "         Why no recommendation: ",
                    " ".join(finding.blockers),
                    width,
                )

    if diagnostics:
        lines.extend(
            [
                "",
                "Could not finish"
                if report.status == "failed"
                else "Incomplete checks",
            ]
        )
        for diagnostic in diagnostics:
            _append_wrapped(
                lines,
                "  ",
                diagnostic.summary,
                width,
            )

    overview = _repository_overview(report)
    if overview:
        lines.extend(["", "Repository"])
        for item in overview:
            _append_wrapped(lines, "  ", item, width)

    if note_findings:
        lines.extend(["", "Notes"])
        for finding in note_findings:
            _append_wrapped(lines, "  ", finding.summary, width)

    if verbose and facts:
        lines.extend(["", "Collected details"])
        for fact in facts:
            _append_wrapped(
                lines,
                f"  {_human_fact_name(fact.id)}: ",
                _technical_fact_value(fact),
                width,
            )
            if fact.applicability != "applicable":
                _append_wrapped(
                    lines,
                    "    Applies: ",
                    _human_identifier(fact.applicability),
                    width,
                )
            if fact.limitations:
                _append_wrapped(
                    lines,
                    "    Limits: ",
                    " ".join(
                        _human_limitation(item) for item in fact.limitations
                    ),
                    width,
                )

    if report.status != "failed":
        lines.extend(["", "Next step"])
        _append_wrapped(
            lines,
            "  ",
            (
                "If Git status is slow, run `git perf diagnose`. It times "
                "several status runs and traces one additional run. Before "
                "starting, it prints the side effects that the current Git "
                "configuration may allow."
            ),
            width,
        )

    if not verbose and report.status != "failed":
        lines.extend(
            [
                "",
                "Run with --verbose to see the facts behind this result.",
            ]
        )

    return "\n".join(lines) + "\n"


def _render_benchmark_human(
    report: Report, *, verbose: bool, width: int
) -> str:
    width = max(40, width)
    diagnostics = sorted(
        report.diagnostics, key=lambda diagnostic: diagnostic.id
    )
    facts = sorted(report.facts, key=lambda fact: fact.id)
    lines = ["Git performance benchmark", "", "Result"]
    measurement = report.measurements[0] if report.measurements else None
    if measurement is None:
        _append_wrapped(
            lines,
            "  ",
            "The benchmark did not run Git status.",
            width,
        )
    else:
        summary = measurement["summary"]
        executions = measurement["executions"]
        first = executions[0] if executions else None
        if summary is None:
            _append_wrapped(
                lines,
                "  ",
                (
                    "git-perf recorded the individual runs but did not "
                    "calculate a median. A run failed, or Git, its settings, "
                    "or the worktree changed during the benchmark."
                ),
                width,
            )
        else:
            result = (
                f"Git status median: "
                f"{_format_duration_ns(summary['median_ns'])} from "
                f"{summary['valid_n']} measured "
                f"{_plural(summary['valid_n'], 'run', 'runs')}."
            )
            _append_wrapped(lines, "  ", result, width)
            spread = summary["spread"]
            if spread["kind"] == "raw_mad":
                _append_wrapped(
                    lines,
                    "  ",
                    (
                        "Median absolute deviation between runs: "
                        f"{_format_duration_ns(spread['value_ns'])}."
                    ),
                    width,
                )
        if first is not None:
            _append_wrapped(
                lines,
                "  ",
                (f"First status run after setup: {_format_execution(first)}."),
                width,
            )
        _append_wrapped(
            lines,
            "  ",
            (
                "These timings apply to this checkout on this machine during "
                "this benchmark."
            ),
            width,
        )

        measured = [
            execution
            for execution in executions
            if execution["role"] == "measured"
        ]
        if measured:
            lines.extend(["", "Measured runs"])
            _append_wrapped(
                lines,
                "  ",
                "  ".join(_format_execution(item) for item in measured),
                width,
            )

        lines.extend(["", "Effects"])
        _append_wrapped(
            lines,
            "  ",
            (
                "git-perf did not edit Git configuration. It ran Git status "
                "with the existing settings, so Git may have updated optional "
                "caches, used FSMonitor, or inspected initialized submodules. A "
                "configured hook could have made other changes."
            ),
            width,
        )

        quality = measurement["quality"]
        limitations = quality["limitations"]
        if limitations:
            lines.extend(["", "Limits"])
            shown = limitations if verbose else limitations[:4]
            for limitation in shown:
                _append_wrapped(
                    lines,
                    "  ",
                    _human_limitation(limitation),
                    width,
                )
            if not verbose and len(shown) < len(limitations):
                _append_wrapped(
                    lines,
                    "  ",
                    "Run with --verbose to see all quality limitations.",
                    width,
                )

        if verbose:
            lines.extend(["", "Execution details"])
            for execution in executions:
                status_state = execution["status"]["state"]
                status_note = (
                    "status output read successfully"
                    if status_state in ("complete", "known")
                    else "status output " + _human_identifier(status_state)
                )
                detail = (
                    f"{execution['order']}. "
                    f"{_human_identifier(execution['role'])}: "
                    f"{_format_execution(execution)}; "
                    f"{status_note}."
                )
                _append_wrapped(lines, "  ", detail, width)

    if diagnostics:
        heading = "Could not finish" if report.status == "failed" else "Notes"
        lines.extend(["", heading])
        for diagnostic in diagnostics:
            _append_wrapped(lines, "  ", diagnostic.summary, width)

    if report.status != "failed":
        lines.extend(["", "Next step"])
        _append_wrapped(
            lines,
            "  ",
            (
                "To see where one status run spends time, run `git perf "
                "trace`. Use `git perf diagnose` to collect both benchmark "
                "timings and a separate trace."
            ),
            width,
        )

    if verbose and facts:
        lines.extend(["", "Collected details"])
        for fact in facts:
            _append_wrapped(
                lines,
                f"  {_human_fact_name(fact.id)}: ",
                _technical_fact_value(fact),
                width,
            )

    return "\n".join(lines) + "\n"


def _render_trace_human(report: Report, *, verbose: bool, width: int) -> str:
    width = max(40, width)
    diagnostics = sorted(
        report.diagnostics, key=lambda diagnostic: diagnostic.id
    )
    facts = sorted(report.facts, key=lambda fact: fact.id)
    offline = report.tool.get("operation") == "trace_input"
    title = "Git Trace2 input" if offline else "Git performance trace"
    lines = [title, "", "Result"]
    measurement = report.measurements[0] if report.measurements else None
    trace = measurement.get("trace") if measurement is not None else None
    execution = (
        measurement.get("execution") if measurement is not None else None
    )
    if trace is None:
        _append_wrapped(
            lines,
            "  ",
            "git-perf could not produce a usable Trace2 summary.",
            width,
        )
    else:
        events = trace.get("events", {})
        regions = trace.get("regions", {})
        _append_wrapped(
            lines,
            "  ",
            (
                f"Read {events.get('total', 0):,} Trace2 "
                f"{_plural(events.get('total', 0), 'event', 'events')} "
                f"from {trace.get('sessions', 0):,} "
                f"{_plural(trace.get('sessions', 0), 'session', 'sessions')}; "
                f"matched {regions.get('matched', 0):,} timed "
                f"{_plural(regions.get('matched', 0), 'region', 'regions')}."
            ),
            width,
        )
        if execution is not None:
            _append_wrapped(
                lines,
                "  ",
                (
                    "Traced Git status time: "
                    f"{_format_execution(execution)}. This run shows where "
                    "Trace2 recorded time, but not whether the timings are "
                    "repeatable."
                ),
                width,
            )
        timeline = trace.get("timeline", {})
        coverage = timeline.get("coverage_fraction")
        trace_window_ns = timeline.get("trace_window_ns")
        if coverage is not None and trace_window_ns is not None:
            _append_wrapped(
                lines,
                "  ",
                (
                    f"Matched regions cover {coverage * 100:.1f}% of the "
                    f"{_format_duration_ns(trace_window_ns)} trace window."
                ),
                width,
            )
        _append_trace_phases(
            lines,
            trace.get("phases", []),
            verbose=verbose,
            width=width,
        )

    if not offline and measurement is not None:
        lines.extend(["", "Effects"])
        _append_wrapped(
            lines,
            "  ",
            (
                "git-perf did not edit Git configuration. It ran Git status "
                "with the existing settings, so FSMonitor, hooks, and "
                "initialized submodules may have run. Git may also have "
                "updated optional caches, and a hook could have made other "
                "changes."
            ),
            width,
        )

    if diagnostics:
        lines.extend(
            [
                "",
                "Could not finish" if report.status == "failed" else "Notes",
            ]
        )
        for diagnostic in diagnostics:
            _append_wrapped(lines, "  ", diagnostic.summary, width)

    lines.extend(["", "How to read this result"])
    _append_wrapped(
        lines,
        "  ",
        (
            "The trace does not establish which repository, Git executable, "
            "or configuration produced it. Treat the phase timings as clues "
            "only."
            if offline
            else "Trace2 adds some overhead, which git-perf did not measure. "
            "One run also cannot show whether a slow phase is repeatable."
        ),
        width,
    )
    lines.extend(["", "Next step"])
    _append_wrapped(
        lines,
        "  ",
        (
            "Run `git perf diagnose` in the affected repository to collect "
            "benchmark timings and a fresh trace."
            if offline
            else "Run `git perf diagnose` to compare several untraced timings "
            "with one separate traced run."
        ),
        width,
    )
    if verbose and facts:
        lines.extend(["", "Collected details"])
        for fact in facts:
            _append_wrapped(
                lines,
                f"  {_human_fact_name(fact.id)}: ",
                _technical_fact_value(fact),
                width,
            )
    return "\n".join(lines) + "\n"


def _render_diagnose_human(report: Report, *, verbose: bool, width: int) -> str:
    width = max(40, width)
    diagnostics = sorted(
        report.diagnostics, key=lambda diagnostic: diagnostic.id
    )
    facts = sorted(report.facts, key=lambda fact: fact.id)
    lines = ["Git performance diagnosis", "", "Result"]
    benchmark = next(
        (
            item
            for item in report.measurements
            if item.get("id") == STATUS_WORKLOAD_ID
        ),
        None,
    )
    trace_measurement = next(
        (
            item
            for item in report.measurements
            if item.get("id") == f"{STATUS_WORKLOAD_ID}.trace2"
        ),
        None,
    )
    if benchmark is None:
        _append_wrapped(
            lines, "  ", "The benchmark did not run Git status.", width
        )
    elif benchmark.get("summary") is None:
        _append_wrapped(
            lines,
            "  ",
            "git-perf recorded the individual runs but did not calculate a "
            "median. A run failed, or Git, its settings, or the worktree "
            "changed during the diagnosis.",
            width,
        )
    else:
        summary = benchmark["summary"]
        _append_wrapped(
            lines,
            "  ",
            (
                "Git status median: "
                f"{_format_duration_ns(summary['median_ns'])} from "
                f"{summary['valid_n']} measured "
                f"{_plural(summary['valid_n'], 'run', 'runs')}."
            ),
            width,
        )

    trace = (
        trace_measurement.get("trace")
        if trace_measurement is not None
        else None
    )
    if trace is None:
        _append_wrapped(
            lines,
            "  ",
            "The separate Trace2 run did not produce a usable summary.",
            width,
        )
    else:
        execution = trace_measurement.get("execution")
        if execution is not None:
            _append_wrapped(
                lines,
                "  ",
                (
                    "Separate traced status run: "
                    f"{_format_execution(execution)}. This time is not "
                    "included in the benchmark median."
                ),
                width,
            )
        _append_trace_phases(
            lines,
            trace.get("phases", []),
            verbose=verbose,
            width=width,
        )

    context_phase = next(
        (
            phase
            for phase in report.tool.get("deadline", {}).get("phases", [])
            if phase.get("id") == "strict_context_postflight"
        ),
        None,
    )
    if context_phase is not None:
        _append_wrapped(
            lines,
            "  ",
            (
                "git-perf checked the repository and Git executable again "
                "after the runs."
                if context_phase.get("state") == "complete"
                else "git-perf could not finish checking whether the "
                "repository or Git executable changed during the runs."
            ),
            width,
        )

    lines.extend(["", "How to read this result"])
    _append_wrapped(
        lines,
        "  ",
        (
            "Use the untraced runs for timing. The trace shows where one "
            "additional run recorded time. git-perf did not measure Trace2's "
            "overhead or repeat the diagnosis in another session."
        ),
        width,
    )
    lines.extend(["", "Effects"])
    _append_wrapped(
        lines,
        "  ",
        (
            "git-perf did not edit Git configuration. It ran Git status with "
            "the existing settings, so FSMonitor, hooks, and initialized "
            "submodules may have run. Git may also have updated optional "
            "caches, and a hook could have made other changes."
        ),
        width,
    )
    if diagnostics:
        lines.extend(
            [
                "",
                "Could not finish" if report.status == "failed" else "Notes",
            ]
        )
        for diagnostic in diagnostics:
            _append_wrapped(lines, "  ", diagnostic.summary, width)

    lines.extend(["", "Assessment"])
    phases = trace.get("phases", []) if trace is not None else []
    ranked = sorted(
        phases,
        key=lambda phase: phase.get("active_wall_ns", 0),
        reverse=True,
    )
    if ranked:
        largest = ranked[0]
        _append_wrapped(
            lines,
            "  ",
            (
                "The phase with the most recorded active time in this trace "
                f"was {_human_identifier(largest['id'])} "
                f"({_format_duration_ns(largest['active_wall_ns'])}). Repeat "
                "the test under controlled conditions before treating that "
                "phase as the cause."
            ),
            width,
        )
    else:
        _append_wrapped(
            lines,
            "  ",
            "The trace did not identify a phase to investigate.",
            width,
        )
    _append_wrapped(
        lines,
        "  ",
        "Do not change Git configuration from this result alone. The "
        "diagnosis contains only one trace, and git-perf did not identify "
        "which settings are supported for this repository.",
        width,
    )

    if verbose:
        deadline = report.tool.get("deadline")
        if deadline:
            lines.extend(["", "Phase budgets"])
            for phase in deadline.get("phases", []):
                _append_wrapped(
                    lines,
                    "  ",
                    (
                        f"{_human_identifier(phase['id'])}: "
                        f"{_human_identifier(phase['state'])}; took "
                        f"{_format_duration_ns(phase['elapsed_ns'])} of the "
                        f"{_format_duration_ns(phase['budget_ns'])} allowed."
                    ),
                    width,
                )
        if facts:
            lines.extend(["", "Collected details"])
            for fact in facts:
                _append_wrapped(
                    lines,
                    f"  {_human_fact_name(fact.id)}: ",
                    _technical_fact_value(fact),
                    width,
                )
    return "\n".join(lines) + "\n"


def _append_trace_phases(
    lines: list[str],
    phases: Sequence[Mapping[str, Any]],
    *,
    verbose: bool,
    width: int,
) -> None:
    if not phases:
        return
    ranked = sorted(
        phases,
        key=lambda phase: phase.get("active_wall_ns", 0),
        reverse=True,
    )
    shown = ranked if verbose else ranked[:8]
    lines.extend(["", "Trace phases"])
    if verbose:
        _append_wrapped(
            lines,
            "  ",
            "Active time counts overlapping occurrences of the same phase "
            "once. The other values show time with and without nested "
            "regions.",
            width,
        )
    for phase in shown:
        if verbose:
            detail = (
                f"{_human_identifier(phase['id'])}: "
                f"{_format_duration_ns(phase['active_wall_ns'])} active; "
                f"{_format_duration_ns(phase['inclusive_ns'])} including "
                f"nested regions; "
                f"{_format_duration_ns(phase['exclusive_ns'])} excluding "
                f"nested regions; {phase['count']} "
                f"{_plural(phase['count'], 'region', 'regions')}."
            )
        else:
            detail = (
                f"{_human_identifier(phase['id'])}: "
                f"{_format_duration_ns(phase['active_wall_ns'])} across "
                f"{phase['count']} "
                f"{_plural(phase['count'], 'region', 'regions')}."
            )
        _append_wrapped(
            lines,
            "  ",
            detail,
            width,
        )
    if len(shown) < len(ranked):
        _append_wrapped(
            lines,
            "  ",
            "Run with --verbose to see every phase and its nested-region "
            "timings.",
            width,
        )


def _format_execution(execution: Mapping[str, Any]) -> str:
    if execution["valid_elapsed"]:
        return _format_duration_ns(execution["elapsed_ns"])
    return _human_identifier(execution["outcome"])


def _format_duration_ns(value: int | float) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.3f} s"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.3f} ms"
    if value >= 1_000:
        return f"{value / 1_000:.3f} us"
    return f"{value:g} ns"


def _fixed_lines(result: GitResult, expected: int) -> Optional[list[bytes]]:
    if (
        not result.ok
        or result.stdout_truncated
        or not result.stdout.endswith(b"\n")
    ):
        return None
    lines = result.stdout[:-1].split(b"\n")
    return lines if len(lines) == expected else None


def _single_line(result: GitResult) -> Optional[bytes]:
    lines = _fixed_lines(result, 1)
    return lines[0] if lines is not None else None


def _parse_git_bool(value: bytes) -> Optional[bool]:
    normalized = value.strip().lower()
    if normalized in (b"true", b"yes", b"on", b"1"):
        return True
    if normalized in (b"false", b"no", b"off", b"0", b""):
        return False
    return None


def _single_git_bool(result: GitResult) -> Optional[bool]:
    line = _single_line(result)
    return _parse_git_bool(line) if line is not None else None


def _path_from_git_output(value: Optional[bytes], cwd: str) -> Optional[str]:
    if value is None or b"\0" in value or b"\n" in value or b"\r" in value:
        return None
    path = os.fsdecode(value)
    if not path:
        return None
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    return os.path.normpath(path)


def _same_path(left: str, right: str) -> bool:
    return os.path.normcase(os.path.realpath(left)) == os.path.normcase(
        os.path.realpath(right)
    )


def _count_worktree_records(data: bytes, *, nul_delimited: bool) -> int:
    separator = b"\0" if nul_delimited else b"\n"
    return sum(
        record.startswith(b"worktree ") for record in data.split(separator)
    )


def _config_value_category(value: bytes) -> str:
    # `git config --get` returns zero bytes for both a valueless true setting
    # and an explicitly empty false setting. A later probe must disambiguate it.
    if value == b"":
        return "empty_or_implicit_boolean"
    boolean = _parse_git_bool(value)
    if boolean is True:
        return "boolean_true"
    if boolean is False:
        return "disabled"
    return "external_hook"


def _origin_category(origin: bytes) -> str:
    lowered = origin.lower()
    if lowered.startswith(b"command line:"):
        return "command"
    if lowered.startswith(b"file:"):
        return "file"
    if lowered.startswith(b"standard input:"):
        return "standard_input"
    if lowered.startswith(b"blob:"):
        return "blob"
    return "unknown"


def _parse_scoped_config_assignments(
    result: GitResult,
) -> Optional[list[dict[str, str]]]:
    if result.returncode == 1 and not result.stdout:
        return []
    if not result.ok or result.stdout_truncated:
        return None
    parts = result.stdout.split(b"\0")
    if not parts or parts[-1] != b"" or (len(parts) - 1) % 3:
        return None
    assignments = []
    for index in range(0, len(parts) - 1, 3):
        scope, origin, value = parts[index : index + 3]
        assignments.append(
            {
                "scope": (
                    scope.decode("ascii")
                    if scope in _KNOWN_CONFIG_SCOPES
                    else "unknown"
                ),
                "origin_category": _origin_category(origin),
                "value_category": _config_value_category(value),
            }
        )
    return assignments


def _parse_unscoped_config_values(result: GitResult) -> Optional[list[str]]:
    if result.returncode == 1 and not result.stdout:
        return []
    if not result.ok or result.stdout_truncated:
        return None
    parts = result.stdout.split(b"\0")
    if not parts or parts[-1] != b"":
        return None
    return [_config_value_category(value) for value in parts[:-1]]


def _status_fsmonitor_mode(config: Optional[bytes]) -> str:
    """Reduce the status-config snapshot to a non-path FSMonitor mode."""
    if config is None:
        return "unknown"
    values = []
    entries = config.split(b"\0")
    if not entries or entries[-1] != b"":
        return "unknown"
    for entry in entries[:-1]:
        try:
            key, value = entry.split(b"\n", 1)
        except ValueError:
            return "unknown"
        if key.lower() == b"core.fsmonitor":
            values.append(value)
    if not values:
        return "unset"
    value = values[-1]
    if not value:
        return "ambiguous"
    boolean = _parse_git_bool(value)
    if boolean is True:
        return "built_in"
    if boolean is False:
        return "disabled"
    return "external_hook"


def _environment_git_bool_mode(environ: Mapping[str, str], key: str) -> str:
    if key not in environ:
        return "default"
    try:
        value = os.fsencode(environ[key])
    except (TypeError, ValueError):
        return "unknown"
    parsed = _parse_git_bool(value)
    if parsed is True:
        return "enabled"
    if parsed is False:
        return "disabled"
    return "unknown"


def _environment_redirects_fact(environ: Mapping[str, str]) -> Fact:
    redirects = {
        key.lower(): "present" if key in environ else "absent"
        for key in _PATH_BEARING_GIT_VARIABLES
    }
    redirects["git_config_nosystem"] = (
        "present" if "GIT_CONFIG_NOSYSTEM" in environ else "absent"
    )
    redirects["git_optional_locks"] = (
        "present" if "GIT_OPTIONAL_LOCKS" in environ else "absent"
    )
    return Fact(
        id="environment.git_redirects",
        domain="environment",
        state="known",
        value=redirects,
        provenance="ambient_environment",
        limitations=("path_values_intentionally_discarded",),
    )


def _partial_clone_fact_from_result(result: GitResult) -> Fact:
    if result.returncode == 1 and not result.stdout:
        return Fact(
            id="repository.partial_clone",
            domain="repository",
            state="known",
            value=False,
            provenance="git_config_allowlist",
        )
    if not result.ok or result.stdout_truncated or result.stdout_read_failed:
        return Fact(
            id="repository.partial_clone",
            domain="repository",
            state="unavailable",
            value=None,
            provenance="git_config_allowlist",
            limitations=("promisor_config_unavailable_or_too_large",),
        )

    entries = result.stdout.split(b"\0")
    if not entries or entries[-1] != b"":
        return Fact(
            id="repository.partial_clone",
            domain="repository",
            state="unavailable",
            value=None,
            provenance="git_config_allowlist",
            limitations=("promisor_config_malformed",),
        )

    is_partial = False
    for entry in entries[:-1]:
        try:
            key, value = entry.split(b"\n", 1)
        except ValueError:
            return Fact(
                id="repository.partial_clone",
                domain="repository",
                state="unavailable",
                value=None,
                provenance="git_config_allowlist",
                limitations=("promisor_config_malformed",),
            )
        lowered_key = key.lower()
        if lowered_key == b"extensions.partialclone":
            if not value:
                return Fact(
                    id="repository.partial_clone",
                    domain="repository",
                    state="unavailable",
                    value=None,
                    provenance="git_config_allowlist",
                    limitations=("promisor_config_malformed",),
                )
            is_partial = True
        elif lowered_key.startswith(b"remote.") and lowered_key.endswith(
            b".promisor"
        ):
            promisor = _parse_git_bool(value)
            if promisor is None:
                return Fact(
                    id="repository.partial_clone",
                    domain="repository",
                    state="unavailable",
                    value=None,
                    provenance="git_config_allowlist",
                    limitations=("promisor_config_malformed",),
                )
            is_partial = is_partial or promisor
        else:
            return Fact(
                id="repository.partial_clone",
                domain="repository",
                state="unavailable",
                value=None,
                provenance="git_config_allowlist",
                limitations=("promisor_config_malformed",),
            )
    return Fact(
        id="repository.partial_clone",
        domain="repository",
        state="known",
        value=is_partial,
        provenance="git_config_allowlist",
        limitations=("remote_names_and_values_discarded",),
    )


@contextlib.contextmanager
def _quiet_gclient_paths():
    """Discard legacy path-bearing warnings while reusing gclient parsing."""
    previous_disable = logging.root.manager.disable
    with contextlib.redirect_stderr(io.StringIO()):
        logging.disable(logging.CRITICAL)
        try:
            yield
        finally:
            logging.disable(previous_disable)


def _find_ancestor_marker(start: str, marker: str) -> bool:
    current = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(current, marker)):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _repository_overview(report: Report) -> list[str]:
    """Turn selected repository facts into a short user-facing profile."""
    facts = {fact.id: fact for fact in report.facts}

    def known(fact_id: str) -> Optional[Fact]:
        fact = facts.get(fact_id)
        return fact if fact is not None and fact.state == "known" else None

    lines = []
    version = known("git.selected.version")
    repository_kind = known("repository.kind")
    kind_phrases = {
        "main_worktree": "the main worktree",
        "linked_worktree": "a linked worktree",
        "bare": "a bare repository",
    }
    if version and repository_kind:
        kind = kind_phrases.get(repository_kind.value, "this repository")
        lines.append(f"Git {version.value['display']} in {kind}.")
    elif version:
        lines.append(f"Git {version.value['display']}.")
    elif repository_kind:
        kind = kind_phrases.get(repository_kind.value)
        if kind:
            lines.append(f"Current repository: {kind}.")

    index = known("repository.index")
    if index:
        lines.append(
            "Index: "
            f"{index.value['entry_count']:,} entries, "
            f"{_format_byte_size(index.value['size_bytes'])} "
            f"(format v{index.value['format_version']})."
        )

    checkout_parts = []
    shallow = known("repository.shallow")
    if shallow:
        checkout_parts.append(
            "shallow history" if shallow.value else "full history"
        )
    partial = known("repository.partial_clone")
    if partial:
        checkout_parts.append(
            "partial clone on" if partial.value else "partial clone off"
        )
    sparse = known("repository.sparse_checkout")
    if sparse:
        checkout_parts.append(
            "sparse checkout on" if sparse.value else "sparse checkout off"
        )
    if (
        shallow
        and partial
        and sparse
        and not shallow.value
        and not partial.value
        and not sparse.value
    ):
        lines.append(
            "Complete history; partial clone and sparse checkout are off."
        )
    elif checkout_parts:
        lines.append("Checkout: " + "; ".join(checkout_parts) + ".")

    worktrees = known("repository.worktrees")
    if worktrees:
        registered = worktrees.value["registered"]
        linked = worktrees.value["additional_linked"]
        if registered == 1:
            lines.append("One registered worktree.")
        else:
            lines.append(
                f"{registered:,} registered worktrees, including "
                f"{linked:,} additional linked {_plural(linked, 'worktree', 'worktrees')}."
            )

    alternates = known("repository.alternates")
    if alternates and alternates.value:
        lines.append("Object storage uses Git alternates.")

    orchestrator = known("checkout.orchestrator")
    role = known("checkout.repository_role")
    if orchestrator and orchestrator.value == "gclient":
        if role and role.value == "primary_solution":
            lines.append("Workspace: gclient primary solution.")
        else:
            lines.append("Workspace: managed by gclient.")
    elif orchestrator and orchestrator.value == "repo":
        lines.append("Workspace: managed by repo.")

    return lines


def _format_byte_size(size: int) -> str:
    if size < 1024:
        return f"{size:,} {_plural(size, 'byte', 'bytes')}"
    value = float(size)
    for unit in ("KiB", "MiB", "GiB"):
        value /= 1024
        if value < 1024 or unit == "GiB":
            return f"{value:,.1f} {unit}"
    raise AssertionError("unreachable")


_FACT_NAMES = {
    "checkout.execution_owner": "Execution owner",
    "checkout.lifecycle": "Checkout lifecycle",
    "checkout.orchestrator": "Workspace tool",
    "checkout.repository_role": "Repository role",
    "checkout.storage_ownership": "Object storage owner",
    "config.core_fsmonitor.assignments": "core.fsmonitor assignments",
    "config.core_fsmonitor.effective": "Effective core.fsmonitor",
    "environment.git_redirects": "Git environment overrides",
    "git.additional_clients": "Other Git clients",
    "git.dispatcher.identity": "Git dispatcher",
    "git.selected.version": "Git version",
    "policy.profiles": "Applicable policy",
    "repository.alternates": "Alternate object store",
    "repository.identity": "Repository identity",
    "repository.index": "Index",
    "repository.is_submodule": "Submodule worktree",
    "repository.kind": "Repository type",
    "repository.partial_clone": "Partial clone",
    "repository.shallow": "Shallow history",
    "repository.sparse_checkout": "Sparse checkout",
    "repository.worktrees": "Registered worktrees",
    "tool.distribution": "git-perf distribution",
    "trace2.transport": "Trace2 capture method",
    "workload.effects.configured_status": "Possible Git status effects",
    "workload.effects.fsmonitor": "FSMonitor mode",
}

_LIMITATION_TEXT = {
    "absence_does_not_prove_repository_local_storage_ownership": (
        "No alternate object store was found, but this does not establish who "
        "owns the repository storage."
    ),
    "alternates_path_unavailable": (
        "Git did not return the path used to check for an alternate object "
        "store."
    ),
    "arbitrary_hook_effects_are_not_contained": (
        "A configured hook can make changes outside git-perf's control."
    ),
    "automation_and_cache_ownership_not_inferred": (
        "The checkout layout does not reveal who manages its automation or "
        "caches."
    ),
    "boolean_true_semantics_require_a_platform_capability_probe": (
        "A true core.fsmonitor value needs a platform check to determine how "
        "Git will implement it."
    ),
    "capture_or_process_cleanup_uncertain": (
        "A trace writer or Git process may have outlived the foreground run."
    ),
    "changed_between_diagnostic_phases": (
        "This value changed while the diagnosis was running."
    ),
    "config_query_failed_or_too_large": (
        "The Git configuration query failed or returned more data than the "
        "tool accepts."
    ),
    "deadline_limited": "The deadline limited this trace.",
    "deadline_limited_sample_count": (
        "The deadline expired before all requested runs finished."
    ),
    "effective_scope_unavailable": (
        "This Git version did not report the scope of the effective value."
    ),
    "environment_load_encountered_not_controlled": (
        "Other activity on the machine was not controlled."
    ),
    "external_vendored_and_recipe_distribution_not_classified": (
        "git-perf did not determine how this copy was installed."
    ),
    "failed_or_censored_executions_present": (
        "At least one requested run failed, timed out, or did not start."
    ),
    "gclient_metadata_unavailable": (
        "gclient metadata was present but could not be read."
    ),
    "header_entry_count_may_not_equal_tracked_files_for_split_or_sparse_indexes": (
        "The index header count may differ from the number of tracked files "
        "when Git uses a split or sparse index."
    ),
    "materialized_index_state_not_inspected": (
        "git-perf did not inspect FSMonitor data stored in the index."
    ),
    "no_attribution": "No trace was available to attribute the time.",
    "no_declared_warmup": "The benchmark did not include a warmup run.",
    "no_overhead_claim": "The trace does not measure its own overhead.",
    "no_whole_disk_or_private_application_scan_performed": (
        "git-perf did not scan the machine for other Git installations."
    ),
    "non_primary_gclient_role_not_yet_classified": (
        "git-perf does not yet identify non-primary gclient repository roles."
    ),
    "orchestration_root_path_discarded": (
        "The report omits the workspace root path."
    ),
    "path_names_and_orchestrator_do_not_establish_identity": (
        "Paths and workspace metadata are not enough to identify repository "
        "support rules."
    ),
    "path_values_intentionally_discarded": (
        "The report records whether each variable is set but omits its value."
    ),
    "paths_discarded": "The report omits worktree paths.",
    "phase_attribution_is_not_a_latency_distribution": (
        "One trace does not show how phase timings vary between runs."
    ),
    "preflight_deadline_exceeded": (
        "The deadline expired before the setup check finished."
    ),
    "promisor_config_malformed": (
        "Git returned partial-clone configuration in an unexpected format."
    ),
    "promisor_config_unavailable_or_too_large": (
        "Partial-clone configuration was unavailable or exceeded the input "
        "limit."
    ),
    "raw_values_and_origins_discarded": (
        "The report keeps value categories and scopes but omits raw values "
        "and file paths."
    ),
    "remote_names_and_values_discarded": (
        "The report omits remote names and configuration values."
    ),
    "repo_marker_does_not_prove_project_or_storage_ownership": (
        "A .repo directory does not identify the project or storage owner."
    ),
    "repository_context_not_joined": (
        "The offline trace is not linked to repository metadata."
    ),
    "repository_discovery_does_not_prove_storage_ownership": (
        "Repository layout does not establish who owns its storage."
    ),
    "repository_local_config_is_a_shared_baseline": (
        "Repository-local configuration is shared by its worktrees."
    ),
    "selected_git_did_not_correlate_scope_and_origin": (
        "This Git version did not report each value with its scope and source."
    ),
    "selected_git_did_not_report_shallow_state": (
        "This Git version did not report whether history is shallow."
    ),
    "selected_git_did_not_report_superproject": (
        "This Git version did not report whether the worktree is a submodule."
    ),
    "serial_samples_may_be_dependent": (
        "Back-to-back runs may influence one another through caches."
    ),
    "single_machine_single_session": (
        "All timings came from one machine and one session."
    ),
    "single_traced_execution": "The report contains one traced run.",
    "sparse_checkout_config_malformed_or_unavailable": (
        "Sparse-checkout configuration was unavailable or malformed."
    ),
    "status_config_unavailable": (
        "git-perf could not read all settings that may affect Git status."
    ),
    "superproject_path_discarded": (
        "The report records submodule status but omits the superproject path."
    ),
    "superproject_path_unrecognized": (
        "Git returned a superproject path with an unsupported encoding."
    ),
    "system_load_not_measured_in_this_slice": (
        "The benchmark did not record CPU, memory, or disk load."
    ),
    "the_dispatching_git_is_outside_this_direct_entrypoint": (
        "git-perf cannot identify which Git executable dispatched the command."
    ),
    "trace_integrity_limited": (
        "The Trace2 stream was incomplete or exceeded a parser limit."
    ),
    "trace_probe_effect_not_measured": (
        "The tool did not measure Trace2's effect on runtime."
    ),
    "trace_probe_effect_unknown": (
        "The offline trace does not say how capture affected runtime."
    ),
    "untrusted_offline_input": (
        "The parser treated every field in the offline trace as untrusted."
    ),
    "unset_interpreted_as_disabled": (
        "An unset sparse-checkout setting is shown as disabled."
    ),
    "version_output_unrecognized": (
        "Git returned a version string that git-perf does not understand."
    ),
    "warmup_does_not_prove_steady_state": (
        "A warmup run does not prove that caches or system load stabilized."
    ),
    "worktree_and_command_overrides_not_inspected": (
        "Worktree-specific and command-line configuration overrides were not "
        "inspected."
    ),
    "worktree_list_unavailable_or_too_large": (
        "The worktree list was unavailable or exceeded the input limit."
    ),
}


def _human_fact_name(fact_id: str) -> str:
    return _FACT_NAMES.get(fact_id, fact_id)


def _human_limitation(value: str) -> str:
    return _LIMITATION_TEXT.get(
        value, _human_identifier(value).capitalize() + "."
    )


_IDENTIFIER_TEXT = {
    "first_after_preflight": "first run after setup",
    "generic_git": "generic Git",
    "main_worktree": "main worktree",
    "nonzero_exit": "Git exited with an error",
    "numeric_fd_stream": "in-memory file descriptor",
    "operation_deadline": "deadline expired",
    "path_mixed": "mixed installation paths",
    "strict_context_postflight": "repository follow-up check",
    "traced_sample": "traced run",
}


def _human_identifier(value: str) -> str:
    return _IDENTIFIER_TEXT.get(value, value.replace("_", " "))


def _human_value(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        return _human_identifier(value)
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(", ", ": ")
    )


def _human_fact_value(fact: Fact) -> str:
    if fact.state == "unset" and fact.value is None:
        return "unset"
    if fact.id == "git.selected.version" and fact.state == "known":
        return fact.value["display"]
    if fact.id == "repository.index" and fact.state == "known":
        return (
            f"v{fact.value['format_version']}, "
            f"{fact.value['entry_count']:,} header entries, "
            f"{fact.value['size_bytes']:,} bytes"
        )
    if fact.id == "repository.worktrees" and fact.state == "known":
        return (
            f"{fact.value['registered']} registered, "
            f"{fact.value['additional_linked']} additional linked"
        )
    if fact.id == "config.core_fsmonitor.effective" and fact.state == "known":
        return f"{fact.value['mode']} ({fact.value['scope']} scope)"
    if fact.id == "environment.git_redirects" and fact.state == "known":
        present = sorted(
            key for key, state in fact.value.items() if state == "present"
        )
        if not present:
            return "none set"
        return ", ".join(key.upper() for key in present)
    if fact.id == "git.additional_clients" and fact.state == "known":
        return (
            f"{fact.value['detected']} detected; scan "
            f"{fact.value['completeness']}"
        )
    if fact.id == "policy.profiles" and fact.state == "known":
        return ", ".join(
            _human_identifier(profile["id"]) for profile in fact.value
        )
    if fact.id == "repository.identity" and fact.state == "known":
        return (
            f"{fact.value['family']} (confidence: {fact.value['confidence']})"
        )
    if fact.id == "config.core_fsmonitor.assignments" and fact.state == "known":
        return "; ".join(
            f"{_human_identifier(item['value_category'])} from "
            f"{_human_identifier(item['scope'])} scope"
            for item in fact.value
        )
    if (
        fact.id == "workload.effects.configured_status"
        and fact.state == "known"
    ):
        effects = []
        if fact.value["external_hook_may_run"]:
            effects.append("an external hook may run")
        if fact.value["built_in_daemon_may_start"]:
            effects.append("the built-in FSMonitor daemon may start")
        if fact.value["initialized_submodules_may_be_inspected"]:
            effects.append("initialized submodules may be inspected")
        if fact.value["derived_state_may_be_updated"]:
            effects.append("Git may update derived state")
        effects.append(
            "optional locks "
            + _human_identifier(str(fact.value["optional_locks"]))
        )
        return "; ".join(effects)
    return _human_value(fact.value)


def _technical_fact_value(fact: Fact) -> str:
    if fact.state == "known":
        return _human_fact_value(fact)
    if fact.state == "unset":
        return "unset"
    if fact.value is None:
        return _human_identifier(fact.state)
    return f"{_human_fact_value(fact)} ({_human_identifier(fact.state)})"


def _append_wrapped(
    lines: list[str], prefix: str, value: str, width: int
) -> None:
    available = max(8, width - len(prefix))
    chunks = textwrap.wrap(
        value,
        width=available,
        break_long_words=True,
        break_on_hyphens=False,
    ) or [""]
    lines.append(prefix + chunks[0])
    continuation = " " * len(prefix)
    lines.extend(continuation + chunk for chunk in chunks[1:])
