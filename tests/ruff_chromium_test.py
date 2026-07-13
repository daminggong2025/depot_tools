#!/usr/bin/env vpython3
#
# [VPYTHON:BEGIN]
# python_version: "3.11"
# wheel: <
#   name: "infra/python/wheels/ruff/${vpython_platform}"
#   version: "version:0.15.17"
# >
# wheel: <
#   name: "infra/python/wheels/yapf-py3"
#   version: "version:0.40.2"
# >
# wheel: <
#   name: "infra/python/wheels/platformdirs-py3"
#   version: "version:4.1.0"
# >
# wheel: <
#   name: "infra/python/wheels/importlib-metadata-py3"
#   version: "version:7.0.0"
# >
# wheel: <
#   name: "infra/python/wheels/tomli-py3"
#   version: "version:2.0.1"
# >
# wheel: <
#  name: "infra/python/wheels/zipp-py3"
#  version: "version:3.7.0"
# >
# [VPYTHON:END]

# Copyright 2026 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import importlib.util
from importlib.machinery import SourceFileLoader
import os
import sys
import json
import io
import unittest
from unittest.mock import Mock, patch
import tempfile
import shutil

# Load depot_tools/ruff_chromium directly by file path to avoid naming collision with ruff package in site-packages
test_dir = os.path.dirname(os.path.abspath(__file__))
depot_tools_dir = os.path.dirname(test_dir)
ruff_path = os.path.join(depot_tools_dir, "ruff_chromium")
loader = SourceFileLoader("depot_tools_ruff_chromium", ruff_path)
spec = importlib.util.spec_from_loader("depot_tools_ruff_chromium",
                                       loader,
                                       origin=ruff_path)
assert spec is not None, f"Failed to load spec from {ruff_path}"
depot_tools_ruff = importlib.util.module_from_spec(spec)
sys.modules["depot_tools_ruff_chromium"] = depot_tools_ruff
loader.exec_module(depot_tools_ruff)

should_use_ruff = depot_tools_ruff.should_use_ruff
translate_args = depot_tools_ruff.translate_args
has_yapf_config = depot_tools_ruff.has_yapf_config
LineRange = depot_tools_ruff.LineRange
extract_root_flag = depot_tools_ruff.extract_root_flag
run_batch = depot_tools_ruff.run_batch



