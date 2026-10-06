#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for Git performance inspection and measurement."""

import dataclasses
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

from concurrent import futures
from unittest import mock

DEPOT_TOOLS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DEPOT_TOOLS_ROOT)

import git_perf_lib  # noqa: E402
from testing_support import git_test_utils  # noqa: E402


def _result(stdout=b"", returncode=0, *, truncated=False):
    return git_perf_lib.GitResult(
        outcome="success" if returncode == 0 else "nonzero_exit",
        returncode=returncode,
        stdout=stdout,
        stdout_truncated=truncated,
        stderr_state="empty",
        elapsed_ns=1,
    )


class _FakeRunner:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def run(self, args, **kwargs):
        key = tuple(args)
        self.calls.append((key, kwargs))
        if key not in self.responses:
            raise AssertionError("unexpected Git call: %r" % (key,))
        response = self.responses[key]
        return response() if callable(response) else response


class _FakeClock:
    def __init__(self):
        self.now_ns = 0

    def __call__(self):
        return self.now_ns

    def advance(self, elapsed_ns):
        self.now_ns += elapsed_ns


class _BenchmarkRunner:
    def __init__(
        self,
        clock,
        status_results,
        *,
        config_results=None,
        version_results=None,
        partial_results=None,
    ):
        self.clock = clock
        self.executable = os.path.abspath(git_perf_lib.__file__)
        self.status_results = list(status_results)
        self.config_results = list(
            config_results
            or [
                _result(b"core.fsmonitor\nPRIVATE-CONFIG-CANARY\0"),
                _result(b"core.fsmonitor\nPRIVATE-CONFIG-CANARY\0"),
            ]
        )
        self.version_results = list(
            version_results
            or [
                _result(b"git version 2.55.0.vendor\n"),
                _result(b"git version 2.55.0.vendor\n"),
            ]
        )
        self.partial_results = list(partial_results or [_result(returncode=1)])
        self.calls = []

    def run(self, args, **kwargs):
        key = tuple(args)
        self.calls.append((key, kwargs))
        if key == ("version",):
            result = self.version_results.pop(0)
        elif key[:2] == ("rev-parse", "--is-bare-repository"):
            result = _result(b"false\ntrue\n/checkout/.git\n/checkout/.git\n")
        elif key == (
            "config",
            "--null",
            "--get-regexp",
            r"^(extensions\.partialclone|remote\..*\.promisor)$",
        ):
            result = self.partial_results.pop(0)
        elif key[:3] == ("config", "--null", "--get-regexp"):
            result = self.config_results.pop(0)
        elif key == git_perf_lib.STATUS_WORKLOAD_ARGS:
            result = self.status_results.pop(0)
            consumer = kwargs.get("stdout_consumer")
            if consumer is not None:
                midpoint = len(result.stdout) // 2
                consumer(result.stdout[:midpoint])
                consumer(result.stdout[midpoint:])
        else:
            raise AssertionError("unexpected Git call: %r" % (key,))
        self.clock.advance(result.elapsed_ns + result.cleanup_elapsed_ns)
        return result


def _timed_result(elapsed_ns, stdout=b"# branch.head main\0? PRIVATE-PATH\0"):
    return git_perf_lib.GitResult(
        outcome="success",
        returncode=0,
        stdout=stdout,
        stdout_truncated=False,
        stderr_state="empty",
        elapsed_ns=elapsed_ns,
    )


def _standard_responses(
    *,
    root="/checkout",
    git_dir="/checkout/.git",
    common_dir="/checkout/.git",
    worktrees=1,
    fsmonitor=b"",
):
    worktree_records = b"".join(
        b"worktree /discarded/path\0HEAD discarded\0\0"
        for _ in range(worktrees)
    )
    fsmonitor_result = (
        _result(fsmonitor) if fsmonitor else _result(returncode=1)
    )
    return {
        ("version",): _result(b"git version 2.55.0.vendor\n"),
        (
            "rev-parse",
            "--is-bare-repository",
            "--is-inside-work-tree",
            "--git-dir",
            "--git-common-dir",
        ): _result(
            b"false\ntrue\n"
            + os.fsencode(git_dir)
            + b"\n"
            + os.fsencode(common_dir)
            + b"\n"
        ),
        ("rev-parse", "--show-toplevel"): _result(os.fsencode(root) + b"\n"),
        ("rev-parse", "--is-shallow-repository"): _result(b"false\n"),
        ("config", "--bool", "--get", "core.sparseCheckout"): _result(
            returncode=1
        ),
        (
            "config",
            "--null",
            "--get-regexp",
            r"^(extensions\.partialclone|remote\..*\.promisor)$",
        ): _result(returncode=1),
        (
            "rev-parse",
            "--git-path",
            "objects/info/alternates",
        ): _result(b"/missing/alternates\n"),
        ("rev-parse", "--show-superproject-working-tree"): _result(),
        ("worktree", "list", "--porcelain", "-z"): _result(worktree_records),
        ("rev-parse", "--git-path", "index"): _result(b"/missing/index\n"),
        (
            "config",
            "--null",
            "--show-origin",
            "--show-scope",
            "--get-all",
            "core.fsmonitor",
        ): fsmonitor_result,
        ("config", "--null", "--get-all", "core.fsmonitor"): _result(
            returncode=1
        ),
    }


def _fact(report, fact_id):
    return next(fact for fact in report.facts if fact.id == fact_id)


class GitRunnerEnvironmentTest(unittest.TestCase):
    def test_strict_environment_preserves_config_and_disables_writes(self):
        runner = git_perf_lib.GitRunner(
            os.path.abspath("/bin/git"),
            environ={
                "PATH": "/bin",
                "GIT_CONFIG_COUNT": "1",
                "GIT_OPTIONAL_LOCKS": "1",
                "GIT_TRACE2_EVENT": "/private/trace-canary",
            },
        )
        environment = runner.strict_environment()

        self.assertEqual("1", environment["GIT_CONFIG_COUNT"])
        self.assertEqual("0", environment["GIT_TRACE2_EVENT"])
        self.assertEqual("0", environment["GIT_OPTIONAL_LOCKS"])
        self.assertEqual("1", environment["GIT_NO_LAZY_FETCH"])
        self.assertNotIn("/private/trace-canary", environment.values())

    def test_normal_git_environment_preserves_effects_and_disables_tracing(
        self,
    ):
        runner = git_perf_lib.GitRunner(
            os.path.abspath("/bin/git"),
            environ={
                "PATH": "/bin",
                "GIT_CONFIG_COUNT": "1",
                "GIT_OPTIONAL_LOCKS": "1",
                "GIT_TRACE2_EVENT": "/private/trace-canary",
            },
        )

        environment = runner.environment_for(
            git_perf_lib.NORMAL_GIT_EFFECT_PROFILE
        )

        self.assertEqual("1", environment["GIT_CONFIG_COUNT"])
        self.assertEqual("1", environment["GIT_OPTIONAL_LOCKS"])
        self.assertEqual("0", environment["GIT_TRACE2_EVENT"])
        self.assertEqual("1", environment["GIT_NO_LAZY_FETCH"])
        self.assertEqual("0", environment["GIT_TERMINAL_PROMPT"])
        self.assertNotIn("/private/trace-canary", environment.values())

    def test_unknown_effect_profile_is_rejected(self):
        runner = git_perf_lib.GitRunner(
            os.path.abspath("/bin/git"), environ={"PATH": "/bin"}
        )

        with self.assertRaisesRegex(ValueError, "effect profile"):
            runner.environment_for("unreviewed")


