#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for the git recipe module's git_setup resource."""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# git_setup lives in a recipe module resources/ directory that is not on the
# import path, so load it directly from its file location.
_GIT_SETUP_PATH = os.path.join(
    ROOT, "recipes", "recipe_modules", "git", "resources", "git_setup.py"
)
_spec = importlib.util.spec_from_file_location("git_setup", _GIT_SETUP_PATH)
git_setup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(git_setup)


class ObjectFormatDetectionTest(unittest.TestCase):
    """Real-repo object-format detection through git_setup.

    git_setup delegates to scm.GIT.GetRemoteObjectFormat via its depot_tools
    root import bootstrap, so these tests also verify that `import scm` works
    in the recipe resource context.
    """

    def _makeRepo(self, object_format):
        repo = tempfile.mkdtemp(suffix=f"{object_format}.git")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        git = "git.bat" if sys.platform == "win32" else "git"
        subprocess.check_call(
            [git, "init", "-q", f"--object-format={object_format}", "."],
            cwd=repo,
        )
        subprocess.check_call(
            [
                git,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@test.com",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "init",
            ],
            cwd=repo,
        )
        return repo

    def testGetRemoteObjectFormat(self):
        cases = [
            ("sha1 repo", lambda: self._makeRepo("sha1"), "sha1"),
            ("sha256 repo", lambda: self._makeRepo("sha256"), "sha256"),
            (
                "unreachable remote",
                lambda: os.path.join(
                    tempfile.gettempdir(), "definitely_nonexistent"
                ),
                "sha1",
            ),
        ]
        for label, make_path, expected in cases:
            with self.subTest(label):
                self.assertEqual(
                    git_setup.scm.GIT.GetRemoteObjectFormat(make_path()),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
