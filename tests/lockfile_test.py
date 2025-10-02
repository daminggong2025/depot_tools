#!/usr/bin/env vpython3
# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for lockfile.py"""

import logging
import multiprocessing
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

DEPOT_TOOLS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DEPOT_TOOLS_ROOT)

from testing_support import coverage_utils

import lockfile

# TODO: Should fix these warnings.
# pylint: disable=line-too-long


def _try_lock_child(lock_path, mode, queue_obj, duration=0):
    """Target function for a child process to try acquiring a lock."""
    try:
        with lockfile.lock(lock_path, timeout=0.1, mode=mode):
            queue_obj.put(True)  # Signal that lock was acquired
            time.sleep(duration)
    except lockfile.LockError:
        queue_obj.put(False)  # Signal that lock was not acquired
    except Exception as e:
        queue_obj.put(e)


class LockTest(unittest.TestCase):
    def setUp(self):
        self.cache_dir = tempfile.mkdtemp(prefix='lockfile')
        self.addCleanup(shutil.rmtree, self.cache_dir, ignore_errors=True)

    def testLock(self):
        with lockfile.lock(self.cache_dir):
            # cached dir locked, attempt to lock it again
            with self.assertRaises(lockfile.LockError):
                with lockfile.lock(self.cache_dir):
                    pass

        with lockfile.lock(self.cache_dir):
            pass

    @mock.patch('time.sleep')
    def testLockConcurrent(self, sleep_mock):
        '''testLockConcurrent simulates what happens when two separate processes try
    to acquire the same file lock with timeout.'''
        # Queues q_f1 and q_sleep are used to controll execution of individual
        # threads.
        q_f1 = queue.Queue()
        q_sleep = queue.Queue()
        results = queue.Queue()

        def side_effect(arg):
            '''side_effect is called when with l.lock is blocked. In this unit test
      case, it comes from f2.'''
            logging.debug('sleep: started')
            q_sleep.put(True)
            logging.debug('sleep: waiting for q_sleep to be consumed')
            q_sleep.join()
            logging.debug('sleep: waiting for result before exiting')
            results.get(timeout=1)
            logging.debug('sleep: exiting')

        sleep_mock.side_effect = side_effect

        def f1():
            '''f1 enters first in l.lock (controlled via q_f1). It then waits for
      side_effect to put a message in queue q_sleep.'''
            logging.debug('f1 started, locking')

            with lockfile.lock(self.cache_dir, timeout=1):
                logging.debug('f1: locked')
                q_f1.put(True)
                logging.debug('f1: waiting on q_f1 to be consumed')
                q_f1.join()
                logging.debug('f1: done waiting on q_f1, getting q_sleep')
                q_sleep.get(timeout=1)
                results.put(True)

            logging.debug('f1: lock released')
            q_sleep.task_done()
            logging.debug('f1: exiting')

        def f2():
            '''f2 enters second in l.lock (controlled by q_f1).'''
            logging.debug('f2: started, consuming q_f1')
            q_f1.get(timeout=1)  # wait for f1 to execute lock
            q_f1.task_done()
            logging.debug('f2: done waiting for q_f1, locking')

            with lockfile.lock(self.cache_dir, timeout=1):
                logging.debug('f2: locked')
                results.put(True)

        t1 = threading.Thread(target=f1)
        t1.start()
        t2 = threading.Thread(target=f2)
        t2.start()
        t1.join()
        t2.join()

        # One result was consumed by side_effect, we expect only one in the
        # queue.
        self.assertEqual(1, results.qsize())
        sleep_mock.assert_called_with(0.1)


class LockfileReadWriteTest(unittest.TestCase):

    def setUp(self):
        # Create a temporary file to serve as the lock target.
        self.tmp_fd, self.tmp_path = tempfile.mkstemp()
        os.close(self.tmp_fd)

    def tearDown(self):
        # Clean up the temporary file and the lock file.
        os.remove(self.tmp_path)
        lock_file_path = self.tmp_path + '.locked'
        if os.path.exists(lock_file_path):
            os.remove(lock_file_path)

    def test_lock_combinations(self):
        """Verify all read/write lock combinations."""
        test_cases = [
            # (test_name, first_lock_mode, second_lock_mode, expected_success)
            ('exclusive_blocks_exclusive', 'w', 'w', False),
            ('exclusive_blocks_shared', 'w', 'r', False),
            ('shared_allows_shared', 'r', 'r', True),
            ('shared_blocks_exclusive', 'r', 'w', False),
        ]

        for name, first_mode, second_mode, expect_success in test_cases:
            with self.subTest(msg=name):
                queue_obj = multiprocessing.Queue()
                with lockfile.lock(self.tmp_path, mode=first_mode):
                    p = multiprocessing.Process(target=_try_lock_child,
                                                args=(self.tmp_path,
                                                      second_mode, queue_obj))
                    p.start()
                    p.join()
                    self.assertEqual(queue_obj.get(), expect_success)

    def test_lock_released_after_exception(self):
        """Verify the lock is released when the 'with' block exits on error."""
        queue_obj = multiprocessing.Queue()
        try:
            with lockfile.lock(self.tmp_path, mode='w'):
                raise ValueError("Intentional error")
        except ValueError:
            # The lock should have been released. Try to acquire it again.
            p = multiprocessing.Process(target=_try_lock_child,
                                        args=(self.tmp_path, 'w', queue_obj))
            p.start()
            p.join()
            self.assertTrue(queue_obj.get())

    def test_timeout_works(self):
        """Verify that a process times out if it can't get a lock."""
        queue_obj = multiprocessing.Queue()
        # Hold a long-running write lock in a child process.
        p1 = multiprocessing.Process(target=_try_lock_child,
                                     args=(self.tmp_path, 'w', queue_obj, 0.5))
        p1.start()

        # Wait for the child to acquire the lock.
        self.assertTrue(queue_obj.get())

        start_time = time.time()
        with self.assertRaises(lockfile.LockError):
            with lockfile.lock(self.tmp_path, timeout=0.1, mode='w'):
                pass  # This should not be reached

        duration = time.time() - start_time
        self.assertGreater(duration, 0.1)
        self.assertLess(duration, 0.3)

        p1.join()


if __name__ == '__main__':
    # This is required for multiprocessing to work correctly on some platforms.
    # The 'fork' start method is not available on Windows.                                                                                                                                          │
    if sys.platform != 'win32':
        multiprocessing.set_start_method('fork', force=True)
    unittest.main()
