#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import json
import os
import shutil
import tempfile
import threading
import time
import unittest

import trace_utils


class TraceUtilsTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.trace_file = os.path.join(self.tmpdir, "test_trace.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_trace_basic(self):
        trace_utils.collector.start(self.trace_file)

        # Test context manager.
        with trace_utils.trace("test_block", cat="test_cat", args={"key": "value"}):
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
        self.assertGreaterEqual(block_events[0]["dur"], 5000)  # at least ~5ms in us
        self.assertEqual(block_events[0]["args"]["key"], "value")

    def test_trace_multithread(self):
        trace_utils.collector.start(self.trace_file)

        def worker(num):
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

    def test_trace_decorator(self):
        trace_utils.collector.start(self.trace_file)

        @trace_utils.trace_function(name="my_func", cat="function")
        def sample_function(x):
            time.sleep(0.01)
            return x * 2

        result = sample_function(21)
        self.assertEqual(result, 42)

        trace_utils.collector.close()

        with open(self.trace_file, "r") as f:
            data = json.load(f)

        func_events = [e for e in data["traceEvents"] if e.get("name") == "my_func"]
        self.assertEqual(len(func_events), 1)
        self.assertEqual(func_events[0]["cat"], "function")

    def test_trace_env_var(self):
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


if __name__ == "__main__":
    unittest.main()
