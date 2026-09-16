# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Lightweight Chrome Trace Event format tracer for depot_tools.

Produces JSON trace files compatible with Perfetto UI (https://ui.perfetto.dev)
and chrome://tracing with zero external dependencies.
"""

import contextlib
import functools
import json
import os
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Dict, Iterator, List, NamedTuple, Optional

try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None


# Configuration for environment-variable-driven trace collection.
class _EnvTraceConfig(NamedTuple):
    env_var: str
    default_file: str
    default_process: str


_ENV_TRACE_CONFIGS: List[_EnvTraceConfig] = [
    _EnvTraceConfig(
        env_var="PRESUBMIT_TRACE",
        default_file=os.path.join(
            tempfile.gettempdir(), "presubmit_trace.json"
        ),
        default_process="presubmit",
    ),
    _EnvTraceConfig(
        env_var="GCLIENT_TRACE",
        default_file=os.path.join(tempfile.gettempdir(), "gclient_trace.json"),
        default_process="gclient",
    ),
    _EnvTraceConfig(
        env_var="DEPOT_TOOLS_TRACE",
        default_file=os.path.join(
            tempfile.gettempdir(), "depot_tools_trace.json"
        ),
        default_process="depot_tools",
    ),
]
_DEFAULT_TRACE_FILE = os.path.join(
    tempfile.gettempdir(), "depot_tools_trace.json"
)


@contextlib.contextmanager
def _file_lock(lock_path: str) -> Iterator[None]:
    """Acquires an exclusive cross-process lock using a dedicated lock file.

    Uses fcntl.flock on POSIX systems and msvcrt.locking on Windows.
    """
    if not (fcntl or msvcrt):
        yield
        return

    parent_dir = os.path.dirname(lock_path)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)

    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        fd = lock_file.fileno()
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        elif msvcrt:
            lock_file.seek(0)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                try:
                    lock_file.seek(0)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass


# Collects trace events in memory and flushes them to a Chrome Trace Event JSON file.
class TraceCollector:
    """Collects trace events and writes them to a JSON file."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: List[Dict[str, Any]] = []
        self._threads: Dict[int, str] = {}
        self._enabled = False
        self._filepath: Optional[str] = None
        self._pid = os.getpid()

        # Check environment variables on startup.
        for config in _ENV_TRACE_CONFIGS:
            env_val = os.environ.get(config.env_var)
            if env_val:
                val_lower = env_val.strip().lower()
                if not val_lower or val_lower in ("0", "false", "no", "off"):
                    continue

                script_name = (
                    os.path.splitext(os.path.basename(sys.argv[0]))[0]
                    if sys.argv and sys.argv[0]
                    else config.default_process
                )
                target_file = (
                    config.default_file
                    if val_lower in ("1", "true", "yes", "on")
                    else env_val
                )
                self.start(target_file, process_name=script_name)
                break

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def filepath(self) -> Optional[str]:
        return self._filepath

    def start(
        self,
        filepath: Optional[str] = None,
        process_name: Optional[str] = None,
    ) -> None:
        """Starts trace collection to the given file path."""
        with self._lock:
            if self._enabled:
                # Tracing is already enabled (e.g. initialized early via environment
                # variable and called again during CLI argument parsing).
                # Do not wipe already collected events; update filepath and process
                # name if specified.
                if filepath:
                    self._filepath = filepath

                if process_name:
                    self._set_process_name_locked(process_name)

                return

            self._enabled = True
            self._filepath = filepath or _DEFAULT_TRACE_FILE
            self._events = []
            self._threads = {}
            # Record main process/thread metadata.
            if not process_name:
                prog = os.path.basename(sys.argv[0]) if sys.argv else ""
                process_name = os.path.splitext(prog)[0] or "depot_tools"

            self._set_process_name_locked(process_name)
            self._set_thread_name_locked(threading.get_ident(), "MainThread")

    def set_thread_name(self, name: str, tid: Optional[int] = None) -> None:
        """Sets the display name for a thread."""
        if not self._enabled:
            return

        if tid is None:
            tid = threading.get_ident()

        with self._lock:
            self._set_thread_name_locked(tid, name)

    def _set_process_name_locked(self, name: str) -> None:
        # In Chrome Trace Event format, "ph": "M" denotes a Metadata event.
        # If a process_name metadata event already exists (e.g. from early
        # initialization), update it in place rather than appending a duplicate.
        for event in self._events:
            if event.get("ph") == "M" and event.get("name") == "process_name":
                event["args"] = {"name": name}
                return

        self._events.append(
            {
                "name": "process_name",
                "ph": "M",
                "pid": self._pid,
                "tid": threading.get_ident(),
                "args": {"name": name},
            }
        )

    def _set_thread_name_locked(self, tid: int, name: str) -> None:
        self._threads[tid] = name
        self._events.append(
            {
                "name": "thread_name",
                "ph": "M",
                "pid": self._pid,
                "tid": tid,
                "args": {"name": name},
            }
        )

    def add_complete_event(
        self,
        name: str,
        cat: Optional[str],
        start_us: int,
        dur_us: int,
        tid: Optional[int] = None,
        args: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Adds a Complete ('X') trace event."""
        if not self._enabled:
            return

        if tid is None:
            tid = threading.get_ident()

        event: Dict[str, Any] = {
            "name": name,
            "cat": cat or "gclient",
            "ph": "X",
            "ts": start_us,
            "dur": dur_us,
            "pid": self._pid,
            "tid": tid,
        }
        if args:
            # Ensure args values are JSON serializable.
            clean_args: Dict[str, Any] = {}
            for k, v in args.items():
                if (
                    isinstance(v, (str, int, float, bool, list, dict))
                    or v is None
                ):
                    clean_args[k] = v
                else:
                    clean_args[k] = str(v)
            event["args"] = clean_args

        with self._lock:
            self._events.append(event)

    def close(self) -> None:
        """Flushes collected trace events to the JSON file and disables tracing."""
        if not self._enabled:
            return

        with self._lock:
            self._enabled = False
            events_to_write = list(self._events)
            filepath = self._filepath

        if not filepath or not events_to_write:
            return

        try:
            parent_dir = os.path.dirname(filepath)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)

            lock_path = filepath + ".lock"
            with _file_lock(lock_path):
                existing_events: List[Dict[str, Any]] = []
                if os.path.exists(filepath):
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            content = f.read()
                            if content.strip():
                                data = json.loads(content)
                                if isinstance(data, dict) and isinstance(
                                    data.get("traceEvents"), list
                                ):
                                    existing_events = data["traceEvents"]
                    except Exception:
                        pass

                all_events = existing_events + events_to_write

                # Write to a temporary file first and atomically replace to
                # prevent data corruption or loss if interrupted during write.
                tmp_filepath = (
                    f"{filepath}.tmp.{os.getpid()}.{threading.get_ident()}"
                )
                try:
                    with open(tmp_filepath, "w", encoding="utf-8") as tmp_f:
                        json.dump({"traceEvents": all_events}, tmp_f, indent=2)
                        tmp_f.flush()
                        os.fsync(tmp_f.fileno())

                    os.replace(tmp_filepath, filepath)
                finally:
                    if os.path.exists(tmp_filepath):
                        try:
                            os.remove(tmp_filepath)
                        except OSError:
                            pass

            print(
                f"Trace written to {os.path.abspath(filepath)} (open in https://ui.perfetto.dev)",
                file=sys.stderr,
            )
        except Exception as e:
            print(
                f"Failed to write trace file {filepath}: {e}", file=sys.stderr
            )


collector = TraceCollector()


@contextlib.contextmanager
def trace(
    name: str,
    cat: str = "gclient",
    args: Optional[Dict[str, Any]] = None,
) -> Iterator[None]:
    """Context manager to trace execution time of a block."""
    if not collector.enabled:
        yield
        return

    tid = threading.get_ident()
    start_time = time.time()
    try:
        yield
    finally:
        end_time = time.time()
        start_us = int(start_time * 1_000_000)
        dur_us = int((end_time - start_time) * 1_000_000)
        collector.add_complete_event(
            name, cat, start_us, dur_us, tid=tid, args=args
        )


def trace_function(
    name: Optional[str] = None,
    cat: str = "gclient",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to trace a function call."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        event_name = name or fn.__qualname__

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not collector.enabled:
                return fn(*args, **kwargs)

            with trace(event_name, cat=cat):
                return fn(*args, **kwargs)

        return wrapper

    return decorator
