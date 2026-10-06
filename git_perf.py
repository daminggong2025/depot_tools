#!/usr/bin/env python3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Command-line interface for Git performance inspection."""

from __future__ import annotations

import argparse
import contextlib
import os
import signal
import sys

from typing import Any, Mapping, Optional, Sequence, TextIO

# Direct execution bypasses python_runner.sh, so prevent imports from writing
# bytecode into the checkout.
if __name__ == "__main__":
    sys.dont_write_bytecode = True

import git_perf_lib  # pylint: disable=wrong-import-position


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        add_help=False,
        allow_abbrev=False,
        argument_default=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="show the facts used in the report and their collection limits",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "write the experimental report as one JSON document; --verbose "
            "does not change JSON output"
        ),
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    parser = argparse.ArgumentParser(
        prog="git perf",
        description=(
            "Inspect the current repository or measure Git status. git-perf "
            "does not edit Git configuration itself. The live measurement "
            "commands run Git with the repository's existing settings, so "
            "configured hooks and caches may have side effects."
        ),
        parents=[common],
        allow_abbrev=False,
    )
    subparsers = parser.add_subparsers(dest="command", metavar="command")
    subparsers.add_parser(
        "check",
        help="inspect repository setup without measuring Git status (default)",
        description=(
            "Read selected Git configuration and repository metadata. This "
            "mode does not run Git status, execute hooks, or edit Git "
            "configuration."
        ),
        parents=[common],
        allow_abbrev=False,
    )
    bench = subparsers.add_parser(
        "bench",
        help="measure Git status with the repository's current settings",
        description=(
            "Run Git status using the repository's current settings. Git may "
            "update optional caches, invoke FSMonitor, or inspect initialized "
            "submodules. A configured hook can have other side effects. The "
            "maximum number of status runs is 1 + warmup + samples."
        ),
        parents=[common],
        allow_abbrev=False,
    )
    _add_benchmark_controls(bench)

    trace = subparsers.add_parser(
        "trace",
        help="record where one Git status run spends its time",
        description=(
            "Capture Trace2 events from one Git status run, or read an "
            "existing Trace2 EVENT stream with --input. A live run uses the "
            "repository's current settings. Hooks and FSMonitor may run, and "
            "Git may inspect submodules or update optional caches."
        ),
        parents=[common],
        allow_abbrev=False,
    )
    trace_mode = trace.add_mutually_exclusive_group()
    trace_mode.add_argument(
        "--input",
        metavar="PATH",
        help=(
            "read Trace2 events from a regular file; use '-' for binary "
            "stdin (this mode does not inspect a repository or run Git)"
        ),
    )
    trace_mode.add_argument(
        "--deadline",
        metavar="SECONDS",
        type=_bounded_integer(
            "deadline",
            git_perf_lib.MIN_BENCHMARK_DEADLINE_SECONDS,
            git_perf_lib.MAX_BENCHMARK_DEADLINE_SECONDS,
        ),
        default=argparse.SUPPRESS,
        help=(
            "deadline for the live Git run and Trace2 capture "
            f"(default: {git_perf_lib.DEFAULT_TRACE_DEADLINE_SECONDS})"
        ),
    )

    diagnose = subparsers.add_parser(
        "diagnose",
        help="time Git status and trace one separate run",
        description=(
            "Benchmark Git status, then trace one additional status run. The "
            "trace is not included in the benchmark timings. These runs use "
            "the repository's current settings, so hooks and FSMonitor may "
            "run and Git may update optional caches. The maximum number of "
            "status runs is 2 + warmup + samples."
        ),
        parents=[common],
        allow_abbrev=False,
    )
    _add_benchmark_controls(diagnose)
    return parser


def _add_benchmark_controls(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--samples",
        metavar="N",
        type=_bounded_integer(
            "samples",
            git_perf_lib.MIN_BENCHMARK_SAMPLES,
            git_perf_lib.MAX_BENCHMARK_SAMPLES,
        ),
        default=argparse.SUPPRESS,
        help=(
            "number of measured runs after the first run and warmups "
            f"(default: {git_perf_lib.DEFAULT_BENCHMARK_SAMPLES})"
        ),
    )
    parser.add_argument(
        "--warmup",
        metavar="N",
        type=_bounded_integer(
            "warmup",
            git_perf_lib.MIN_BENCHMARK_WARMUP,
            git_perf_lib.MAX_BENCHMARK_WARMUP,
        ),
        default=argparse.SUPPRESS,
        help=(
            "extra runs to omit from the timings after the first run "
            f"(default: {git_perf_lib.DEFAULT_BENCHMARK_WARMUP})"
        ),
    )
    parser.add_argument(
        "--deadline",
        metavar="SECONDS",
        type=_bounded_integer(
            "deadline",
            git_perf_lib.MIN_BENCHMARK_DEADLINE_SECONDS,
            git_perf_lib.MAX_BENCHMARK_DEADLINE_SECONDS,
        ),
        default=argparse.SUPPRESS,
        help=(
            "deadline for setup, status runs, and follow-up checks "
            f"(default: {git_perf_lib.DEFAULT_BENCHMARK_DEADLINE_SECONDS})"
        ),
    )