@unittest.skipIf(sys.platform == "win32", "uses a POSIX test executable")
class GitRunnerProcessTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="git-perf-runner-")
        self.addCleanup(shutil.rmtree, self.tempdir, True)
        self.executable = os.path.join(self.tempdir, "fake-git")
        with open(self.executable, "w", encoding="utf-8") as script:
            script.write(
                textwrap.dedent("""\
                    #!/usr/bin/env python3
                    import os
                    import signal
                    import subprocess
                    import sys
                    import time

                    command = sys.argv[2]
                    if command == "environment":
                        print(os.environ.get("GIT_TRACE2_EVENT"))
                        print(os.environ.get("GIT_OPTIONAL_LOCKS"))
                    elif command == "trace-event":
                        descriptor = int(os.environ["GIT_TRACE2_EVENT"])
                        os.write(descriptor, b'{"event":"test"}\\n')
                        print(descriptor)
                    elif command == "trace-large":
                        descriptor = int(os.environ["GIT_TRACE2_EVENT"])
                        os.write(descriptor, b'x' * 1024 * 1024)
                    elif command == "trace-multi-writer":
                        descriptor = int(os.environ["GIT_TRACE2_EVENT"])
                        children = []
                        for writer in range(3):
                            child = os.fork()
                            if child == 0:
                                line = ('{"writer":%d}\\n' % writer).encode()
                                for _ in range(100):
                                    os.write(descriptor, line)
                                os._exit(0)
                            children.append(child)
                        for child in children:
                            os.waitpid(child, 0)
                    elif command == "trace-descendant":
                        descriptor = int(os.environ["GIT_TRACE2_EVENT"])
                        if os.fork() == 0:
                            os.setsid()
                            os.close(1)
                            os.close(2)
                            time.sleep(1.5)
                            try:
                                os.write(descriptor, b"late")
                            except OSError:
                                pass
                            os._exit(0)
                    elif command == "large":
                        sys.stdout.buffer.write(b"x" * 8192)
                    elif command == "both-large":
                        sys.stdout.buffer.write(b"o" * 1024 * 1024)
                        sys.stdout.buffer.flush()
                        sys.stderr.buffer.write(b"e" * 1024 * 1024)
                    elif command == "descendant":
                        marker = sys.argv[3]
                        child = (
                            "import signal,sys,time;"
                            "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                            "print('ready', flush=True);"
                            "time.sleep(1.5);"
                            "open(sys.argv[1], 'w', encoding='utf-8').write('escaped');"
                        )
                        child_process = subprocess.Popen(
                            [sys.executable, "-c", child, marker],
                            stdout=subprocess.PIPE,
                            text=True,
                        )
                        child_process.stdout.readline()
                        print(child_process.pid, flush=True)
                        time.sleep(10)
                    elif command == "stderr":
                        sys.stderr.write("PRIVATE-STDERR-CANARY")
                        sys.exit(7)
                    elif command == "sleep":
                        time.sleep(10)
                    else:
                        sys.exit(9)
                    """)
            )
        os.chmod(self.executable, 0o755)
        self.runner = git_perf_lib.GitRunner(
            self.executable,
            environ={
                "PATH": os.environ.get("PATH", ""),
                "GIT_OPTIONAL_LOCKS": "1",
                "GIT_TRACE2_EVENT": "/private/trace",
            },
        )

    def test_direct_process_receives_strict_environment(self):
        result = self.runner.run(["environment"], cwd=self.tempdir)
        self.assertTrue(result.ok)
        self.assertEqual(b"0\n0\n", result.stdout)

    def test_normal_git_process_preserves_optional_locks(self):
        result = self.runner.run(
            ["environment"],
            cwd=self.tempdir,
            effect_profile=git_perf_lib.NORMAL_GIT_EFFECT_PROFILE,
        )
        self.assertTrue(result.ok)
        self.assertEqual(b"0\n1\n", result.stdout)

    def test_output_is_drained_but_retention_is_bounded(self):
        result = self.runner.run(
            ["large"], cwd=self.tempdir, output_limit_bytes=128
        )
        self.assertTrue(result.ok)
        self.assertEqual(128, len(result.stdout))
        self.assertTrue(result.stdout_truncated)

    def test_large_stdout_and_stderr_are_drained_concurrently(self):
        result = self.runner.run(
            ["both-large"], cwd=self.tempdir, output_limit_bytes=128
        )

        self.assertTrue(result.ok)
        self.assertEqual(128, len(result.stdout))
        self.assertTrue(result.stdout_truncated)
        self.assertEqual("truncated", result.stderr_state)

    def test_output_consumer_sees_full_stream_beyond_retained_prefix(self):
        consumed = bytearray()

        result = self.runner.run(
            ["large"],
            cwd=self.tempdir,
            output_limit_bytes=128,
            stdout_consumer=consumed.extend,
        )

        self.assertTrue(result.ok)
        self.assertEqual(b"x" * 8192, consumed)
        self.assertEqual(128, len(result.stdout))
        self.assertTrue(result.stdout_truncated)
        self.assertFalse(result.stdout_consumer_failed)

    def test_output_consumer_failure_does_not_stop_pipe_drain(self):

        def fail_consumer(_chunk):
            raise ValueError("PRIVATE-CONSUMER-CANARY")

        result = self.runner.run(
            ["large"],
            cwd=self.tempdir,
            output_limit_bytes=128,
            stdout_consumer=fail_consumer,
        )

        self.assertTrue(result.ok)
        self.assertEqual(128, len(result.stdout))
        self.assertTrue(result.stdout_truncated)
        self.assertTrue(result.stdout_consumer_failed)
        self.assertNotIn("PRIVATE-CONSUMER-CANARY", repr(result))

    def test_trace_uses_supported_inherited_descriptor_range(self):
        consumed = bytearray()

        result = self.runner.run(
            ["trace-event"],
            cwd=self.tempdir,
            trace2_event_consumer=consumed.extend,
        )

        self.assertTrue(result.ok)
        self.assertGreaterEqual(int(result.stdout), 3)
        self.assertLessEqual(int(result.stdout), 9)
        self.assertEqual(b'{"event":"test"}\n', consumed)
        self.assertFalse(result.trace_consumer_failed)
        self.assertFalse(result.trace_read_failed)

    def test_trace_backpressure_is_drained(self):
        bytes_seen = 0

        def consume(chunk):
            nonlocal bytes_seen
            bytes_seen += len(chunk)

        result = self.runner.run(
            ["trace-large"],
            cwd=self.tempdir,
            trace2_event_consumer=consume,
        )

        self.assertTrue(result.ok)
        self.assertEqual(1024 * 1024, bytes_seen)

    def test_concurrent_trace_invocations_do_not_collide_descriptors(self):

        def run_one(_index):
            consumed = bytearray()
            result = self.runner.run(
                ["trace-event"],
                cwd=self.tempdir,
                trace2_event_consumer=consumed.extend,
            )
            return result, bytes(consumed)

        with futures.ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(run_one, range(8)))

        self.assertTrue(all(result.ok for result, _data in results))
        self.assertTrue(
            all(data == b'{"event":"test"}\n' for _result, data in results)
        )

    def test_multiple_process_writers_preserve_event_framing(self):
        consumed = bytearray()

        result = self.runner.run(
            ["trace-multi-writer"],
            cwd=self.tempdir,
            trace2_event_consumer=consumed.extend,
        )

        lines = bytes(consumed).splitlines()
        self.assertTrue(result.ok)
        self.assertEqual(300, len(lines))
        self.assertTrue(
            all(
                line in (b'{"writer":0}', b'{"writer":1}', b'{"writer":2}')
                for line in lines
            )
        )

    def test_trace_consumer_failure_is_classified_while_pipe_drains(self):

        def fail(_chunk):
            raise ValueError("PRIVATE-TRACE-CONSUMER")

        result = self.runner.run(
            ["trace-large"],
            cwd=self.tempdir,
            trace2_event_consumer=fail,
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.trace_consumer_failed)
        self.assertNotIn("PRIVATE-TRACE-CONSUMER", repr(result))

    def test_trace_reader_does_not_wait_for_detached_writer(self):
        with mock.patch.object(
            git_perf_lib, "PROCESS_CLEANUP_GRACE_SECONDS", 0.05
        ):
            started = time.monotonic()
            result = self.runner.run(
                ["trace-descendant"],
                cwd=self.tempdir,
                trace2_event_consumer=lambda _chunk: None,
            )
            elapsed = time.monotonic() - started

        self.assertTrue(result.ok)
        self.assertLess(elapsed, 0.8)
        self.assertTrue(result.cleanup_uncertain)

    def test_raw_stderr_is_not_returned(self):
        result = self.runner.run(["stderr"], cwd=self.tempdir)
        self.assertEqual("nonzero_exit", result.outcome)
        self.assertEqual("present", result.stderr_state)
        self.assertFalse(hasattr(result, "stderr"))
        self.assertNotIn("PRIVATE-STDERR-CANARY", repr(result))

    def test_timeout_terminates_the_foreground_group(self):
        clock_values = iter((100, 200, 500))
        runner = git_perf_lib.GitRunner(
            self.executable,
            environ={"PATH": os.environ.get("PATH", "")},
            clock_ns=lambda: next(clock_values),
        )

        result = runner.run(["sleep"], cwd=self.tempdir, timeout_seconds=0.05)
        self.assertEqual("timeout", result.outcome)
        self.assertIsNotNone(result.returncode)
        self.assertEqual(100, result.elapsed_ns)
        self.assertEqual(300, result.cleanup_elapsed_ns)

    def test_timeout_terminates_a_controllable_descendant(self):
        marker = os.path.join(self.tempdir, "escaped-descendant")

        with mock.patch.object(
            git_perf_lib, "PROCESS_CLEANUP_GRACE_SECONDS", 0.05
        ):
            result = self.runner.run(
                ["descendant", marker],
                cwd=self.tempdir,
                timeout_seconds=1.0,
            )

        time.sleep(1.2)
        self.assertEqual("timeout", result.outcome)
        self.assertFalse(os.path.exists(marker))

    def test_start_failure_is_a_path_free_result(self):
        missing = os.path.join(self.tempdir, "private-missing-git")
        runner = git_perf_lib.GitRunner(
            missing, environ={"PATH": os.environ.get("PATH", "")}
        )

        result = runner.run(["version"], cwd=self.tempdir)

        self.assertEqual("start_failed", result.outcome)
        self.assertNotIn(missing, repr(result))

    def test_interrupt_requests_cleanup_and_is_reraised(self):
        process = mock.Mock()
        process.stdout = io.BytesIO()
        process.stderr = io.BytesIO()
        process.wait.side_effect = KeyboardInterrupt

        with mock.patch("git_perf_lib.subprocess.Popen", return_value=process):
            with mock.patch.object(
                self.runner, "_terminate_process", return_value=False
            ) as terminate:
                with self.assertRaises(KeyboardInterrupt):
                    self.runner.run(["sleep"], cwd=self.tempdir)

        terminate.assert_called_once_with(process)

    def test_deadline_runner_clamps_calls_and_stops_new_work(self):
        clock = _FakeClock()
        runner = mock.Mock()
        runner.executable = os.path.abspath(git_perf_lib.__file__)
        runner.run.return_value = _result()
        bounded = git_perf_lib._DeadlineRunner(
            runner,
            deadline_ns=1_000_000_000,
            clock_ns=clock,
        )

        first = bounded.run(["version"], cwd=None, timeout_seconds=5.0)
        clock.advance(1_000_000_000)
        stopped = bounded.run(["version"], cwd=None)

        self.assertTrue(first.ok)
        self.assertEqual(1.0, runner.run.call_args.kwargs["timeout_seconds"])
        self.assertEqual("operation_deadline", stopped.outcome)
        self.assertTrue(bounded.deadline_reached)
        runner.run.assert_called_once()


