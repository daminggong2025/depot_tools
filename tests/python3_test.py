#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import shutil
import subprocess
import sys
import tempfile
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

  def _assert_routing(self, res, expected_in_depot_tools):
    self.assertEqual(res.returncode, 0, msg=res.stderr)
    exec_path = os.path.abspath(res.stdout.strip())
    is_in_depot_tools = exec_path.startswith(ROOT_DIR)
    if expected_in_depot_tools:
      self.assertTrue(
          is_in_depot_tools,
          msg=f'Expected CIPD python inside {ROOT_DIR}, got {exec_path}',
      )
    else:
      self.assertFalse(
          is_in_depot_tools,
          msg=f'Expected system python outside {ROOT_DIR}, got {exec_path}',
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
    self._assert_routing(res, expected_in_depot_tools=True)

  def testVpythonBypassUsesSystemPython(self):
    env = os.environ.copy()
    env['VPYTHON_BYPASS'] = '1'
    res = subprocess.run(
        [self.python3_cmd, '-c', 'import sys; print(sys.executable)'],
        capture_output=True,
        text=True,
        env=env,
    )
    self._assert_routing(res, expected_in_depot_tools=False)

  def testDepotToolsBypassUsesSystemPython(self):
    env = os.environ.copy()
    env['DEPOT_TOOLS_PYTHON_BYPASS'] = '1'
    res = subprocess.run(
        [self.python3_cmd, '-c', 'import sys; print(sys.executable)'],
        capture_output=True,
        text=True,
        env=env,
    )
    self._assert_routing(res, expected_in_depot_tools=False)

  def testBypassZeroDoesNotBypass(self):
    env = os.environ.copy()
    env['VPYTHON_BYPASS'] = '0'
    env['DEPOT_TOOLS_PYTHON_BYPASS'] = '0'
    res = subprocess.run(
        [self.python3_cmd, '-c', 'import sys; print(sys.executable)'],
        capture_output=True,
        text=True,
        env=env,
    )
    self._assert_routing(res, expected_in_depot_tools=True)

  def testDirectInnerWrapperRouting(self):
    env = os.environ.copy()
    env.pop('VPYTHON_BYPASS', None)
    env.pop('DEPOT_TOOLS_PYTHON_BYPASS', None)
    res_default = subprocess.run(
        [self.python_bin_cmd, '-c', 'import sys; print(sys.executable)'],
        capture_output=True,
        text=True,
        env=env,
    )
    self._assert_routing(res_default, expected_in_depot_tools=True)

    env['VPYTHON_BYPASS'] = '1'
    res_bypass = subprocess.run(
        [self.python_bin_cmd, '-c', 'import sys; print(sys.executable)'],
        capture_output=True,
        text=True,
        env=env,
    )
    self._assert_routing(res_bypass, expected_in_depot_tools=False)

  def testExitCodePropagation(self):
    env = os.environ.copy()
    for cmd in (self.python3_cmd, self.python_bin_cmd):
      res = subprocess.run(
          [cmd, '-c', 'import sys; sys.exit(42)'],
          capture_output=True,
          text=True,
          env=env,
      )
      self.assertEqual(res.returncode, 42)

  @unittest.skipIf(
      sys.platform == 'win32', 'POSIX-specific PATH filtering test'
  )
  def testBypassFailsCleanlyWithoutSystemPython(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      required_tools = [
          'bash',
          'sh',
          'dirname',
          'cat',
          'xargs',
          'tr',
          'grep',
          'paste',
          'pwd',
          'sed',
          'printf',
          'env',
      ]
      for tool in required_tools:
        src = shutil.which(tool)
        if src:
          try:
            os.symlink(src, os.path.join(tmpdir, tool))
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
