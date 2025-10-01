# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Exclusive and shared filelocking for all supported platforms."""

import contextlib
import logging
import os
import sys
import time


class LockError(Exception):
    pass


if sys.platform.startswith('win'):
    # Windows implementation
    import win32imports

    BYTES_TO_LOCK = 1

    def _open_file(lockfile, mode):
        # For a write lock, we use dwShareMode=0 to prevent any other handle from
        # being opened. For a read lock, we allow other readers.
        share_mode = win32imports.FILE_SHARE_READ if mode == 'r' else 0
        return win32imports.Handle(
            win32imports.CreateFileW(
                lockfile,  # lpFileName
                win32imports.GENERIC_WRITE,  # dwDesiredAccess
                share_mode,  # dwShareMode
                None,  # lpSecurityAttributes
                win32imports.CREATE_ALWAYS,  # dwCreationDisposition
                win32imports.FILE_ATTRIBUTE_NORMAL,  # dwFlagsAndAttributes
                None  # hTemplateFile
            ))

    def _close_file(handle, unlock):
        if unlock:
            # Locks are released *before* the CloseHandle function is finished
            # processing.
            pass
        win32imports.CloseHandle(handle)

    def _lock_file(handle, mode):
        # For a write lock, request an exclusive lock. For a read lock, the
        # default LockFileEx flag (0) requests a shared lock.
        lock_flags = win32imports.LOCKFILE_EXCLUSIVE_LOCK if mode == 'w' else 0
        ret = win32imports.LockFileEx(
            handle,  # hFile
            win32imports.LOCKFILE_FAIL_IMMEDIATELY | lock_flags,  # dwFlags
            0,  #dwReserved
            BYTES_TO_LOCK,  # nNumberOfBytesToLockLow
            0,  # nNumberOfBytesToLockHigh
            win32imports.Overlapped()  # lpOverlapped
        )
        if ret == 0:
            error_code = win32imports.GetLastError()
            raise OSError('Failed to lock handle (error code: %d).' %
                          error_code)
else:
    # Unix implementation
    import fcntl

    def _open_file(lockfile, mode):
        # Mode is unused here but kept for API consistency with Windows.
        open_flags = (os.O_CREAT | os.O_WRONLY)
        return os.open(lockfile, open_flags, 0o644)

    def _close_file(fd, unlock):
        if unlock:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def _lock_file(fd, mode):
        lock_type = fcntl.LOCK_EX if mode == 'w' else fcntl.LOCK_SH
        fcntl.flock(fd, lock_type | fcntl.LOCK_NB)


def _try_lock(lockfile, mode):
    f = _open_file(lockfile, mode)
    try:
        _lock_file(f, mode)
    except Exception:
        _close_file(f, unlock=False)
        raise
    return lambda: _close_file(f, unlock=True)


def _lock(path, timeout=0, mode='w'):
    """_lock returns function to release the lock if locking was successful."""
    elapsed = 0
    sleep_time = 0.1
    while True:
        try:
            return _try_lock(path + '.locked', mode)
        except (OSError, IOError) as e:
            if elapsed < timeout:
                logging.info(
                    'Could not create git cache lockfile; '
                    'will retry after sleep(%d).', sleep_time)
                elapsed += sleep_time
                time.sleep(sleep_time)
                continue
            raise LockError("Error locking %s (err: %s)" % (path, str(e)))


@contextlib.contextmanager
def lock(path, timeout=0, mode='w'):
    """
    Get a lock to a path, shared for read ('r') or exclusive for write ('w').

    Usage:
        import lockfile
        # Get a write lock
        with lockfile.lock(path, timeout, mode='w'):
            # Do exclusive work
            pass

        # Get a read lock
        with lockfile.lock(path, timeout, mode='r'):
            # Do shared work
            pass
    """
    if mode not in ('r', 'w'):
        raise ValueError('Mode must be "r" or "w"')
    release_fn = _lock(path, timeout, mode)
    try:
        yield
    finally:
        release_fn()
