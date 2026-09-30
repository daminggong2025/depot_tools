#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""CLI tests for git_perf.py."""

import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import git_perf
import git_perf_lib


def _report(status="complete"):
    return git_perf_lib.Report(
        status=status,
        tool={"name": "git-perf", "implementation": "test"},
        subject={"kind": "current_repository"},
        facts=[
            git_perf_lib.Fact(
                "repository.kind",
                "repository",
                "known",
                "main_worktree",
                "test",
            )
        ],
    )


class _BrokenPipe:
    def write(self, _data):
        raise BrokenPipeError()

    def flush(self):
        raise BrokenPipeError()


class GitPerfCliTest(unittest.TestCase):
    @mock.patch("git_perf.git_perf_lib.collect_check", return_value=_report())
    def test_no_arguments_and_explicit_check_are_equivalent(self, collect):
        default_output = io.StringIO()
        check_output = io.StringIO()

        self.assertEqual(0, git_perf.main([], stdout=default_output))
        self.assertEqual(0, git_perf.main(["check"], stdout=check_output))

        self.assertEqual(default_output.getvalue(), check_output.getvalue())
        self.assertEqual(2, collect.call_count)

    @mock.patch("git_perf.git_perf_lib.collect_check", return_value=_report())
    def test_root_and_subcommand_json_options_are_equivalent(self, _collect):
        root_output = io.StringIO()
        command_output = io.StringIO()

        self.assertEqual(0, git_perf.main(["--json"], stdout=root_output))
        self.assertEqual(
            0, git_perf.main(["check", "--json"], stdout=command_output)
        )

        self.assertEqual(
            json.loads(root_output.getvalue()),
            json.loads(command_output.getvalue()),
        )
        self.assertEqual(
            "experimental",
            json.loads(root_output.getvalue())["schema_stability"],
        )

    @mock.patch("git_perf.git_perf_lib.collect_check", return_value=_report())
    def test_json_and_verbose_explicitly_produce_json(self, _collect):
        output = io.StringIO()
        self.assertEqual(
            0,
            git_perf.main(["--verbose", "check", "--json"], stdout=output),
        )
        self.assertEqual("complete", json.loads(output.getvalue())["status"])
        self.assertNotIn("Git performance", output.getvalue())

    @mock.patch(
        "git_perf.git_perf_lib.collect_check", return_value=_report("failed")
    )
    def test_failed_report_exits_two_but_is_still_rendered(self, _collect):
        output = io.StringIO()
        self.assertEqual(2, git_perf.main(["--json"], stdout=output))
        self.assertEqual("failed", json.loads(output.getvalue())["status"])

    @mock.patch(
        "git_perf.git_perf_lib.collect_check", side_effect=KeyboardInterrupt
    )
    def test_interrupt_is_quiet_and_returns_130(self, _collect):
        output = io.StringIO()
        error = io.StringIO()
        self.assertEqual(130, git_perf.main([], stdout=output, stderr=error))
        self.assertEqual("", output.getvalue())
        self.assertEqual("interrupted\n", error.getvalue())

    @mock.patch("git_perf.git_perf_lib.collect_check", return_value=_report())
    def test_broken_pipe_is_quiet_success(self, _collect):
        error = io.StringIO()
        self.assertEqual(
            0, git_perf.main([], stdout=_BrokenPipe(), stderr=error)
        )
        self.assertEqual("", error.getvalue())

    @mock.patch(
        "git_perf.git_perf_lib.collect_check", return_value=_report("failed")
    )
    def test_broken_pipe_preserves_an_earlier_failed_report(self, _collect):
        self.assertEqual(2, git_perf.main([], stdout=_BrokenPipe()))

    def test_help_spellings(self):
        for argv in (
            ["--help"],
            ["help"],
            ["check", "--help"],
            ["help", "check"],
            ["bench", "--help"],
            ["help", "bench"],
            ["trace", "--help"],
            ["help", "trace"],
            ["diagnose", "--help"],
            ["help", "diagnose"],
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                with self.assertRaises(SystemExit) as raised:
                    git_perf.main(argv)
            self.assertEqual(0, raised.exception.code)
            self.assertTrue(
                any(
                    phrase in output.getvalue()
                    for phrase in (
                        "Inspect the current repository",
                        "Read selected Git configuration",
                        "Run Git status",
                        "Capture Trace2 events",
                        "Benchmark Git status",
                    )
                )
            )

    def test_option_abbreviations_are_rejected(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            with self.assertRaises(SystemExit) as raised:
                git_perf.main(["--verb"])
        self.assertEqual(2, raised.exception.code)
        self.assertIn("unrecognized arguments", error.getvalue())

    @mock.patch("git_perf.git_perf_lib.collect_check", return_value=_report())
    def test_runtime_context_is_passed_to_collection(self, collect):
        output = io.StringIO()
        environment = {"PATH": "/controlled"}
        self.assertEqual(
            0,
            git_perf.main(
                [],
                stdout=output,
                cwd="/controlled/repository",
                environ=environment,
                git_executable="/controlled/git",
            ),
        )
        collect.assert_called_once_with(
            cwd="/controlled/repository",
            environ=environment,
            git_executable="/controlled/git",
        )

    @mock.patch(
        "git_perf.git_perf_lib.collect_benchmark", return_value=_report()
    )
    def test_bench_routes_bounded_controls_and_runtime_context(self, collect):
        output = io.StringIO()
        environment = {"PATH": "/controlled"}

        self.assertEqual(
            0,
            git_perf.main(
                [
                    "bench",
                    "--samples=3",
                    "--warmup=0",
                    "--deadline=12",
                    "--json",
                ],
                stdout=output,
                cwd="/controlled/repository",
                environ=environment,
                git_executable="/controlled/git",
            ),
        )

        collect.assert_called_once_with(
            cwd="/controlled/repository",
            environ=environment,
            git_executable="/controlled/git",
            samples=3,
            warmup=0,
            deadline_seconds=12,
            progress=mock.ANY,
        )
        self.assertEqual("complete", json.loads(output.getvalue())["status"])

    def test_bench_control_bounds_are_rejected_by_cli(self):
        for option in ("--samples=0", "--warmup=-1", "--deadline=0"):
            with self.subTest(option=option):
                error = io.StringIO()
                with contextlib.redirect_stderr(error):
                    with self.assertRaises(SystemExit) as raised:
                        git_perf.main(["bench", option])
                self.assertEqual(2, raised.exception.code)
                self.assertIn("must be from", error.getvalue())

    @mock.patch("git_perf.git_perf_lib.collect_trace", return_value=_report())
    def test_live_trace_routes_deadline_and_runtime_context(self, collect):
        output = io.StringIO()
        environment = {"PATH": "/controlled"}

        self.assertEqual(
            0,
            git_perf.main(
                ["trace", "--deadline=9", "--json"],
                stdout=output,
                cwd="/controlled/repository",
                environ=environment,
                git_executable="/controlled/git",
            ),
        )

        collect.assert_called_once_with(
            cwd="/controlled/repository",
            environ=environment,
            git_executable="/controlled/git",
            deadline_seconds=9,
            progress=mock.ANY,
        )

    @mock.patch(
        "git_perf.git_perf_lib.collect_trace_input", return_value=_report()
    )
    def test_offline_trace_routes_binary_stdin_without_live_context(
        self, collect
    ):
        trace_input = io.BytesIO(b"trace")
        self.assertEqual(
            0,
            git_perf.main(
                ["trace", "--input=-", "--json"],
                stdout=io.StringIO(),
                stdin=trace_input,
                cwd="/must/not/be/discovered",
                environ={"PATH": ""},
                git_executable="/missing/git",
            ),
        )

        collect.assert_called_once_with("-", stdin=trace_input)

    @mock.patch(
        "git_perf.git_perf_lib.collect_diagnose", return_value=_report()
    )
    def test_diagnose_routes_controls_and_runtime_context(self, collect):
        environment = {"PATH": "/controlled"}
        self.assertEqual(
            0,
            git_perf.main(
                [
                    "diagnose",
                    "--samples=2",
                    "--warmup=1",
                    "--deadline=15",
                    "--json",
                ],
                stdout=io.StringIO(),
                cwd="/controlled/repository",
                environ=environment,
                git_executable="/controlled/git",
            ),
        )
        collect.assert_called_once_with(
            cwd="/controlled/repository",
            environ=environment,
            git_executable="/controlled/git",
            samples=2,
            warmup=1,
            deadline_seconds=15,
            progress=mock.ANY,
        )

    def test_trace_input_and_live_deadline_are_mutually_exclusive(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            with self.assertRaises(SystemExit) as raised:
                git_perf.main(["trace", "--input=-", "--deadline=5"])
        self.assertEqual(2, raised.exception.code)
        self.assertIn("not allowed with argument", error.getvalue())

    @mock.patch(
        "git_perf.git_perf_lib.collect_check",
        side_effect=git_perf._TerminationRequested,
    )
    def test_catchable_sigterm_returns_143(self, _collect):
        self.assertEqual(143, git_perf.main([], stdout=io.StringIO()))

    def test_direct_entry_does_not_cache_product_modules(self):
        with tempfile.TemporaryDirectory(prefix="git-perf-pycache-") as tempdir:
            cache = os.path.join(tempdir, "cache")
            environment = os.environ.copy()
            environment.pop("PYTHONDONTWRITEBYTECODE", None)
            environment["PYTHONPYCACHEPREFIX"] = cache
            process = subprocess.run(
                [sys.executable, git_perf.__file__, "--json"],
                cwd=tempdir,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            self.assertEqual(2, process.returncode)
            self.assertEqual("failed", json.loads(process.stdout)["status"])
            product_bytecode = list(pathlib.Path(cache).rglob("git_perf*.pyc"))
            self.assertEqual([], product_bytecode)

    def test_offline_trace_cli_needs_neither_git_nor_repository(self):
        events = b"".join(
            json.dumps(event, separators=(",", ":")).encode() + b"\n"
            for event in (
                {
                    "event": "version",
                    "sid": "PRIVATE-SID",
                    "thread": "main",
                    "time": "2026-01-01T00:00:00.000000Z",
                    "evt": "2",
                },
                {
                    "event": "start",
                    "sid": "PRIVATE-SID",
                    "thread": "main",
                    "time": "2026-01-01T00:00:00.100000Z",
                    "t_abs": 0.1,
                },
                {
                    "event": "atexit",
                    "sid": "PRIVATE-SID",
                    "thread": "main",
                    "time": "2026-01-01T00:00:00.200000Z",
                    "t_abs": 0.2,
                },
            )
        )
        with tempfile.TemporaryDirectory(prefix="git-perf-offline-cli-") as cwd:
            environment = os.environ.copy()
            environment["PATH"] = ""
            process = subprocess.run(
                [
                    sys.executable,
                    git_perf.__file__,
                    "trace",
                    "--input=-",
                    "--json",
                ],
                cwd=cwd,
                env=environment,
                input=events,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(0, process.returncode, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual("complete", report["status"])
        self.assertEqual("trace_input", report["tool"]["operation"])
        self.assertNotIn(b"PRIVATE", process.stdout)
        self.assertEqual(b"", process.stderr)

    @unittest.skipIf(sys.platform == "win32", "uses POSIX process signals")
    def test_sigterm_cleans_active_git_and_exits_143_without_traceback(self):
        with tempfile.TemporaryDirectory(prefix="git-perf-sigterm-") as tempdir:
            fake_git = os.path.join(tempdir, "git")
            with open(fake_git, "w", encoding="utf-8") as script:
                script.write("#!/bin/sh\nexec /bin/sleep 10\n")
            os.chmod(fake_git, 0o755)
            environment = os.environ.copy()
            environment["PATH"] = tempdir
            process = subprocess.Popen(
                [sys.executable, git_perf.__file__, "--json"],
                cwd=tempdir,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            time.sleep(0.2)
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)

        self.assertEqual(143, process.returncode)
        self.assertEqual(b"", stdout)
        self.assertNotIn(b"Traceback", stderr)

    def test_launcher_provenance_classifies_coherent_and_mixed_paths(self):
        root = os.path.dirname(git_perf.__file__)
        coherent = {
            "DEPOT_TOOLS_GIT_PERF_LAUNCHER": os.path.join(root, "git-perf"),
            "DEPOT_TOOLS_GIT_PERF_PYTHON_RUNNER": os.path.join(
                root, "python_runner.sh"
            ),
        }
        with tempfile.NamedTemporaryFile() as unrelated:
            mixed = dict(coherent)
            mixed["DEPOT_TOOLS_GIT_PERF_PYTHON_RUNNER"] = unrelated.name

            self.assertEqual(
                "coherent", git_perf_lib.launcher_provenance(coherent)
            )
            self.assertEqual(
                "path_mixed", git_perf_lib.launcher_provenance(mixed)
            )
        self.assertEqual("unknown", git_perf_lib.launcher_provenance({}))

    @unittest.skipIf(shutil.which("git") is None, "requires Git")
    def test_bench_cli_end_to_end_emits_private_path_free_json(self):
        with tempfile.TemporaryDirectory(prefix="git-perf-cli-bench-") as repo:
            subprocess.run(
                ["git", "init", "--quiet", repo],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            canary = "PRIVATE-CLI-BENCH-CANARY"
            with open(os.path.join(repo, canary), "wb") as untracked:
                untracked.write(b"untracked\n")
            environment = os.environ.copy()
            environment["GIT_CONFIG_NOSYSTEM"] = "1"
            environment["GIT_CONFIG_GLOBAL"] = os.devnull

            process = subprocess.run(
                [
                    sys.executable,
                    git_perf.__file__,
                    "bench",
                    "--samples=1",
                    "--warmup=0",
                    "--deadline=20",
                    "--json",
                ],
                cwd=repo,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

        self.assertEqual(0, process.returncode, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual("complete", report["status"])
        self.assertEqual("bench", report["tool"]["operation"])
        self.assertEqual(
            ["first_after_preflight", "measured"],
            [item["role"] for item in report["measurements"][0]["executions"]],
        )
        self.assertNotIn(canary, process.stdout)
        self.assertNotIn(repo, process.stdout)
        self.assertIn("About to run Git status up to 2 times", process.stderr)
        self.assertIn(
            "git-perf itself does not edit Git configuration",
            process.stderr,
        )


if __name__ == "__main__":
    unittest.main()
