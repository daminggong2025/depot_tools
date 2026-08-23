#!/usr/bin/env python3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for presubmit_thread_pool.py."""

import os
import sys
import threading
import unittest
from unittest import mock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import presubmit_support as presubmit  # noqa: E402
import presubmit_thread_pool  # noqa: E402
import subprocess2 as subprocess  # noqa: E402


class ThreadPoolTest(unittest.TestCase):
    def setUp(self):
        super(ThreadPoolTest, self).setUp()
        mock.patch("subprocess2.Popen").start()
        mock.patch("presubmit_thread_pool.sigint_handler").start()
        mock.patch("presubmit_thread_pool.time_time", return_value=0).start()
        presubmit_thread_pool.sigint_handler.wait.return_value = (b"stdout", "")
        self.addCleanup(mock.patch.stopall)

    def testSurfaceExceptions(self):
        def FakePopen(cmd, **kwargs):
            if cmd[0] == "3":
                raise TypeError("TypeError")
            if cmd[0] == "4":
                raise OSError("OSError")
            if cmd[0] == "5":
                return mock.Mock(returncode=1)
            return mock.Mock(returncode=0)

        subprocess.Popen.side_effect = FakePopen

        mock_tests = [
            presubmit_thread_pool.CommandData(
                name=str(i),
                cmd=[str(i)],
                kwargs={},
                message=presubmit.OutputApi.PresubmitError,
            )
            for i in range(10)
        ]

        t = presubmit_thread_pool.ThreadPool(1)
        t.AddTests(mock_tests)
        messages = sorted(t.RunAsync(), key=lambda x: x._message)

        self.assertEqual(3, len(messages))
        self.assertIn(
            "3\n3 exec failure (0.00s)\nTraceback (most recent call last):",
            messages[0]._message,
        )
        self.assertIn(
            "4\n4 exec failure (0.00s)\nTraceback (most recent call last):",
            messages[1]._message,
        )
        self.assertEqual(
            "5\n5 exit code 1 (0.00s)\nstdout", messages[2]._message
        )

    def testOutputParser(self):
        def FakePopen(cmd, **kwargs):
            if cmd[0] == "multiple":
                return mock.Mock(returncode=0)
            if cmd[0] == "empty_pass":
                return mock.Mock(returncode=0)
            if cmd[0] == "empty_fail":
                return mock.Mock(returncode=1)
            return mock.Mock(returncode=0)

        subprocess.Popen.side_effect = FakePopen

        def parse_multiple(output):
            return [
                presubmit.OutputApi.PresubmitError("error 1"),
                presubmit.OutputApi.PresubmitError("error 2"),
                presubmit.OutputApi.PresubmitPromptWarning("warning 1"),
            ]

        def parse_empty(output):
            return []

        mock_tests = [
            presubmit_thread_pool.CommandData(
                name="multiple",
                cmd=["multiple"],
                kwargs={},
                output_parser=parse_multiple,
            ),
            presubmit_thread_pool.CommandData(
                name="empty_pass",
                cmd=["empty_pass"],
                kwargs={},
                output_parser=parse_empty,
            ),
            presubmit_thread_pool.CommandData(
                name="empty_fail",
                cmd=["empty_fail"],
                kwargs={},
                message=presubmit.OutputApi.PresubmitError,
                output_parser=parse_empty,
            ),
        ]

        t = presubmit_thread_pool.ThreadPool(1)
        t.AddTests(mock_tests)
        messages = t.RunAsync()
        message_strs = [r._message for r in messages]

        self.assertEqual(4, len(messages))
        self.assertIn(
            "empty_fail\nempty_fail exit code 1 (0.00s)\nstdout", message_strs
        )
        self.assertIn("error 1", message_strs)
        self.assertIn("error 2", message_strs)
        self.assertIn("warning 1", message_strs)

    def testEagerExecutionBeforeRunAsync(self):
        started_event = threading.Event()
        release_event = threading.Event()

        def SlowPopen(cmd, **kwargs):
            started_event.set()
            release_event.wait(timeout=5.0)
            return mock.Mock(returncode=0)

        mock_test = presubmit_thread_pool.CommandData(
            name="eager_test",
            cmd=["eager_cmd"],
            kwargs={},
            message=presubmit.OutputApi.PresubmitError,
        )

        with mock.patch.object(subprocess, "Popen", side_effect=SlowPopen):
            with presubmit_thread_pool.ThreadPool(1) as t:
                t.AddTests([mock_test], parallel=True)
                # Verify the worker started the command eagerly BEFORE RunAsync is called.
                self.assertTrue(started_event.wait(timeout=2.0))
                release_event.set()
                messages = t.RunAsync()
                self.assertEqual([], messages)

    def testBarrierNonparallelOrdering(self):
        execution_order = []

        def FakePopen(cmd, **kwargs):
            if cmd[0] == "parallel_task":
                import time as py_time

                py_time.sleep(0.05)
                execution_order.append("parallel")
            elif cmd[0] == "sequential_task":
                execution_order.append("sequential")
            return mock.Mock(returncode=0)

        test_parallel = presubmit_thread_pool.CommandData(
            name="parallel",
            cmd=["parallel_task"],
            kwargs={},
            message=presubmit.OutputApi.PresubmitError,
        )
        test_seq = presubmit_thread_pool.CommandData(
            name="sequential",
            cmd=["sequential_task"],
            kwargs={},
            message=presubmit.OutputApi.PresubmitError,
        )

        with mock.patch.object(subprocess, "Popen", side_effect=FakePopen):
            with presubmit_thread_pool.ThreadPool(2) as t:
                t.AddTests([test_parallel], parallel=True)
                t.AddTests([test_seq], parallel=False)
                t.RunAsync()

        # Non-parallel test should execute only after the barrier drains parallel tasks
        self.assertEqual(["parallel", "sequential"], execution_order)

    def testContextManagerDeferredTempFileCleanup(self):
        with mock.patch("os.remove") as mock_remove:
            with presubmit_thread_pool.ThreadPool(1) as t:
                t.RegisterTemporaryFiles(["fake_file_1.txt", "fake_file_2.txt"])
                mock_remove.assert_not_called()

            self.assertEqual(
                mock_remove.mock_calls,
                [mock.call("fake_file_2.txt"), mock.call("fake_file_1.txt")],
            )

    def testAbsoluteCwdNormalization(self):
        captured_kwargs = []

        def FakePopen(cmd, **kwargs):
            captured_kwargs.append(kwargs)
            return mock.Mock(returncode=0)

        mock_test = presubmit_thread_pool.CommandData(
            name="cwd_test",
            cmd=["test_cmd"],
            kwargs={"cwd": "."},
            message=presubmit.OutputApi.PresubmitError,
        )

        with mock.patch.object(subprocess, "Popen", side_effect=FakePopen):
            with presubmit_thread_pool.ThreadPool(1) as t:
                t.AddTests([mock_test], parallel=True)
                t.RunAsync()

        self.assertEqual(1, len(captured_kwargs))
        self.assertTrue(os.path.isabs(captured_kwargs[0]["cwd"]))

    def testCancelTerminatesActiveSubprocesses(self):
        started_event = threading.Event()
        mock_proc = mock.Mock()

        def FakeWait(p, stdin):
            started_event.set()
            while not mock_proc.terminate.called:
                import time as py_time

                py_time.sleep(0.01)
            return b"stdout", b""

        mock_test = presubmit_thread_pool.CommandData(
            name="hanging_test",
            cmd=["hanging_cmd"],
            kwargs={},
            message=presubmit.OutputApi.PresubmitError,
        )

        with mock.patch.object(
            presubmit_thread_pool.sigint_handler, "wait", side_effect=FakeWait
        ):
            with mock.patch.object(subprocess, "Popen", return_value=mock_proc):
                t = presubmit_thread_pool.ThreadPool(1)
                t.AddTests([mock_test], parallel=True)
                self.assertTrue(started_event.wait(timeout=2.0))
                t.Cancel()
                mock_proc.terminate.assert_called_once()
                t.Close()
                t.Join(timeout=0.5)


if __name__ == "__main__":
    unittest.main()
