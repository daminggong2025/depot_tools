# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import multiprocessing
import os
import sys
import tempfile
import time
import unittest

# Add depot_tools to the path to allow importing lockfile.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lockfile


def _try_lock_child(lock_path, mode, queue, duration=0):
    """Target function for a child process to try acquiring a lock."""
    try:
        with lockfile.lock(lock_path, timeout=0.1, mode=mode):
            queue.put(True)  # Signal that lock was acquired
            time.sleep(duration)
    except lockfile.LockError:
        queue.put(False)  # Signal that lock was not acquired
    except Exception as e:
        queue.put(e)


class LockfileTest(unittest.TestCase):
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

    def test_exclusive_lock_blocks_exclusive_lock(self):
        """Verify that a write lock blocks another write lock."""
        queue = multiprocessing.Queue()
        with lockfile.lock(self.tmp_path, mode='w'):
            p = multiprocessing.Process(target=_try_lock_child,
                                        args=(self.tmp_path, 'w', queue))
            p.start()
            p.join()
            self.assertFalse(queue.get())

    def test_exclusive_lock_blocks_shared_lock(self):
        """Verify that a write lock blocks a read lock."""
        queue = multiprocessing.Queue()
        with lockfile.lock(self.tmp_path, mode='w'):
            p = multiprocessing.Process(target=_try_lock_child,
                                        args=(self.tmp_path, 'r', queue))
            p.start()
            p.join()
            self.assertFalse(queue.get())

    def test_shared_lock_allows_shared_lock(self):
        """Verify that a read lock allows another read lock."""
        queue = multiprocessing.Queue()
        with lockfile.lock(self.tmp_path, mode='r'):
            p = multiprocessing.Process(target=_try_lock_child,
                                        args=(self.tmp_path, 'r', queue))
            p.start()
            p.join()
            self.assertTrue(queue.get())

    def test_shared_lock_blocks_exclusive_lock(self):
        """Verify that a read lock blocks a write lock."""
        queue = multiprocessing.Queue()
        with lockfile.lock(self.tmp_path, mode='r'):
            p = multiprocessing.Process(target=_try_lock_child,
                                        args=(self.tmp_path, 'w', queue))
            p.start()
            p.join()
            self.assertFalse(queue.get())

    def test_lock_released_after_exception(self):
        """Verify the lock is released when the 'with' block exits on error."""
        queue = multiprocessing.Queue()
        try:
            with lockfile.lock(self.tmp_path, mode='w'):
                raise ValueError("Intentional error")
        except ValueError:
            # The lock should have been released. Try to acquire it again.
            p = multiprocessing.Process(target=_try_lock_child,
                                        args=(self.tmp_path, 'w', queue))
            p.start()
            p.join()
            self.assertTrue(queue.get())

    def test_timeout_works(self):
        """Verify that a process times out if it can't get a lock."""
        queue = multiprocessing.Queue()
        # Hold a long-running write lock in a child process.
        p1 = multiprocessing.Process(target=_try_lock_child,
                                     args=(self.tmp_path, 'w', queue, 0.5))
        p1.start()

        # Wait for the child to acquire the lock.
        self.assertTrue(queue.get())

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
    multiprocessing.set_start_method('fork', force=True)
    unittest.main()