class PorcelainV2StatusParserTest(unittest.TestCase):
    _HASH_A = b"a" * 40
    _HASH_B = b"b" * 40
    _HASH_C = b"c" * 40

    def _ordinary(self, xy, submodule, path):
        return b" ".join(
            (
                b"1",
                xy,
                submodule,
                b"100644",
                b"100644",
                b"100644",
                self._HASH_A,
                self._HASH_B,
                path,
            )
        )

    def _rename(self, path):
        return b" ".join(
            (
                b"2",
                b"R.",
                b"N...",
                b"100644",
                b"100644",
                b"100644",
                self._HASH_A,
                self._HASH_B,
                b"R100",
                path,
            )
        )

    def _unmerged(self, path):
        return b" ".join(
            (
                b"u",
                b"UU",
                b"N...",
                b"100644",
                b"100644",
                b"100644",
                b"100644",
                self._HASH_A,
                self._HASH_B,
                self._HASH_C,
                path,
            )
        )

    def test_counts_all_record_types_across_arbitrary_chunk_boundaries(self):
        canary = b"PRIVATE-PATH-\xff-CANARY"
        stream = b"\0".join(
            (
                b"# branch.head PRIVATE-BRANCH-CANARY",
                self._ordinary(b"M.", b"N...", canary + b" staged"),
                self._ordinary(b".M", b"S.M.", canary + b" unstaged"),
                self._rename(canary + b" renamed target"),
                b"? looks-like-an-untracked-record-but-is-the-original-path",
                self._unmerged(canary + b" unmerged"),
                b"? " + canary + b" untracked",
                b"! " + canary + b" ignored",
                b"",
            )
        )
        parser = git_perf_lib.PorcelainV2StatusParser(
            byte_limit=len(stream), record_limit=4096
        )

        for byte in stream:
            parser.consume(bytes((byte,)))
        result = parser.finish()

        self.assertEqual("complete", result.state)
        self.assertEqual(len(stream), result.bytes_seen)
        self.assertEqual(7, result.records_seen)
        self.assertEqual(
            git_perf_lib.StatusCounts(
                ordinary=2,
                renamed_or_copied=1,
                unmerged=1,
                untracked=1,
                ignored=1,
                staged=2,
                unstaged=1,
                submodule_changed=1,
                branch_headers=1,
            ),
            result.counts,
        )
        self.assertNotIn("PRIVATE", repr(result))
        self.assertNotIn("CANARY", repr(result))
        self.assertNotIn("PRIVATE", repr(parser.__dict__))

    def test_branch_headers_have_safe_counts_and_keyed_drift_identity(self):
        key = b"k" * 32

        def parse(head, *, stability_key=key):
            parser = git_perf_lib.PorcelainV2StatusParser(
                byte_limit=4096,
                record_limit=4096,
                stability_key=stability_key,
            )
            parser.consume(
                b"# branch.oid "
                + self._HASH_A
                + b"\0# branch.head "
                + head
                + b"\0# branch.ab +12 -3\0"
            )
            return parser.finish()

        first = parse(b"PRIVATE-BRANCH-CANARY")
        same = parse(b"PRIVATE-BRANCH-CANARY")
        changed = parse(b"PRIVATE-OTHER-BRANCH")
        differently_keyed = parse(
            b"PRIVATE-BRANCH-CANARY", stability_key=b"z" * 32
        )

        self.assertEqual("complete", first.state)
        self.assertEqual(3, first.counts.branch_headers)
        self.assertEqual(12, first.counts.ahead)
        self.assertEqual(3, first.counts.behind)
        self.assertEqual(first.status_fingerprint, same.status_fingerprint)
        self.assertNotEqual(
            first.status_fingerprint, changed.status_fingerprint
        )
        self.assertNotEqual(
            first.status_fingerprint, differently_keyed.status_fingerprint
        )
        self.assertNotIn("PRIVATE", repr(first))

    def test_status_fingerprint_detects_path_drift_without_retaining_paths(
        self,
    ):

        def parse(path):
            parser = git_perf_lib.PorcelainV2StatusParser(
                byte_limit=4096,
                record_limit=4096,
                stability_key=b"k" * 32,
            )
            parser.consume(b"? " + path + b"\0")
            return parser.finish()

        first = parse(b"PRIVATE-FIRST-PATH")
        same = parse(b"PRIVATE-FIRST-PATH")
        changed = parse(b"PRIVATE-SECOND-PATH")

        self.assertEqual(first.counts, changed.counts)
        self.assertEqual(first.status_fingerprint, same.status_fingerprint)
        self.assertNotEqual(
            first.status_fingerprint, changed.status_fingerprint
        )
        self.assertNotIn("PRIVATE", repr(first))
        self.assertNotIn("PRIVATE", repr(changed))

    def test_malformed_branch_ahead_behind_is_unavailable(self):
        parser = git_perf_lib.PorcelainV2StatusParser(
            byte_limit=1024, record_limit=1024
        )
        parser.consume(b"# branch.ab +1 PRIVATE-CANARY\0")

        result = parser.finish()

        self.assertEqual("unavailable", result.state)
        self.assertEqual("malformed_record", result.reason)
        self.assertNotIn("PRIVATE", repr(result))

    def test_clean_empty_stream_is_complete(self):
        parser = git_perf_lib.PorcelainV2StatusParser(
            byte_limit=1, record_limit=1
        )

        result = parser.finish()

        self.assertEqual("complete", result.state)
        self.assertEqual(
            git_perf_lib.StatusCounts(0, 0, 0, 0, 0, 0, 0, 0),
            result.counts,
        )

    def test_unknown_and_malformed_records_make_counts_unavailable(self):
        streams = (
            (b"x PRIVATE-UNKNOWN\0", "unknown_or_malformed_record"),
            (b"1 malformed PRIVATE-PATH\0", "malformed_record"),
            (b"\0", "malformed_record"),
        )
        for stream, reason in streams:
            with self.subTest(reason=reason, stream=stream[:1]):
                parser = git_perf_lib.PorcelainV2StatusParser(
                    byte_limit=1024, record_limit=1024
                )
                parser.consume(stream)

                result = parser.finish()

                self.assertEqual("unavailable", result.state)
                self.assertIsNone(result.counts)
                self.assertEqual(reason, result.reason)
                self.assertNotIn("PRIVATE", repr(result))

    def test_parse_byte_limit_discards_remaining_input(self):
        stream = self._ordinary(b"M.", b"N...", b"PRIVATE-PATH") + b"\0"
        parser = git_perf_lib.PorcelainV2StatusParser(
            byte_limit=len(stream) - 1, record_limit=1024
        )

        parser.consume(stream)
        parser.consume(b"PRIVATE-AFTER-LIMIT")
        result = parser.finish()

        self.assertEqual("unavailable", result.state)
        self.assertEqual("parse_byte_limit_exceeded", result.reason)
        self.assertEqual(
            len(stream) + len(b"PRIVATE-AFTER-LIMIT"), result.bytes_seen
        )
        self.assertNotIn("PRIVATE", repr(result))

    def test_record_limit_bounds_an_unterminated_path(self):
        parser = git_perf_lib.PorcelainV2StatusParser(
            byte_limit=1024, record_limit=8
        )

        parser.consume(b"? PRIVATE-PATH-WITHOUT-A-TERMINATOR")
        result = parser.finish()

        self.assertEqual("unavailable", result.state)
        self.assertEqual("record_byte_limit_exceeded", result.reason)
        self.assertNotIn("PRIVATE", repr(result))

    def test_oversized_similarity_score_is_malformed_without_integer_parsing(
        self,
    ):
        record = self._rename(b"PRIVATE-TARGET").replace(
            b"R100", b"R" + b"9" * 5000
        )
        parser = git_perf_lib.PorcelainV2StatusParser(
            byte_limit=10_000, record_limit=10_000
        )

        parser.consume(record + b"\0PRIVATE-ORIGINAL\0")
        result = parser.finish()

        self.assertEqual("unavailable", result.state)
        self.assertEqual("malformed_record", result.reason)
        self.assertNotIn("PRIVATE", repr(result))

    def test_missing_rename_original_path_is_truncated(self):
        stream = self._rename(b"PRIVATE-TARGET") + b"\0"
        parser = git_perf_lib.PorcelainV2StatusParser(
            byte_limit=1024, record_limit=1024
        )
        parser.consume(stream)

        result = parser.finish()

        self.assertEqual("unavailable", result.state)
        self.assertEqual("truncated_record", result.reason)
        self.assertIsNone(result.counts)

    def test_finish_is_idempotent_and_rejects_late_chunks(self):
        parser = git_perf_lib.PorcelainV2StatusParser(
            byte_limit=1, record_limit=1
        )

        first = parser.finish()

        self.assertIs(first, parser.finish())
        with self.assertRaisesRegex(RuntimeError, "already finished"):
            parser.consume(b"x")


