#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import shutil
import subprocess
import sys
import tempfile
from typing import List
import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def wrappers_to_test() -> List[str]:
  wrappers = [
      os.path.join(
          ROOT_DIR, 'python3.bat' if sys.platform == 'win32' else 'python3'
      ),
      os.path.join(
          ROOT_DIR,
          'python-bin',
          'python3.bat' if sys.platform == 'win32' else 'python3',
      ),
  ]
  if sys.platform == 'win32':
    wrappers.append(os.path.join(ROOT_DIR, 'bootstrap', 'python3.bat'))
  return wrappers


def _assert_routing(
    res: subprocess.CompletedProcess, expected_in_depot_tools: bool
) -> None:
  assert res.returncode == 0, res.stderr
  exec_path = os.path.abspath(res.stdout.strip())
  is_in_depot_tools = exec_path.startswith(ROOT_DIR)
  if expected_in_depot_tools:
    assert (
        is_in_depot_tools
    ), f'Expected CIPD python inside {ROOT_DIR}, got {exec_path}'
  else:
    assert (
        not is_in_depot_tools
    ), f'Expected system python outside {ROOT_DIR}, got {exec_path}'


def test_default_routing_to_cipd(wrappers_to_test: List[str]) -> None:
  env = os.environ.copy()
  env.pop('VPYTHON_BYPASS', None)
  env.pop('DEPOT_TOOLS_PYTHON_BYPASS', None)
  for cmd in wrappers_to_test:
    res = subprocess.run(
        [cmd, '-c', 'import sys; print(sys.executable)'],
        capture_output=True,
        text=True,
        env=env,
    )
    _assert_routing(res, expected_in_depot_tools=True)


def test_vpython_bypass_uses_system_python(wrappers_to_test: List[str]) -> None:
  env = os.environ.copy()
  env['VPYTHON_BYPASS'] = '1'
  for cmd in wrappers_to_test:
    res = subprocess.run(
        [cmd, '-c', 'import sys; print(sys.executable)'],
        capture_output=True,
        text=True,
        env=env,
    )
    _assert_routing(res, expected_in_depot_tools=False)


def test_depot_tools_bypass_uses_system_python(
    wrappers_to_test: List[str],
) -> None:
  env = os.environ.copy()
  env['DEPOT_TOOLS_PYTHON_BYPASS'] = '1'
  for cmd in wrappers_to_test:
    res = subprocess.run(
        [cmd, '-c', 'import sys; print(sys.executable)'],
        capture_output=True,
        text=True,
        env=env,
    )
    _assert_routing(res, expected_in_depot_tools=False)


def test_bypass_zero_does_not_bypass(wrappers_to_test: List[str]) -> None:
  env = os.environ.copy()
  env['VPYTHON_BYPASS'] = '0'
  env['DEPOT_TOOLS_PYTHON_BYPASS'] = '0'
  for cmd in wrappers_to_test:
    res = subprocess.run(
        [cmd, '-c', 'import sys; print(sys.executable)'],
        capture_output=True,
        text=True,
        env=env,
    )
    _assert_routing(res, expected_in_depot_tools=True)


@pytest.mark.parametrize('vpython_val, depot_val', [('0', '1'), ('1', '0')])
def test_bypass_variable_or_logic(
    wrappers_to_test: List[str], vpython_val: str, depot_val: str
) -> None:
  env = os.environ.copy()
  env['VPYTHON_BYPASS'] = vpython_val
  env['DEPOT_TOOLS_PYTHON_BYPASS'] = depot_val
  for cmd in wrappers_to_test:
    res = subprocess.run(
        [cmd, '-c', 'import sys; print(sys.executable)'],
        capture_output=True,
        text=True,
        env=env,
    )
    _assert_routing(res, expected_in_depot_tools=False)


def test_argument_forwarding_with_spaces_and_quotes(
    wrappers_to_test: List[str],
) -> None:
  env = os.environ.copy()
  for cmd in wrappers_to_test:
    res = subprocess.run(
        [cmd, '-c', 'import sys; print(sys.argv[1])', 'hello world'],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == 'hello world'


def test_exit_code_propagation(wrappers_to_test: List[str]) -> None:
  env = os.environ.copy()
  for cmd in wrappers_to_test:
    res = subprocess.run(
        [cmd, '-c', 'import sys; sys.exit(42)'],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode == 42


def test_bypass_fails_cleanly_without_system_python() -> None:
  python_bin_cmd = os.path.join(
      ROOT_DIR,
      'python-bin',
      'python3.bat' if sys.platform == 'win32' else 'python3',
  )
  with tempfile.TemporaryDirectory() as tmpdir:
    if sys.platform != 'win32':
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
        [python_bin_cmd, '-c', 'import sys; print("Should not reach")'],
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )
    assert res.returncode != 0
    assert 'not found in PATH' in res.stderr


# Stanza to have pytest be executed.
if __name__ == '__main__':
  sys.exit(pytest.main([__file__] + sys.argv[1:]))
