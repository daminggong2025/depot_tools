#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import subprocess
import sys
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Python3WrapperTest(unittest.TestCase):

  def setUp(self):
    super().setUp()
    self.python3_cmd = os.path.join(
        ROOT_DIR, 'python3.bat' if sys.platform == 'win32' else 'python3'
    )
    self.python_bin_cmd = os.path.join(
        ROOT_DIR,
        'python-bin',
        'python3.bat' if sys.platform == 'win32' else 'python3',
    )

  def testDefaultRoutingToCipd(self):
    env = os.environ.copy()
    env.pop('VPYTHON_BYPASS', None)
    env.pop('DEPOT_TOOLS_PYTHON_BYPASS', None)
    res = subprocess.run(
        [self.python3_cmd, '-c', 'import sys; print(sys.executable)'],
        capture_output=True,
        text=True,
        env=env,
    )
    self.assertEqual(res.returncode, 0, msg=res.stderr)
    # Default routing should invoke the CIPD-managed CPython binary.
    self.assertIn('bootstrap', res.stdout)

  def testVpythonBypassUsesSystemPython(self):
    env = os.environ.copy()
    env['VPYTHON_BYPASS'] = '1'
    res = subprocess.run(
        [self.python3_cmd, '-c', 'import sys; print(sys.executable)'],
        capture_output=True,
        text=True,
        env=env,
    )
    self.assertEqual(res.returncode, 0, msg=res.stderr)
    # When bypass is enabled, it should not use the bootstrap CIPD Python.
    self.assertNotIn('bootstrap', res.stdout)

  def testDepotToolsBypassUsesSystemPython(self):
    env = os.environ.copy()
    env['DEPOT_TOOLS_PYTHON_BYPASS'] = '1'
    res = subprocess.run(
        [self.python3_cmd, '-c', 'import sys; print(sys.executable)'],
        capture_output=True,
        text=True,
        env=env,
    )
    self.assertEqual(res.returncode, 0, msg=res.stderr)
    self.assertNotIn('bootstrap', res.stdout)

  @unittest.skipIf(
      sys.platform == 'win32', 'POSIX-specific PATH filtering test'
  )
  def testBypassFailsCleanlyWithoutSystemPython(self):
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
      for bin_dir in ('/bin', '/usr/bin'):
        if os.path.exists(bin_dir):
          for name in os.listdir(bin_dir):
            if not name.startswith('python'):
              src = os.path.join(bin_dir, name)
              dst = os.path.join(tmpdir, name)
              if not os.path.exists(dst):
                try:
                  os.symlink(src, dst)
                except OSError:
                  pass
      env = os.environ.copy()
      env['VPYTHON_BYPASS'] = '1'
      env['PATH'] = tmpdir
      res = subprocess.run(
          [self.python_bin_cmd, '-c', 'import sys; print("Should not reach")'],
          capture_output=True,
          text=True,
          env=env,
          timeout=5,
      )
      self.assertNotEqual(res.returncode, 0)
      self.assertIn('System python3 not found in PATH', res.stderr)


if __name__ == '__main__':
  unittest.main()