class BenchmarkCollectionTest(unittest.TestCase):
    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    def test_ambiguous_partial_clone_state_blocks_effectful_workload(
        self, _is_cog
    ):
        clock = _FakeClock()
        runner = _BenchmarkRunner(
            clock,
            [_timed_result(100)],
            partial_results=[_result(b"remote.PRIVATE.promisor\nnot-a-bool\0")],
        )

        report = git_perf_lib.collect_benchmark(
            cwd="/checkout",
            environ={},
            samples=1,
            warmup=0,
            deadline_seconds=10,
            runner=runner,
            clock_ns=clock,
        )

        self.assertEqual("failed", report.status)
        self.assertEqual([], report.measurements)
        self.assertEqual(
            "unavailable", _fact(report, "repository.partial_clone").state
        )
        self.assertFalse(
            any(
                args == git_perf_lib.STATUS_WORKLOAD_ARGS
                for args, _kwargs in runner.calls
            )
        )
        self.assertNotIn("PRIVATE", git_perf_lib.report_json(report))

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    def test_orders_runs_and_reports_median_and_raw_mad(self, _is_cog):
        clock = _FakeClock()
        runner = _BenchmarkRunner(
            clock,
            [
                _timed_result(900),
                _timed_result(800),
                _timed_result(100),
                _timed_result(300),
                _timed_result(200),
            ],
        )

        report = git_perf_lib.collect_benchmark(
            cwd="/checkout",
            environ={"PATH": "/bin"},
            samples=3,
            warmup=1,
            deadline_seconds=10,
            runner=runner,
            clock_ns=clock,
        )

        self.assertEqual("complete", report.status)
        measurement = report.measurements[0]
        self.assertEqual(
            [
                "first_after_preflight",
                "warmup",
                "measured",
                "measured",
                "measured",
            ],
            [item["role"] for item in measurement["executions"]],
        )
        self.assertEqual(200, measurement["summary"]["median_ns"])
        self.assertEqual(
            {"kind": "raw_mad", "value_ns": 100},
            measurement["summary"]["spread"],
        )
        self.assertFalse(measurement["executions"][1]["included_in_summary"])
        self.assertEqual(
            [True, True, True],
            [
                item["included_in_summary"]
                for item in measurement["executions"][2:]
            ],
        )
        status_calls = [
            kwargs
            for args, kwargs in runner.calls
            if args == git_perf_lib.STATUS_WORKLOAD_ARGS
        ]
        self.assertEqual(5, len(status_calls))
        self.assertTrue(
            all(
                call["effect_profile"] == git_perf_lib.NORMAL_GIT_EFFECT_PROFILE
                for call in status_calls
            )
        )
        serialized = git_perf_lib.report_json(report)
        self.assertNotIn("PRIVATE-PATH", serialized)
        self.assertNotIn("PRIVATE-CONFIG-CANARY", serialized)
        self.assertNotIn("/checkout", serialized)
        rendered = git_perf_lib.render_human(report)
        flat_rendered = " ".join(rendered.split())
        self.assertIn("Git status median", rendered)
        self.assertIn("Median absolute deviation between runs", rendered)
        self.assertIn("optional caches", rendered)
        self.assertIn("git-perf did not edit Git configuration", flat_rendered)
        self.assertIn("A configured hook could", flat_rendered)
        self.assertNotIn("PRIVATE", rendered)

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    def test_config_drift_suppresses_homogeneous_summary(self, _is_cog):
        clock = _FakeClock()
        runner = _BenchmarkRunner(
            clock,
            [_timed_result(100), _timed_result(90)],
            config_results=[
                _result(b"status.aheadBehind\ntrue\0"),
                _result(b"status.aheadBehind\nfalse\0"),
            ],
        )

        report = git_perf_lib.collect_benchmark(
            cwd="/checkout",
            environ={},
            samples=1,
            warmup=0,
            deadline_seconds=10,
            runner=runner,
            clock_ns=clock,
        )

        measurement = report.measurements[0]
        self.assertEqual("partial", report.status)
        self.assertIsNone(measurement["summary"])
        self.assertEqual(
            "observed_changed",
            measurement["quality"]["config_stability"],
        )

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    def test_count_drift_suppresses_homogeneous_summary(self, _is_cog):
        clock = _FakeClock()
        runner = _BenchmarkRunner(
            clock,
            [
                _timed_result(100),
                _timed_result(90, b"# branch.head main\0"),
            ],
        )

        report = git_perf_lib.collect_benchmark(
            cwd="/checkout",
            environ={},
            samples=1,
            warmup=0,
            deadline_seconds=10,
            runner=runner,
            clock_ns=clock,
        )

        measurement = report.measurements[0]
        self.assertEqual("partial", report.status)
        self.assertIsNone(measurement["summary"])
        self.assertEqual(
            "observed_changed",
            measurement["quality"]["worktree_state_stability"],
        )

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    def test_private_branch_header_drift_suppresses_summary(self, _is_cog):
        clock = _FakeClock()
        runner = _BenchmarkRunner(
            clock,
            [
                _timed_result(100, b"# branch.head PRIVATE-FIRST\0"),
                _timed_result(90, b"# branch.head PRIVATE-SECOND\0"),
            ],
        )

        report = git_perf_lib.collect_benchmark(
            cwd="/checkout",
            environ={},
            samples=1,
            warmup=0,
            deadline_seconds=10,
            runner=runner,
            clock_ns=clock,
        )

        measurement = report.measurements[0]
        self.assertEqual("partial", report.status)
        self.assertIsNone(measurement["summary"])
        self.assertEqual(
            "observed_changed",
            measurement["quality"]["worktree_state_stability"],
        )
        self.assertNotIn("PRIVATE", git_perf_lib.report_json(report))

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    def test_private_path_drift_suppresses_summary(self, _is_cog):
        clock = _FakeClock()
        runner = _BenchmarkRunner(
            clock,
            [
                _timed_result(100, b"? PRIVATE-FIRST-PATH\0"),
                _timed_result(90, b"? PRIVATE-SECOND-PATH\0"),
            ],
        )

        report = git_perf_lib.collect_benchmark(
            cwd="/checkout",
            environ={},
            samples=1,
            warmup=0,
            deadline_seconds=10,
            runner=runner,
            clock_ns=clock,
        )

        measurement = report.measurements[0]
        self.assertEqual("partial", report.status)
        self.assertIsNone(measurement["summary"])
        self.assertEqual(
            "observed_changed",
            measurement["quality"]["worktree_state_stability"],
        )
        self.assertNotIn("PRIVATE", git_perf_lib.report_json(report))

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    def test_deadline_stops_before_scheduling_a_measured_run(self, _is_cog):
        clock = _FakeClock()
        runner = _BenchmarkRunner(
            clock,
            [_timed_result(400_000_000), _timed_result(400_000_000)],
        )

        report = git_perf_lib.collect_benchmark(
            cwd="/checkout",
            environ={},
            samples=3,
            warmup=1,
            deadline_seconds=1,
            runner=runner,
            clock_ns=clock,
        )

        measurement = report.measurements[0]
        self.assertEqual("partial", report.status)
        self.assertEqual("limited", measurement["deadline"]["state"])
        self.assertEqual(
            ["first_after_preflight", "warmup"],
            [item["role"] for item in measurement["executions"]],
        )
        self.assertIsNone(measurement["summary"])

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    def test_failed_first_workload_fails_the_measurement(self, _is_cog):
        clock = _FakeClock()
        timeout = git_perf_lib.GitResult(
            outcome="timeout",
            returncode=-15,
            stdout=b"",
            stdout_truncated=False,
            stderr_state="empty",
            elapsed_ns=100,
        )
        runner = _BenchmarkRunner(clock, [timeout])

        report = git_perf_lib.collect_benchmark(
            cwd="/checkout",
            environ={},
            samples=1,
            warmup=0,
            deadline_seconds=10,
            runner=runner,
            clock_ns=clock,
        )

        self.assertEqual("failed", report.status)
        self.assertEqual(
            "failed", report.measurements[0]["quality"]["outcome_validity"]
        )
        self.assertEqual("benchmark.first_failed", report.diagnostics[0].id)

    def test_rejects_out_of_range_controls(self):
        for kwargs in (
            {"samples": 0},
            {"warmup": -1},
            {"deadline_seconds": 0},
            {"samples": True},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    git_perf_lib.collect_benchmark(
                        cwd="/checkout", environ={}, **kwargs
                    )


class GitVersionTest(unittest.TestCase):
    def test_parses_numeric_components_and_vendor_suffixes(self):
        for text, expected in (
            ("git version 2.46.0\n", (2, 46, 0)),
            ("git version 2.46.0.windows.1\n", (2, 46, 0)),
            ("git version 2.39.5 (Apple Git-154)\n", (2, 39, 5)),
            ("git version 2.46.0.123.gabcdef\n", (2, 46, 0, 123)),
            ("unrecognized version", ()),
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    expected, git_perf_lib._parse_git_version(text)
                )


class IndexReaderTest(unittest.TestCase):
    def setUp(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.index_path = os.path.join(tempdir.name, "index")

    def test_reads_only_the_index_header(self):
        with open(self.index_path, "wb") as index_file:
            index_file.write(
                b"DIRC" + (4).to_bytes(4, "big") + (17).to_bytes(4, "big")
            )
            index_file.write(b"private-path-canary")
        state, value, limitations = git_perf_lib.read_index_header(
            self.index_path
        )

        self.assertEqual("known", state)
        self.assertEqual(4, value["format_version"])
        self.assertEqual(17, value["entry_count"])
        self.assertTrue(limitations)
        self.assertNotIn("private-path-canary", repr(value))

    def test_missing_index_is_unset(self):
        state, value, limitations = git_perf_lib.read_index_header(
            "/path/that/does/not/exist"
        )
        self.assertEqual("unset", state)
        self.assertEqual({"present": False}, value)
        self.assertFalse(limitations)

    def test_malformed_index_is_unavailable_not_corrupt(self):
        with open(self.index_path, "wb") as index_file:
            index_file.write(b"not an index")
        state, value, limitations = git_perf_lib.read_index_header(
            self.index_path
        )
        self.assertEqual("unavailable", state)
        self.assertIsNone(value)
        self.assertIn("index_header_unrecognized", limitations)

    def test_path_replacement_after_read_is_unavailable(self):
        with open(self.index_path, "wb") as index_file:
            index_file.write(
                b"DIRC" + (4).to_bytes(4, "big") + (1).to_bytes(4, "big")
            )
        initial = os.lstat(self.index_path)
        changed = mock.Mock(
            st_dev=initial.st_dev,
            st_ino=initial.st_ino + 1,
            st_size=initial.st_size,
            st_mtime_ns=initial.st_mtime_ns,
        )
        with mock.patch(
            "git_perf_lib.os.lstat", side_effect=(initial, changed)
        ):
            state, value, limitations = git_perf_lib.read_index_header(
                self.index_path
            )

        self.assertEqual("unavailable", state)
        self.assertIsNone(value)
        self.assertIn("index_changed_during_read", limitations)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFO support")
    def test_special_index_file_is_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = os.path.join(tempdir, "index-fifo")
            os.mkfifo(path)
            state, value, limitations = git_perf_lib.read_index_header(path)
        self.assertEqual("unavailable", state)
        self.assertIsNone(value)
        self.assertIn("index_is_not_a_regular_file", limitations)


class CollectionTest(unittest.TestCase):
    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    @mock.patch("git_perf_lib.gclient_paths.FindGclientRoot", return_value=None)
    def test_collects_typed_facts_without_running_status(
        self, _find_gclient, _is_cog
    ):
        runner = _FakeRunner(_standard_responses())
        redirect_canary = "/private/GIT-DIR-CANARY"
        report = git_perf_lib.collect_check(
            cwd="/checkout",
            environ={"PATH": "/bin", "GIT_DIR": redirect_canary},
            runner=runner,
        )

        self.assertEqual("complete", report.status)
        self.assertEqual(
            "2.55.0", _fact(report, "git.selected.version").value["display"]
        )
        self.assertEqual(
            "main_worktree", _fact(report, "repository.kind").value
        )
        self.assertFalse(_fact(report, "repository.partial_clone").value)
        commands = [call[0] for call in runner.calls]
        self.assertFalse(any("status" in command for command in commands))
        self.assertFalse(any("diff" in command for command in commands))
        self.assertTrue(
            all(
                call[1]["effect_profile"] == git_perf_lib.STRICT_EFFECT_PROFILE
                for call in runner.calls
            )
        )
        serialized = git_perf_lib.report_json(report)
        self.assertNotIn(redirect_canary, serialized)
        self.assertNotIn("/checkout", serialized)
        self.assertEqual(
            "present",
            _fact(report, "environment.git_redirects").value["git_dir"],
        )
        fact_ids = [fact.id for fact in report.facts]
        self.assertEqual(len(fact_ids), len(set(fact_ids)))
        self.assertTrue(
            all(
                fact.state
                in {"known", "unset", "unsupported", "unavailable", "failed"}
                for fact in report.facts
            )
        )

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    @mock.patch("git_perf_lib.gclient_paths.FindGclientRoot", return_value=None)
    def test_unrecognized_version_is_an_explicit_partial_fact(
        self, _find_gclient, _is_cog
    ):
        responses = _standard_responses()
        responses[("version",)] = _result(b"git version vendor-mystery\n")
        report = git_perf_lib.collect_check(
            cwd="/checkout",
            environ={"PATH": "/bin"},
            runner=_FakeRunner(responses),
        )

        self.assertEqual("partial", report.status)
        self.assertEqual(
            "unavailable", _fact(report, "git.selected.version").state
        )
        self.assertIn(
            "git.version_unavailable", {item.id for item in report.diagnostics}
        )

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    @mock.patch("git_perf_lib.gclient_paths.FindGclientRoot", return_value=None)
    def test_config_values_and_origins_are_reduced_before_report(
        self, _find_gclient, _is_cog
    ):
        canary = b"PRIVATE-FSMONITOR-HOOK-CANARY"
        scoped = b"global\0file:/private/config\0" + canary + b"\0"
        runner = _FakeRunner(_standard_responses(fsmonitor=scoped))

        report = git_perf_lib.collect_check(
            cwd="/checkout", environ={"PATH": "/bin"}, runner=runner
        )
        serialized = git_perf_lib.report_json(report)

        effective = _fact(report, "config.core_fsmonitor.effective")
        self.assertEqual(
            {"mode": "external_hook", "scope": "global"}, effective.value
        )
        self.assertIn(
            "config.fsmonitor_broad_scope",
            {finding.id for finding in report.findings},
        )
        self.assertNotIn(canary.decode(), serialized)
        self.assertNotIn("/private/config", serialized)

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    @mock.patch("git_perf_lib.gclient_paths.FindGclientRoot", return_value=None)
    def test_boolean_true_is_not_claimed_as_builtin(
        self, _find_gclient, _is_cog
    ):
        runner = _FakeRunner(
            _standard_responses(fsmonitor=b"local\0file:.git/config\0true\0")
        )

        report = git_perf_lib.collect_check(
            cwd="/checkout", environ={"PATH": "/bin"}, runner=runner
        )

        effective = _fact(report, "config.core_fsmonitor.effective")
        self.assertEqual(
            {"mode": "boolean_true", "scope": "local"}, effective.value
        )
        self.assertIn(
            "boolean_true_semantics_require_a_platform_capability_probe",
            effective.limitations,
        )

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    @mock.patch("git_perf_lib.gclient_paths.FindGclientRoot", return_value=None)
    def test_empty_fsmonitor_value_remains_semantically_ambiguous(
        self, _find_gclient, _is_cog
    ):
        runner = _FakeRunner(
            _standard_responses(fsmonitor=b"local\0file:.git/config\0\0")
        )

        report = git_perf_lib.collect_check(
            cwd="/checkout", environ={"PATH": "/bin"}, runner=runner
        )

        self.assertEqual(
            {"mode": "empty_or_implicit_boolean", "scope": "local"},
            _fact(report, "config.core_fsmonitor.effective").value,
        )

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    @mock.patch("git_perf_lib.gclient_paths.FindGclientRoot", return_value=None)
    def test_old_git_scope_fallback_still_discards_hook_value(
        self, _find_gclient, _is_cog
    ):
        canary = b"PRIVATE-OLD-GIT-HOOK-CANARY"
        responses = _standard_responses()
        responses[
            (
                "config",
                "--null",
                "--show-origin",
                "--show-scope",
                "--get-all",
                "core.fsmonitor",
            )
        ] = _result(returncode=129)
        responses[("config", "--null", "--get-all", "core.fsmonitor")] = (
            _result(canary + b"\0")
        )

        report = git_perf_lib.collect_check(
            cwd="/checkout",
            environ={"PATH": "/bin"},
            runner=_FakeRunner(responses),
        )

        self.assertEqual(
            "unsupported",
            _fact(report, "config.core_fsmonitor.assignments").state,
        )
        self.assertEqual(
            {"mode": "external_hook", "scope": "unknown"},
            _fact(report, "config.core_fsmonitor.effective").value,
        )
        self.assertNotIn(canary.decode(), git_perf_lib.report_json(report))

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    @mock.patch("git_perf_lib.gclient_paths.FindGclientRoot", return_value=None)
    def test_partial_clone_remote_identity_is_not_retained(
        self, _find_gclient, _is_cog
    ):
        canary = b"PRIVATE-PROMISOR-REMOTE-CANARY"
        responses = _standard_responses()
        responses[
            (
                "config",
                "--null",
                "--get-regexp",
                r"^(extensions\.partialclone|remote\..*\.promisor)$",
            )
        ] = _result(b"extensions.partialclone\n" + canary + b"\0")

        report = git_perf_lib.collect_check(
            cwd="/checkout",
            environ={"PATH": "/bin"},
            runner=_FakeRunner(responses),
        )

        self.assertTrue(_fact(report, "repository.partial_clone").value)
        self.assertNotIn(canary.decode(), git_perf_lib.report_json(report))

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    @mock.patch("git_perf_lib.gclient_paths.FindGclientRoot", return_value=None)
    def test_malformed_promisor_boolean_is_unavailable_not_false(
        self, _find_gclient, _is_cog
    ):
        canary = b"PRIVATE-MALFORMED-PROMISOR"
        responses = _standard_responses()
        responses[
            (
                "config",
                "--null",
                "--get-regexp",
                r"^(extensions\.partialclone|remote\..*\.promisor)$",
            )
        ] = _result(b"remote." + canary + b".promisor\nnot-a-bool\0")

        report = git_perf_lib.collect_check(
            cwd="/checkout",
            environ={"PATH": "/bin"},
            runner=_FakeRunner(responses),
        )

        fact = _fact(report, "repository.partial_clone")
        self.assertEqual("unavailable", fact.state)
        self.assertIsNone(fact.value)
        self.assertNotIn(canary.decode(), git_perf_lib.report_json(report))

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    @mock.patch(
        "git_perf_lib.gclient_paths.GetGClientPrimarySolutionName",
        return_value="src",
    )
    @mock.patch(
        "git_perf_lib.gclient_paths.FindGclientRoot", return_value="/checkout"
    )
    def test_reuses_gclient_paths_for_primary_solution_context(
        self, find_gclient, primary_name, _is_cog
    ):
        runner = _FakeRunner(_standard_responses(root="/checkout/src"))
        report = git_perf_lib.collect_check(
            cwd="/checkout/src", environ={"PATH": "/bin"}, runner=runner
        )

        self.assertEqual(
            "gclient", _fact(report, "checkout.orchestrator").value
        )
        self.assertEqual(
            "primary_solution", _fact(report, "checkout.repository_role").value
        )
        find_gclient.assert_called_once_with(os.path.normpath("/checkout/src"))
        primary_name.assert_called_once_with("/checkout")

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    @mock.patch("git_perf_lib.gclient_paths.FindGclientRoot", return_value=None)
    def test_linked_worktree_count_has_shared_scope_finding(
        self, _find_gclient, _is_cog
    ):
        runner = _FakeRunner(
            _standard_responses(
                git_dir="/checkout/.git/worktrees/linked",
                common_dir="/checkout/.git",
                worktrees=3,
            )
        )
        report = git_perf_lib.collect_check(
            cwd="/checkout", environ={"PATH": "/bin"}, runner=runner
        )

        self.assertEqual(
            "linked_worktree", _fact(report, "repository.kind").value
        )
        self.assertEqual(
            {"registered": 3, "additional_linked": 2},
            _fact(report, "repository.worktrees").value,
        )
        self.assertIn(
            "repository.local_scope_shared",
            {finding.id for finding in report.findings},
        )

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    def test_non_repository_produces_failed_report(self, _is_cog):
        runner = _FakeRunner(
            {
                ("version",): _result(b"git version 2.55.0\n"),
                (
                    "rev-parse",
                    "--is-bare-repository",
                    "--is-inside-work-tree",
                    "--git-dir",
                    "--git-common-dir",
                ): _result(returncode=128),
            }
        )
        report = git_perf_lib.collect_check(
            cwd="/not-a-repository", environ={"PATH": "/bin"}, runner=runner
        )
        self.assertEqual("failed", report.status)
        self.assertEqual("repository.not_found", report.diagnostics[0].id)

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    def test_non_git_environment_launches_no_child(self, _is_cog):
        _is_cog.return_value = True
        runner = _FakeRunner({})
        report = git_perf_lib.collect_check(
            cwd="/checkout", environ={}, runner=runner
        )
        self.assertEqual("failed", report.status)
        self.assertEqual([], runner.calls)

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    def test_missing_selected_git_is_a_failed_report(self, _is_cog):
        report = git_perf_lib.collect_check(
            cwd="/checkout",
            environ={"PATH": ""},
            git_executable="/does/not/exist/git",
        )
        self.assertEqual("failed", report.status)
        self.assertEqual("git.not_found", report.diagnostics[0].id)


class ActualGitStatusParserTest(git_test_utils.GitRepoReadWriteTestBase):
    REPO_SCHEMA = "A"
    COMMIT_A = {
        ".gitignore": {"data": b"PRIVATE-IGNORED-*\n"},
        "rename-source.txt": {"data": b"rename me\n"},
        "tracked.txt": {"data": b"original\n"},
    }

    def _environment(self):
        environment = os.environ.copy()
        environment["GIT_CONFIG_NOSYSTEM"] = "1"
        environment["GIT_CONFIG_GLOBAL"] = os.devnull
        return environment

    def _parse_status(self, *extra_args):
        environment = self._environment()
        runner = git_perf_lib.GitRunner.resolve(environ=environment)
        self.assertIsNotNone(runner)
        parser = git_perf_lib.PorcelainV2StatusParser(
            byte_limit=1024 * 1024, record_limit=64 * 1024
        )

        process_result = runner.run(
            ["status", "--porcelain=v2", "-z", "--branch", *extra_args],
            cwd=self.repo.repo_path,
            effect_profile=git_perf_lib.NORMAL_GIT_EFFECT_PROFILE,
            output_limit_bytes=0,
            stdout_consumer=parser.consume,
        )
        parse_result = parser.finish()

        self.assertTrue(process_result.ok)
        self.assertFalse(process_result.stdout_consumer_failed)
        self.assertEqual(b"", process_result.stdout)
        self.assertEqual("complete", parse_result.state)
        return parse_result

    def test_exact_workload_counts_state_without_retaining_paths(self):
        self.repo.git("config", "status.renames", "true")
        self.repo.git("config", "status.showUntrackedFiles", "all")
        with self.repo.open("tracked.txt", "wb") as tracked:
            tracked.write(b"modified\n")
        with self.repo.open("PRIVATE-STAGED-CANARY.txt", "wb") as staged:
            staged.write(b"staged\n")
        self.repo.git("add", "PRIVATE-STAGED-CANARY.txt")
        self.repo.git("mv", "rename-source.txt", "PRIVATE-RENAMED-CANARY.txt")
        with self.repo.open("PRIVATE-UNTRACKED-CANARY.txt", "wb") as untracked:
            untracked.write(b"untracked\n")
        with self.repo.open("PRIVATE-IGNORED-CANARY", "wb") as ignored:
            ignored.write(b"ignored\n")

        result = self._parse_status()

        self.assertEqual(
            git_perf_lib.StatusCounts(
                ordinary=2,
                renamed_or_copied=1,
                unmerged=0,
                untracked=1,
                ignored=0,
                staged=2,
                unstaged=1,
                submodule_changed=0,
                branch_headers=2,
            ),
            result.counts,
        )
        self.assertNotIn("PRIVATE", repr(result))
        self.assertNotIn(self.repo.repo_path, repr(result))

    def test_ignored_records_are_counted_when_requested(self):
        with self.repo.open("PRIVATE-IGNORED-CANARY", "wb") as ignored:
            ignored.write(b"ignored\n")

        result = self._parse_status("--ignored")

        self.assertEqual(1, result.counts.ignored)
        self.assertNotIn("PRIVATE", repr(result))

    @unittest.skipIf(sys.platform == "win32", "uses a POSIX byte pathname")
    def test_non_utf8_path_is_counted_without_decoding(self):
        canary = b"PRIVATE-NON-UTF8-\xff-CANARY"
        path = os.path.join(os.fsencode(self.repo.repo_path), canary)
        try:
            with open(path, "wb") as untracked:
                untracked.write(b"untracked\n")
        except OSError as error:
            self.skipTest(
                "filesystem rejected a non-UTF-8 fixture (errno %s)"
                % error.errno
            )

        result = self._parse_status()

        self.assertEqual(1, result.counts.untracked)
        self.assertNotIn("PRIVATE", repr(result))


class ActualGitBenchmarkTest(git_test_utils.GitRepoReadWriteTestBase):
    REPO_SCHEMA = "A"

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    def test_benchmark_runs_exact_workload_without_retaining_paths(
        self, _is_cog
    ):
        with self.repo.open("PRIVATE-BENCHMARK-CANARY", "wb") as untracked:
            untracked.write(b"untracked\n")
        trace_target = os.path.join(
            self.repo.repo_path, "PRIVATE-AMBIENT-TRACE-CANARY"
        )
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TRACE2_EVENT": trace_target,
            }
        )

        report = git_perf_lib.collect_benchmark(
            cwd=self.repo.repo_path,
            environ=environment,
            samples=1,
            warmup=0,
            deadline_seconds=20,
        )

        self.assertEqual("complete", report.status)
        measurement = report.measurements[0]
        self.assertEqual(
            ["first_after_preflight", "measured"],
            [item["role"] for item in measurement["executions"]],
        )
        self.assertEqual(
            1,
            measurement["executions"][0]["status"]["counts"]["untracked"],
        )
        self.assertIsNotNone(measurement["summary"])
        serialized = git_perf_lib.report_json(report)
        self.assertNotIn("PRIVATE-BENCHMARK-CANARY", serialized)
        self.assertNotIn("PRIVATE-AMBIENT-TRACE-CANARY", serialized)
        self.assertNotIn(self.repo.repo_path, serialized)
        self.assertFalse(os.path.exists(trace_target))

    @unittest.skipIf(sys.platform == "win32", "uses a POSIX hook fixture")
    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    def test_effectful_benchmark_discloses_and_runs_external_fsmonitor_hook(
        self, _is_cog
    ):
        hook_path = os.path.join(self.repo.repo_path, "PRIVATE-FSMONITOR-HOOK")
        marker = os.path.join(self.repo.repo_path, "PRIVATE-HOOK-RAN")
        with open(hook_path, "w", encoding="utf-8") as hook:
            hook.write(
                "#!/bin/sh\n: > \"$GIT_PERF_TEST_MARKER\"\nprintf 'token\\0'\n"
            )
        os.chmod(hook_path, 0o755)
        self.repo.git("config", "core.fsmonitor", hook_path)
        self.repo.git("config", "core.fsmonitorHookVersion", "2")
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_PERF_TEST_MARKER": marker,
            }
        )
        progress = []

        report = git_perf_lib.collect_benchmark(
            cwd=self.repo.repo_path,
            environ=environment,
            samples=1,
            warmup=0,
            deadline_seconds=20,
            progress=progress.append,
        )

        self.assertEqual("complete", report.status)
        self.assertTrue(os.path.exists(marker))
        self.assertEqual(
            "external_hook",
            _fact(report, "workload.effects.fsmonitor").value,
        )
        self.assertIn("A hook can make any change allowed to it", progress[0])
        self.assertNotIn("PRIVATE", git_perf_lib.report_json(report))


