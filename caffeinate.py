# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import contextlib
import os
import subprocess
import sys

_NO_CAFFEINATE_FLAG = '--no-caffeinate'

_HELP_MESSAGE = f"""\
caffeinate:
  {_NO_CAFFEINATE_FLAG}  do not prepend `caffeinate` to ninja command
"""


def call(args, **call_kwargs):
    """Runs a command (via subprocess.call) with `caffeinate` if it's on macOS."""
    if sys.platform == 'darwin':
        if isinstance(args, (str, bytes, os.PathLike)):
            args = [args]
        if '-h' in args or '--help' in args:
            print(_HELP_MESSAGE, file=sys.stderr)
        if _NO_CAFFEINATE_FLAG in args:
            args.remove(_NO_CAFFEINATE_FLAG)
        else:
            args = ['caffeinate'] + args
    return subprocess.call(args, **call_kwargs)


@contextlib.contextmanager
def scope(actually_caffeinate=True, max_sec=None):
    """Acts as a context manager keeping a Mac awake, unless flagged off.

    If the process is not running on a Mac, or `actually_caffeinate` is falsey,
    this acts as a context manager that does nothing. The `actually_caffeinate`
    flag is provided so command line flags can control the caffeinate behavior
    without requiring weird plumbing to use or not use the context manager.

    If running on a Mac while actually_caffeinate is True (the default), this
    runs `caffeinate` in a separate process, which is terminated when the
    context manager exits. If max_sec is provided, caffeinate will exit on its
    own after that many seconds if it has not already been terminated by then.
    """
    if sys.platform != 'darwin' or not actually_caffeinate:
        # Behave like a no-op context manager.
        yield False
        return

    # Caffeinate flags used here:
    #   -i: suppress idle sleep
    #   -w: exit after process with this PID exits
    #   -t: exit after this number of seconds
    # If both -w and -t are provided, caffeinate exits (canceling its wake lock)
    # when either condition is met. -w is used here to make sure caffeinate
    # doesn't outlive this process, even in abnormal scenarios.
    cmd = ['caffeinate', '-i', '-w', str(os.getpid())]
    if max_sec is not None:
        cmd += ['-t', str(max_sec)]

    proc = subprocess.Popen(cmd,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        yield True
    finally:
        proc.terminate()
