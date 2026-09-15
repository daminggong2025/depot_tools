#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

import trace_utils  # noqa: E402


class TraceUtilsTest(unittest.TestCase):
    """Unit tests for the Chrome Trace Event format tracer in `trace_utils`."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.trace_file = os.path.join(self.tmpdir, "test_trace.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir)

    def test_trace_basic(self) -> None:
        """Verifies basic trace collection and JSON event structure.

        Scenario:
        - Start tracing with a custom process name.
        - Record a scoped trace event using the `trace` context manager.
        - Close the collector and verify the JSON trace output.

        Expected:
        - Trace file is written containing `process_name` metadata and the
          complete event with matching name, category, duration, and args.
        """
        trace_utils.collector.start(self.trace_file, process_name="gclient")

        # Test context manager.
        with trace_utils.trace(
            "test_block", cat="test_cat", args={"key": "value"}
        ):
            time.sleep(0.01)

        trace_utils.collector.close()

        self.assertTrue(os.path.exists(self.trace_file))
        with open(self.trace_file, "r") as f:
            data = json.load(f)

        self.assertIn("traceEvents", data)
        events = data["traceEvents"]

        # Check metadata events.
        process_events = [e for e in events if e.get("name") == "process_name"]
        self.assertTrue(len(process_events) > 0)
        self.assertEqual(process_events[0]["args"]["name"], "gclient")

        # Check block event.
        block_events = [e for e in events if e.get("name") == "test_block"]
        self.assertEqual(len(block_events), 1)
        self.assertEqual(block_events[0]["cat"], "test_cat")
        self.assertEqual(block_events[0]["ph"], "X")
        self.assertGreaterEqual(
            block_events[0]["dur"], 5000
        )  # at least ~5ms in us
        self.assertEqual(block_events[0]["args"]["key"], "value")

    def test_trace_multithread(self) -> None:
        """Verifies trace event collection across multiple concurrent threads.

        Scenario:
        - Spawn multiple worker threads that record trace events simultaneously.
        - Close the collector after all threads complete.

        Expected:
        - All thread events are safely recorded into the trace file without data
          loss or corruption.
        """
        trace_utils.collector.start(self.trace_file)

        def worker(num: int) -> None:
            trace_utils.collector.set_thread_name(f"Worker-{num}")
            with trace_utils.trace(f"task_{num}", cat="worker"):
                time.sleep(0.01)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        trace_utils.collector.close()

        with open(self.trace_file, "r") as f:
            data = json.load(f)

        events = data["traceEvents"]
        for i in range(3):
            task_events = [e for e in events if e.get("name") == f"task_{i}"]
            self.assertEqual(len(task_events), 1)

    def test_trace_decorator(self) -> None:
        """Verifies function tracing via the `trace_function` decorator.

        Scenario:
        - Decorate a function with `@trace_function`.
        - Execute the function and verify return value.

        Expected:
        - Function execution produces a corresponding complete event in the
          trace file while preserving the return value.
        """
        trace_utils.collector.start(self.trace_file)

        @trace_utils.trace_function(name="my_func", cat="function")
        def sample_function(x: int) -> int:
            time.sleep(0.01)
            return x * 2

        result = sample_function(21)
        self.assertEqual(result, 42)

        trace_utils.collector.close()

        with open(self.trace_file, "r") as f:
            data = json.load(f)

        func_events = [
            e for e in data["traceEvents"] if e.get("name") == "my_func"
        ]
        self.assertEqual(len(func_events), 1)
        self.assertEqual(func_events[0]["cat"], "function")

    def test_trace_env_var(self) -> None:
        """Verifies tracing activation via the `GCLIENT_TRACE` environment variable.

        Scenario:
        - Set `GCLIENT_TRACE` to a target file path.
        - Instantiate `TraceCollector` and record a trace event.

        Expected:
        - Collector automatically activates with the specified filepath on
          startup.
        """
        try:
            os.environ["GCLIENT_TRACE"] = self.trace_file
            collector = trace_utils.TraceCollector()
            self.assertTrue(collector.enabled)
            self.assertEqual(collector.filepath, self.trace_file)

            with trace_utils.trace("env_test"):
                time.sleep(0.005)

            collector.close()
            self.assertTrue(os.path.exists(self.trace_file))
        finally:
            os.environ.pop("GCLIENT_TRACE", None)

    def test_trace_presubmit_env_var(self) -> None:
        """Verifies automatic tracing and script name detection via `PRESUBMIT_TRACE`.

        Scenario:
        - Set `PRESUBMIT_TRACE` to a target file path.
        - Instantiate `TraceCollector` and record a trace event.
        - Close the collector and parse the output JSON.

        Expected:
        - Collector automatically activates and detects the calling script name
          as the process name metadata (e.g. `trace_utils_test`).
        """
        try:
            os.environ["PRESUBMIT_TRACE"] = self.trace_file
            collector = trace_utils.TraceCollector()
            self.assertTrue(collector.enabled)
            self.assertEqual(collector.filepath, self.trace_file)

            with trace_utils.trace("presubmit_test"):
                time.sleep(0.005)

            collector.close()
            self.assertTrue(os.path.exists(self.trace_file))
            with open(self.trace_file, "r") as f:
                data = json.load(f)
            process_events = [
                e
                for e in data["traceEvents"]
                if e.get("name") == "process_name"
            ]
            self.assertEqual(
                process_events[0]["args"]["name"], "trace_utils_test"
            )
        finally:
            os.environ.pop("PRESUBMIT_TRACE", None)

    def test_trace_env_var_default_path(self) -> None:
        """Verifies default output path resolution when `GCLIENT_TRACE=1` (or truthy)."""
        for val in ("1", "true", "yes", "on", "TRUE", "YES"):
            try:
                os.environ["GCLIENT_TRACE"] = val
                collector = trace_utils.TraceCollector()
                self.assertTrue(
                    collector.enabled,
                    f"Expected tracing enabled for GCLIENT_TRACE={val!r}",
                )
                self.assertEqual(
                    collector.filepath,
                    os.path.join(tempfile.gettempdir(), "gclient_trace.json"),
                )
            finally:
                os.environ.pop("GCLIENT_TRACE", None)

    def test_trace_env_var_disabled(self) -> None:
        """Verifies that falsy GCLIENT_TRACE values do not enable tracing.

        Scenario:
        - Set GCLIENT_TRACE to various falsy values ("0", "false", "no", "off", "").
        - Instantiate TraceCollector.

        Expected:
        - Tracing is not enabled.
        """
        for val in ("0", "false", "no", "off", "FALSE", "NO", "OFF", "", "  "):
            try:
                os.environ["GCLIENT_TRACE"] = val
                collector = trace_utils.TraceCollector()
                self.assertFalse(
                    collector.enabled,
                    f"Expected tracing disabled for GCLIENT_TRACE={val!r}",
                )
            finally:
                os.environ.pop("GCLIENT_TRACE", None)

    def test_start_default_filepath(self) -> None:
        """Verifies default output path resolution when `start()` is called without args."""
        collector = trace_utils.TraceCollector()
        collector.start()
        self.assertTrue(collector.enabled)
        self.assertEqual(
            collector.filepath,
            os.path.join(tempfile.gettempdir(), "depot_tools_trace.json"),
        )

    def test_start_already_enabled_does_not_wipe_events(self) -> None:
        collector = trace_utils.TraceCollector()
        collector.start(self.trace_file)
        collector.add_complete_event("initial_event", "cat", 1000, 2000)

        # Calling start again should not wipe existing events.
        new_trace_file = os.path.join(self.tmpdir, "new_trace.json")
        collector.start(new_trace_file)
        self.assertEqual(collector.filepath, new_trace_file)

        collector.close()

        with open(new_trace_file, "r") as f:
            data = json.load(f)
        events = [
            e for e in data["traceEvents"] if e.get("name") == "initial_event"
        ]
        self.assertEqual(len(events), 1)

    def test_trace_merge_existing_file(self) -> None:
        """Verifies that multiple collector sessions merge events into an existing trace file.

        Scenario:
        - Collector 1 starts, writes an event to the trace file, and closes.
        - Collector 2 starts on the same file, writes another event, and closes.
        - Read and parse the resulting trace file.

        Expected:
        - Both events (`event1` and `event2`) coexist in the merged `traceEvents`
          list, simulating sequential or concurrent subprocess trace outputs.
        """
        collector1 = trace_utils.TraceCollector()
        collector1.start(self.trace_file, process_name="proc1")
        collector1.add_complete_event("event1", "cat1", 1000, 500)
        collector1.close()

        collector2 = trace_utils.TraceCollector()
        collector2.start(self.trace_file, process_name="proc2")
        collector2.add_complete_event("event2", "cat2", 2000, 500)
        collector2.close()

        with open(self.trace_file, "r") as f:
            data = json.load(f)

        event_names = [e.get("name") for e in data["traceEvents"]]
        self.assertIn("event1", event_names)
        self.assertIn("event2", event_names)


if __name__ == "__main__":
    unittest.main()