class ActualGitCheckTest(git_test_utils.GitRepoReadWriteTestBase):
    REPO_SCHEMA = "A"

    def _environment(self):
        environment = os.environ.copy()
        environment["GIT_CONFIG_NOSYSTEM"] = "1"
        environment["GIT_CONFIG_GLOBAL"] = os.devnull
        return environment

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    def test_strict_check_preserves_repository_and_blocks_ambient_trace(
        self, _is_cog
    ):
        canary = "PRIVATE-FSMONITOR-%s" % self._testMethodName
        self.repo.git("config", "core.fsmonitor", canary)
        with self.repo.open("untracked canary", "wb") as worktree_file:
            worktree_file.write(b"unchanged")

        git_dir = os.path.join(self.repo.repo_path, ".git")
        with open(os.path.join(git_dir, "index"), "rb") as index_file:
            index_before = index_file.read()
        with open(os.path.join(git_dir, "config"), "rb") as config_file:
            config_before = config_file.read()
        refs_before = self.repo.git("show-ref").stdout

        trace_target = os.path.join(self.repo.repo_path, "ambient trace canary")
        environment = self._environment()
        environment.update(
            {
                "GIT_TRACE": trace_target,
                "GIT_TRACE2_EVENT": trace_target,
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "trace2.eventTarget",
                "GIT_CONFIG_VALUE_0": trace_target,
            }
        )
        report = git_perf_lib.collect_check(
            cwd=self.repo.repo_path, environ=environment
        )

        self.assertIn(report.status, ("complete", "partial"))
        self.assertFalse(os.path.exists(trace_target))
        with open(os.path.join(git_dir, "index"), "rb") as index_file:
            self.assertEqual(index_before, index_file.read())
        with open(os.path.join(git_dir, "config"), "rb") as config_file:
            self.assertEqual(config_before, config_file.read())
        self.assertEqual(refs_before, self.repo.git("show-ref").stdout)
        with self.repo.open("untracked canary", "rb") as worktree_file:
            self.assertEqual(b"unchanged", worktree_file.read())

        serialized = git_perf_lib.report_json(report)
        self.assertNotIn(canary, serialized)
        self.assertNotIn(trace_target, serialized)
        self.assertEqual(
            "external_hook",
            _fact(report, "config.core_fsmonitor.effective").value["mode"],
        )

    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    def test_linked_worktree_with_spaces_and_unicode(self, _is_cog):
        parent = tempfile.mkdtemp(prefix="git perf worktree ")
        linked = os.path.join(parent, "linked ü worktree")
        result = self.repo.git("worktree", "add", "--detach", linked)
        self.assertEqual(0, result.retcode)
        try:
            report = git_perf_lib.collect_check(
                cwd=linked, environ=self._environment()
            )

            self.assertEqual(
                "linked_worktree", _fact(report, "repository.kind").value
            )
            self.assertEqual(
                1,
                _fact(report, "repository.worktrees").value[
                    "additional_linked"
                ],
            )
            self.assertNotIn(linked, git_perf_lib.report_json(report))
        finally:
            self.repo.git("worktree", "remove", "--force", linked)
            shutil.rmtree(parent, ignore_errors=True)