def _bounded_integer(name: str, minimum: int, maximum: int):

    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as error:
            raise argparse.ArgumentTypeError(
                f"{name} must be an integer"
            ) from error
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(
                f"{name} must be from {minimum} to {maximum}"
            )
        return parsed

    return parse


def _normalize_help(argv: Sequence[str]) -> list[str]:
    """Map depot_tools-style `help [command]` onto argparse help."""
    argv = list(argv)
    if not argv or argv[0] != "help":
        return argv
    if len(argv) == 1:
        return ["--help"]
    if len(argv) == 2 and argv[1] in (
        "check",
        "bench",
        "trace",
        "diagnose",
    ):
        return [argv[1], "--help"]
    return argv


class _TerminationRequested(BaseException):
    """Internal control flow for a catchable SIGTERM."""


@contextlib.contextmanager
def _catch_sigterm():
    previous = None
    installed = False

    def terminate(_signum, _frame):
        raise _TerminationRequested()

    try:
        previous = signal.signal(signal.SIGTERM, terminate)
        installed = True
    except (AttributeError, OSError, ValueError):
        pass
    try:
        yield
    finally:
        if installed:
            signal.signal(signal.SIGTERM, previous)


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
    stdin: Optional[Any] = None,
    cwd: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    git_executable: str = "git",
) -> int:
    """Run the requested operation and return its documented exit status."""
    argv = sys.argv[1:] if argv is None else argv
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    stdin = sys.stdin if stdin is None else stdin
    options = build_parser().parse_args(_normalize_help(argv))
    if options.command is None:
        options.command = "check"

    report = None

    def progress(message: str) -> None:
        stderr.write(message + "\n")
        stderr.flush()

    try:
        with _catch_sigterm():
            collection_args = {
                "cwd": os.getcwd() if cwd is None else cwd,
                "environ": os.environ if environ is None else environ,
                "git_executable": git_executable,
            }
            if options.command == "bench":
                report = git_perf_lib.collect_benchmark(
                    **collection_args,
                    samples=getattr(
                        options,
                        "samples",
                        git_perf_lib.DEFAULT_BENCHMARK_SAMPLES,
                    ),
                    warmup=getattr(
                        options,
                        "warmup",
                        git_perf_lib.DEFAULT_BENCHMARK_WARMUP,
                    ),
                    deadline_seconds=getattr(
                        options,
                        "deadline",
                        git_perf_lib.DEFAULT_BENCHMARK_DEADLINE_SECONDS,
                    ),
                    progress=progress,
                )
            elif options.command == "trace":
                input_path = getattr(options, "input", None)
                if input_path is not None:
                    binary_stdin = getattr(stdin, "buffer", stdin)
                    report = git_perf_lib.collect_trace_input(
                        input_path, stdin=binary_stdin
                    )
                else:
                    report = git_perf_lib.collect_trace(
                        **collection_args,
                        deadline_seconds=getattr(
                            options,
                            "deadline",
                            git_perf_lib.DEFAULT_TRACE_DEADLINE_SECONDS,
                        ),
                        progress=progress,
                    )
            elif options.command == "diagnose":
                report = git_perf_lib.collect_diagnose(
                    **collection_args,
                    samples=getattr(
                        options,
                        "samples",
                        git_perf_lib.DEFAULT_BENCHMARK_SAMPLES,
                    ),
                    warmup=getattr(
                        options,
                        "warmup",
                        git_perf_lib.DEFAULT_BENCHMARK_WARMUP,
                    ),
                    deadline_seconds=getattr(
                        options,
                        "deadline",
                        git_perf_lib.DEFAULT_BENCHMARK_DEADLINE_SECONDS,
                    ),
                    progress=progress,
                )
            else:
                report = git_perf_lib.collect_check(**collection_args)
            report.tool["launcher_provenance"] = (
                git_perf_lib.launcher_provenance(collection_args["environ"])
            )
            if getattr(options, "json", False):
                stdout.write(git_perf_lib.report_json(report) + "\n")
            else:
                stdout.write(
                    git_perf_lib.render_human(
                        report, verbose=getattr(options, "verbose", False)
                    )
                )
            stdout.flush()
    except BrokenPipeError:
        return 2 if report is not None and report.status == "failed" else 0
    except KeyboardInterrupt:
        try:
            stderr.write("interrupted\n")
            stderr.flush()
        except BrokenPipeError:
            pass
        return 130
    except _TerminationRequested:
        return 143

    assert report is not None
    return 2 if report.status == "failed" else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
