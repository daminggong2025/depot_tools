#!/usr/bin/env vpython3
# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for lockfile.py"""

import logging
import multiprocessing
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import queue
from unittest import mock

# Standard setup to add depot_tools to the path
DEPOT_TOOLS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DEPOT_TOOLS_ROOT)

from testing_support import coverage_utils
import lockfile

# pylint: disable=line-too-long


# Helper function for the multiprocessing tests
def _try_lock_child(lock_path, mode, queue, duration=0):
    """Target function for a child process to try acquiring a lock."""
    try:
        # Note: A small timeout is used to ensure the lock attempt is quick.
        with lockfile.lock(lock_path, timeout=0.1, mode=mode):
            queue.put(True)  # Signal that lock was acquired
            time.sleep(duration)
    except lockfile.LockError:
        queue.put(False)  # Signal that lock was not acquired
    except Exception as e:
        queue.put(e)


class LockfileTest(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for the old tests (threading-based)
        self.cache_dir = tempfile.mkdtemp(prefix='lockfile_thread')

        # Create a temporary file for the new tests (multiprocessing-based)
        self.tmp_fd, self.tmp_path = tempfile.mkstemp(prefix='lockfile_proc')
        os.close(self.tmp_fd)

        # Cleanup functions
        self.addCleanup(shutil.rmtree, self.cache_dir, ignore_errors=True)
        self.addCleanup(os.remove, self.tmp_path)

        # Clean up the lock file created by the process tests
        lock_file_path = self.tmp_path + '.locked'
        self.addCleanup(lambda: os.path.exists(lock_file_path) and os.remove(
            lock_file_path))

    def testLock(self):
        """Verify basic non-timeout lock acquisition and release."""
        with lockfile.lock(self.cache_dir):
            # Cached dir locked, attempt to lock it again immediately (default timeout=0)
            with self.assertRaises(lockfile.LockError):
                with lockfile.lock(self.cache_dir):
                    pass

        # Verify lock is released and can be re-acquired
        with lockfile.lock(self.cache_dir):
            pass

    @mock.patch('time.sleep')
    def testLockConcurrent(self, sleep_mock):
        """Simulates lock acquisition with timeout using two threads."""
        q_f1 = queue.Queue()
        q_sleep = queue.Queue()
        results = queue.Queue()

        def side_effect(arg):
            """Mocked sleep: waits for f1 to finish before exiting."""
            logging.debug('sleep: started')
            q_sleep.put(True)
            logging.debug('sleep: waiting for q_sleep to be consumed')
            q_sleep.join()
            logging.debug('sleep: waiting for result before exiting')
            # The original test waits 1s; we trust it's fast enough.
            results.get(timeout=1)
            logging.debug('sleep: exiting')

        sleep_mock.side_effect = side_effect

        def f1():
            """Thread 1: Acquires lock, signals other thread to try, then releases."""
            logging.debug('f1 started, locking')
            with lockfile.lock(self.cache_dir, timeout=1):
                logging.debug('f1: locked')
                q_f1.put(True)
                logging.debug('f1: waiting on q_f1 to be consumed')
                q_f1.join()
                logging.debug('f1: done waiting on q_f1, getting q_sleep')
                q_sleep.get(timeout=1)
                results.put(True)  # Result put by f1
            logging.debug('f1: lock released')
            q_sleep.task_done()
            logging.debug('f1: exiting')

        def f2():
            """Thread 2: Waits for T1 to get the lock, then attempts lock (which calls mocked sleep)."""
            logging.debug('f2: started, consuming q_f1')
            q_f1.get(timeout=1)  # Wait for f1 to execute lock
            q_f1.task_done()
            logging.debug('f2: done waiting for q_f1, locking')

            # This lock attempt will fail, trigger time.sleep (mocked),
            # and then wait for f1 to release the lock.
            with lockfile.lock(self.cache_dir, timeout=1):
                logging.debug('f2: locked')
                # Results put by f2 after acquiring lock
                results.put(True)

        t1 = threading.Thread(target=f1)
        t2 = threading.Thread(target=f2)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Both f1 and f2 put a result, but one was consumed by side_effect,
        # so we expect only one left in the queue.
        self.assertEqual(1, results.qsize())
        # Verify that time.sleep was called during the retry loop.
        sleep_mock.assert_called_with(0.1)

    # --------------------------------------------------------------------------
    # New Tests (Multiprocessing/Cross-Process Isolation)
    # --------------------------------------------------------------------------

    def test_exclusive_lock_blocks_exclusive_lock(self):
        """Verify that a write lock blocks another write lock across processes."""
        q = multiprocessing.Queue()
        with lockfile.lock(self.tmp_path, mode='w'):
            p = multiprocessing.Process(target=_try_lock_child,
                                        args=(self.tmp_path, 'w', q))
            p.start()
            p.join()
            # Child process should fail to acquire the lock (put False)
            self.assertFalse(q.get())

    def test_exclusive_lock_blocks_shared_lock(self):
        """Verify that a write lock blocks a read lock across processes."""
        q = multiprocessing.Queue()
        with lockfile.lock(self.tmp_path, mode='w'):
            p = multiprocessing.Process(target=_try_lock_child,
                                        args=(self.tmp_path, 'r', q))
            p.start()
            p.join()
            # Child process should fail to acquire the lock (put False)
            self.assertFalse(q.get())

    def test_shared_lock_allows_shared_lock(self):
        """Verify that a read lock allows another read lock across processes."""
        q = multiprocessing.Queue()
        with lockfile.lock(self.tmp_path, mode='r'):
            p = multiprocessing.Process(target=_try_lock_child,
                                        args=(self.tmp_path, 'r', q))
            p.start()
            p.join()
            # Child process should successfully acquire the lock (put True)
            self.assertTrue(q.get())

    def test_shared_lock_blocks_exclusive_lock(self):
        """Verify that a read lock blocks a write lock across processes."""
        q = multiprocessing.Queue()
        with lockfile.lock(self.tmp_path, mode='r'):
            p = multiprocessing.Process(target=_try_lock_child,
                                        args=(self.tmp_path, 'w', q))
            p.start()
            p.join()
            # Child process should fail to acquire the lock (put False)
            self.assertFalse(q.get())

    def test_lock_released_after_exception(self):
        """Verify the lock is released when the 'with' block exits on error."""
        q = multiprocessing.Queue()
        try:
            with lockfile.lock(self.tmp_path, mode='w'):
                raise ValueError("Intentional error")
        except ValueError:
            # The lock should have been released by the context manager's finally block.
            # Try to acquire it again in a new process (should succeed).
            p = multiprocessing.Process(target=_try_lock_child,
                                        args=(self.tmp_path, 'w', q))
            p.start()
            p.join()
            self.assertTrue(q.get())

    def test_timeout_works(self):
        """Verify that a process times out if it can't get a lock within the limit."""
        q = multiprocessing.Queue()
        # Hold a long-running write lock in a child process (0.5s duration).
        p1 = multiprocessing.Process(target=_try_lock_child,
                                     args=(self.tmp_path, 'w', q, 0.5))
        p1.start()

        # Wait for the first child to acquire the lock.
        self.assertTrue(q.get())

        start_time = time.time()
        # The main thread tries to get the lock with a short 0.1s timeout.
        with self.assertRaises(lockfile.LockError):
            with lockfile.lock(self.tmp_path, timeout=0.1, mode='w'):
                pass  # This should not be reached

        duration = time.time() - start_time
        # Verify the duration is slightly greater than the 0.1s timeout
        self.assertGreater(duration, 0.1)
        # Verify the duration is less than the original p1 lock duration (0.5s),
        # plus some small buffer (e.g., 0.3s is safe for 0.1s check).
        self.assertLess(duration, 0.3)

        p1.join()


if __name__ == '__main__':
    # Required for multiprocessing on some platforms (like macOS with 'spawn' default)
    # The original script used a specific path which is likely unnecessary
    # for general unit testing if coverage is not being run.
    if sys.platform == 'darwin':
        multiprocessing.set_start_method('fork', force=True)

    logging.basicConfig(
        level=logging.DEBUG if '-v' in sys.argv else logging.ERROR)

    # If the file is run directly (not via a test runner) and we have the
    # coverage utils, use it. Otherwise, use standard unittest.
    if 'coverage_utils' in sys.modules:
        sys.exit(
            coverage_utils.covered_main(
                (os.path.join(DEPOT_TOOLS_ROOT, 'git_cache.py')),
                required_percentage=0))
    else:
        unittest.main()