class ActualBareRepositoryTest(unittest.TestCase):
    @mock.patch("git_perf_lib.gclient_utils.IsEnvCog", return_value=False)
    def test_bare_repository_is_classified_without_an_index(self, _is_cog):
        with tempfile.TemporaryDirectory(prefix="git perf bare ") as repository:
            subprocess.run(
                ["git", "init", "--bare", repository],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            environment = os.environ.copy()
            environment["GIT_CONFIG_NOSYSTEM"] = "1"
            environment["GIT_CONFIG_GLOBAL"] = os.devnull

            report = git_perf_lib.collect_check(
                cwd=repository, environ=environment
            )

        self.assertEqual("bare", _fact(report, "repository.kind").value)
        index = _fact(report, "repository.index")
        self.assertEqual("unavailable", index.state)
        self.assertEqual(
            "not_applicable_to_bare_repository", index.applicability
        )


class RenderingTest(unittest.TestCase):
    def test_diagnostic_fact_merge_preserves_semantically_stable_values(self):
        first = git_perf_lib.Fact(
            "repository.kind",
            "repository",
            "known",
            "main_worktree",
            "benchmark",
            limitations=("first_phase",),
        )
        second = git_perf_lib.Fact(
            "repository.kind",
            "repository",
            "known",
            "main_worktree",
            "strict_context",
            limitations=("second_phase",),
        )

        stable = git_perf_lib._merge_diagnostic_facts([first], [second])[0]
        changed = git_perf_lib._merge_diagnostic_facts(
            [first],
            [dataclasses.replace(second, value="linked_worktree")],
        )[0]

        self.assertEqual("known", stable.state)
        self.assertEqual("multiple_diagnostic_phases", stable.provenance)
        self.assertEqual(("first_phase", "second_phase"), stable.limitations)
        self.assertEqual("unavailable", changed.state)
        self.assertIn("changed_between_diagnostic_phases", changed.limitations)

    def test_json_and_human_rendering_are_deterministic(self):
        report = git_perf_lib.Report(
            status="complete",
            tool={"name": "git-perf"},
            subject={"kind": "current_repository"},
            facts=[
                git_perf_lib.Fact("z.fact", "repository", "known", 2, "test"),
                git_perf_lib.Fact("a.fact", "repository", "known", 1, "test"),
            ],
        )
        first_json = git_perf_lib.report_json(report)
        second_json = git_perf_lib.report_json(report)
        self.assertEqual(first_json, second_json)
        self.assertLess(first_json.index("a.fact"), first_json.index("z.fact"))
        self.assertEqual(
            json.loads(first_json)["schema_stability"], "experimental"
        )
        self.assertEqual(
            git_perf_lib.render_human(report),
            git_perf_lib.render_human(report),
        )
        self.assertEqual(
            ["z.fact", "a.fact"], [fact.id for fact in report.facts]
        )

    def test_default_output_leads_with_a_result_not_internal_fact_states(self):
        report = git_perf_lib.Report(
            status="complete",
            tool={"name": "git-perf"},
            subject={"kind": "current_repository"},
            facts=[
                git_perf_lib.Fact(
                    "git.selected.version",
                    "git_client",
                    "known",
                    {"components": [2, 55, 0], "display": "2.55.0"},
                    "test",
                ),
                git_perf_lib.Fact(
                    "repository.kind",
                    "repository",
                    "known",
                    "main_worktree",
                    "test",
                ),
                git_perf_lib.Fact(
                    "repository.index",
                    "index",
                    "known",
                    {
                        "present": True,
                        "format_version": 4,
                        "entry_count": 1234,
                        "size_bytes": 2048,
                    },
                    "test",
                ),
                git_perf_lib.Fact(
                    "repository.shallow",
                    "repository",
                    "known",
                    False,
                    "test",
                ),
                git_perf_lib.Fact(
                    "repository.partial_clone",
                    "repository",
                    "known",
                    False,
                    "test",
                ),
                git_perf_lib.Fact(
                    "repository.sparse_checkout",
                    "repository",
                    "known",
                    False,
                    "test",
                ),
                git_perf_lib.Fact(
                    "repository.worktrees",
                    "repository",
                    "known",
                    {"registered": 1, "additional_linked": 0},
                    "test",
                ),
            ],
        )

        output = git_perf_lib.render_human(report)
        flat_output = " ".join(output.split())

        self.assertIn(
            "Nothing in the collected setup matched a warning.", output
        )
        self.assertIn(
            "It does not time Git commands or edit Git configuration.",
            flat_output,
        )
        self.assertIn("run `git perf diagnose`", output)
        self.assertIn("Git 2.55.0 in the main worktree.", output)
        self.assertIn("Index: 1,234 entries, 2.0 KiB (format v4).", output)
        self.assertIn(
            "Complete history; partial clone and sparse checkout are off.",
            output,
        )
        self.assertNotIn("KNOWN", output)
        self.assertNotIn("Facts", output)
        self.assertNotIn("repository.kind", output)

    def test_verbose_output_keeps_every_fact_without_known_badges(self):
        report = git_perf_lib.Report(
            status="partial",
            tool={"name": "git-perf"},
            subject={"kind": "current_repository"},
            facts=[
                git_perf_lib.Fact(
                    "example.available",
                    "test",
                    "known",
                    True,
                    "test",
                ),
                git_perf_lib.Fact(
                    "example.missing",
                    "test",
                    "unavailable",
                    None,
                    "test",
                    limitations=("probe_did_not_finish",),
                ),
            ],
        )

        output = git_perf_lib.render_human(report, verbose=True)

        self.assertIn("Collected details", output)
        self.assertIn("example.available: yes", output)
        self.assertIn("example.missing: unavailable", output)
        self.assertIn("Probe did not finish", output)
        self.assertNotIn("KNOWN", output)

    def test_human_output_wraps_for_a_narrow_terminal(self):
        report = git_perf_lib.Report(
            status="complete",
            tool={"name": "git-perf"},
            subject={"kind": "current_repository"},
            facts=[
                git_perf_lib.Fact(
                    "environment.git_redirects",
                    "environment",
                    "known",
                    {
                        "one_long_environment_key": "present",
                        "another": "absent",
                    },
                    "test",
                    limitations=(
                        "a_long_limitation_that_must_wrap_without_losing_meaning",
                    ),
                )
            ],
            findings=[
                git_perf_lib.Finding(
                    "finding.long",
                    "warning",
                    "environment",
                    1,
                    "A deliberately long finding that should wrap on narrow terminals.",
                    blockers=("A deliberately long blocker that also wraps.",),
                )
            ],
        )

        output = git_perf_lib.render_human(report, verbose=True, width=60)

        self.assertTrue(all(len(line) <= 60 for line in output.splitlines()))
        self.assertIn("deliberately long finding", output)


if __name__ == "__main__":
    unittest.main()