class TestHasYapfConfig(unittest.TestCase):

    def setUp(self):
        depot_tools_ruff._dir_config_cache.clear()
        self.test_dir = tempfile.mkdtemp(prefix="ruff_test_")
        self.old_cwd = os.getcwd()
        os.chdir(self.test_dir)

    def tearDown(self):
        os.chdir(self.old_cwd)
        shutil.rmtree(self.test_dir)

    def write_file(self, rel_path, content=""):
        abs_path = os.path.join(self.test_dir, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        return abs_path

    def test_style_yapf_present(self):
        self.write_file(".style.yapf", "")
        self.assertTrue(has_yapf_config("foo.py"))
        self.assertTrue(has_yapf_config("a/b/foo.py"))

    def test_pyproject_toml_with_yapf(self):
        self.write_file("pyproject.toml", "[tool.yapf]\n")
        self.assertTrue(has_yapf_config("foo.py"))

    def test_ruff_only_no_yapf_config(self):
        self.write_file("ruff.toml", 'exclude = ["foo.py"]\n')
        self.assertFalse(has_yapf_config("foo.py"))

    def test_no_config(self):
        self.assertFalse(has_yapf_config("foo.py"))



class TestShouldUseRuffRouting(unittest.TestCase):

    def setUp(self):
        depot_tools_ruff._dir_config_cache.clear()
        self.test_dir = tempfile.mkdtemp(prefix="ruff_test_")
        self.old_cwd = os.getcwd()
        os.chdir(self.test_dir)

    def tearDown(self):
        os.chdir(self.old_cwd)
        shutil.rmtree(self.test_dir)

    def write_file(self, rel_path, content=""):
        abs_path = os.path.join(self.test_dir, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        return abs_path

    def test_ruff_toml_only(self):
        self.write_file("ruff.toml", "")
        self.assertTrue(should_use_ruff("foo.py"))

    def test_pyproject_toml_with_ruff(self):
        self.write_file("pyproject.toml", "[tool.ruff]\n")
        self.assertTrue(should_use_ruff("foo.py"))

    def test_pyproject_toml_with_ruff_and_pyink(self):
        self.write_file("pyproject.toml", "[tool.ruff]\n[tool.pyink]\n")
        self.assertFalse(should_use_ruff("foo.py"))

    def test_pyproject_toml_with_ruff_and_black(self):
        self.write_file("pyproject.toml", "[tool.ruff]\n[tool.black]\n")
        self.assertFalse(should_use_ruff("foo.py"))

    def test_closest_config_wins(self):
        self.write_file("pyproject.toml", "[tool.black]\n")
        self.write_file("a/b/ruff.toml", "")
        self.assertTrue(should_use_ruff("a/b/foo.py"))

    def test_ignore_child_directory_config(self):
        self.write_file("a/b/ruff.toml", "")
        self.write_file("a/.style.yapf", "")
        self.assertFalse(should_use_ruff("a/foo.py"))

    def test_ignore_child_directory_config_no_parent(self):
        self.write_file("a/b/ruff.toml", "")
        self.assertFalse(should_use_ruff("a/foo.py"))

    def test_root_dir_boundary_stops_traversal(self):
        self.write_file("ruff.toml", "")
        self.write_file(".style.yapf", "")
        sub_dir = os.path.join(self.test_dir, "subrepo")
        os.makedirs(sub_dir, exist_ok=True)
        self.assertFalse(should_use_ruff("subrepo/foo.py", root_dir=sub_dir))
        self.assertFalse(has_yapf_config("subrepo/foo.py", root_dir=sub_dir))

    def test_extract_root_flag(self):
        asc_dir = "/foo/bar"
        if sys.platform == "win32":
            asc_dir = "C:\\foo\\bar"
        root, remaining = extract_root_flag(
            ["--root", "/foo/bar", "format", "baz.py"])
        self.assertEqual(root, asc_dir)
        self.assertEqual(remaining, ["format", "baz.py"])

        root2, remaining2 = extract_root_flag(
            ["--top-dir=/foo/bar", "format", "baz.py"])
        self.assertEqual(root2, asc_dir)
        self.assertEqual(remaining2, ["format", "baz.py"])


class TestTranslateArgs(unittest.TestCase):

    def test_translate_range_space(self):
        got = translate_args(["format", "--range", "5:1-10:1", "foo.py"])
        self.assertEqual(got, ["--line", "5-10", "foo.py", "-i"])

    def test_translate_range_equals(self):
        got = translate_args(["format", "--range=5:1-10:1", "foo.py"])
        self.assertEqual(got, ["--line", "5-10", "foo.py", "-i"])

    def test_translate_diff_and_stdin(self):
        got = translate_args(["format", "--diff", "--range=1:10-3:20", "-"])
        self.assertEqual(got, ["--diff", "--line", "1-3", "-"])








class TestBatchMode(unittest.TestCase):

    def setUp(self):
        depot_tools_ruff._dir_config_cache.clear()
        self.test_dir = tempfile.mkdtemp(prefix="ruff_batch_test_")
        self.old_cwd = os.getcwd()
        os.chdir(self.test_dir)
        patcher = patch('depot_tools_ruff_chromium.get_ruff_bin', return_value='ruff')
        self.mock_get_ruff_bin = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        os.chdir(self.old_cwd)
        shutil.rmtree(self.test_dir)

    def write_file(self, rel_path, content=""):
        abs_path = os.path.join(self.test_dir, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        return abs_path

    def test_batch_empty(self):
        config = {"files": []}
        with patch('sys.stdin', io.StringIO(json.dumps(config))):
            ret = run_batch()
        self.assertEqual(0, ret)

    @patch('depot_tools_ruff_chromium.should_use_ruff')
    @patch('subprocess.run')
    @patch('yapf.FormatFiles')
    def test_batch_ruff_no_range(self, mock_yapf, mock_run, mock_should_use):
        config = {
            "root": self.test_dir,
            "files": [{"path": "foo.py", "ranges": []}]
        }
        mock_should_use.return_value = True
        mock_run.return_value = Mock(returncode=0)

        with patch('sys.stdin', io.StringIO(json.dumps(config))):
            ret = run_batch()

        self.assertEqual(0, ret)
        mock_run.assert_called_once_with(
            ["ruff", "format", "foo.py"],
            capture_output=True
        )
        mock_yapf.assert_not_called()

    @patch('depot_tools_ruff_chromium.should_use_ruff')
    @patch('subprocess.run')
    def test_batch_ruff_with_ranges(self, mock_run, mock_should_use):
        config = {
            "root": self.test_dir,
            "files": [{"path": "foo.py", "ranges": [[1, 3]]}]
        }
        mock_should_use.return_value = True
        mock_run.return_value = Mock(returncode=0)

        with patch('sys.stdin', io.StringIO(json.dumps(config))):
            ret = run_batch()

        self.assertEqual(0, ret)
        mock_run.assert_called_once_with(
            ["ruff", "format", "foo.py"],
            capture_output=True
        )

    @patch('depot_tools_ruff_chromium.should_use_ruff')
    @patch('yapf.FormatFiles')
    def test_batch_yapf(self, mock_format, mock_should_use):
        foo_path = self.write_file("foo.py", "def foo():pass\n")
        config = {
            "root": self.test_dir,
            "files": [{"path": foo_path, "ranges": [[1, 3]]}]
        }
        mock_should_use.return_value = False
        mock_format.return_value = True

        with patch('sys.stdin', io.StringIO(json.dumps(config))):
            ret = run_batch()

        self.assertEqual(0, ret)
        mock_format.assert_called_once_with(
            [foo_path],
            lines=[(1, 2)],
            style_config="pep8",
            in_place=True,
            print_diff=False,
            quiet=False
        )

    @patch('depot_tools_ruff_chromium.should_use_ruff')
    @patch('yapf.FormatFiles')
    def test_batch_yapf_dry_run_changes(self, mock_format, mock_should_use):
        foo_path = self.write_file("foo.py", "def foo():pass\n")
        config = {
            "root": self.test_dir,
            "dry_run": True,
            "files": [{"path": foo_path, "ranges": []}]
        }
        mock_should_use.return_value = False
        mock_format.return_value = True

        with patch('sys.stdin', io.StringIO(json.dumps(config))):
            ret = run_batch()

        self.assertEqual(2, ret)
        mock_format.assert_called_once_with(
            [foo_path],
            lines=None,
            style_config="pep8",
            in_place=False,
            print_diff=False,
            quiet=True
        )
if __name__ == "__main__":
    unittest.main()
