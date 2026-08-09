#!/usr/bin/env vpython3
# coding=utf-8
# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for git_squash_branch_tree."""

import os
import sys
import unittest
from unittest import mock

DEPOT_TOOLS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DEPOT_TOOLS_ROOT)

from testing_support import git_test_utils

import git_squash_branch_tree
import git_common
import subprocess2

git_common.TEST_MODE = True


class GitSquashBranchTreeTest(git_test_utils.GitRepoReadWriteTestBase):
    # Empty repo.
    REPO_SCHEMA = """
  """

    def setUp(self):
        super(GitSquashBranchTreeTest, self).setUp()

        # Note: Using the REPO_SCHEMA wouldn't simplify this test so it is not
        #       used.
        #
        # Create a repo with the follow schema
        #
        # main <- branchA <- branchB
        #            ^
        #            \ branchC
        #
        # where each branch has 2 commits.

        # The repo is empty. Add the first commit or else most commands don't
        # work, including `git branch`, which doesn't even show the main branch.
        self.repo.git("commit", "-m", "First commit", "--allow-empty")

        # Create the first branch downstream from `main` with 2 commits.
        self.repo.git("checkout", "-B", "branchA", "--track", "main")
        self._createFileAndCommit("fileA1")
        self._createFileAndCommit("fileA2")

        # Create a branch downstream from `branchA` with 2 commits.
        self.repo.git("checkout", "-B", "branchB", "--track", "branchA")
        self._createFileAndCommit("fileB1")
        self._createFileAndCommit("fileB2")

        # Create another branch downstream from `branchA` with 2 commits.
        self.repo.git("checkout", "-B", "branchC", "--track", "branchA")
        self._createFileAndCommit("fileC1")
        self._createFileAndCommit("fileC2")

    def testGitSquashBranchTreeDefaultCurrent(self):
        self.assertEqual(self._getCountAheadOfUpstream("branchA"), 2)
        self.assertEqual(self._getCountAheadOfUpstream("branchB"), 2)
        self.assertEqual(self._getCountAheadOfUpstream("branchC"), 2)

        # Note: Passing --ignore-no-upstream as this repo has no remote and so
        # the `main` branch can't have an upstream.
        self.repo.git("checkout", "branchB")
        self.repo.run(git_squash_branch_tree.main, ["--ignore-no-upstream"])

        self.assertEqual(self._getCountAheadOfUpstream("branchA"), 2)
        self.assertEqual(self._getCountAheadOfUpstream("branchB"), 1)
        self.assertEqual(self._getCountAheadOfUpstream("branchC"), 2)

    def testGitSquashBranchTreeAll(self):
        self.assertEqual(self._getCountAheadOfUpstream("branchA"), 2)
        self.assertEqual(self._getCountAheadOfUpstream("branchB"), 2)
        self.assertEqual(self._getCountAheadOfUpstream("branchC"), 2)

        self.repo.run(
            git_squash_branch_tree.main,
            ["--branch", "branchA", "--ignore-no-upstream"],
        )

        self.assertEqual(self._getCountAheadOfUpstream("branchA"), 1)
        self.assertEqual(self._getCountAheadOfUpstream("branchB"), 1)
        self.assertEqual(self._getCountAheadOfUpstream("branchC"), 1)

    def testGitSquashBranchTreeSingle(self):
        self.assertEqual(self._getCountAheadOfUpstream("branchA"), 2)
        self.assertEqual(self._getCountAheadOfUpstream("branchB"), 2)
        self.assertEqual(self._getCountAheadOfUpstream("branchC"), 2)

        self.repo.run(
            git_squash_branch_tree.main,
            ["--branch", "branchB", "--ignore-no-upstream"],
        )

        self.assertEqual(self._getCountAheadOfUpstream("branchA"), 2)
        self.assertEqual(self._getCountAheadOfUpstream("branchB"), 1)
        self.assertEqual(self._getCountAheadOfUpstream("branchC"), 2)

        self.repo.run(
            git_squash_branch_tree.main,
            ["--branch", "branchC", "--ignore-no-upstream"],
        )

        self.assertEqual(self._getCountAheadOfUpstream("branchA"), 2)
        self.assertEqual(self._getCountAheadOfUpstream("branchB"), 1)
        self.assertEqual(self._getCountAheadOfUpstream("branchC"), 1)

        self.repo.run(
            git_squash_branch_tree.main,
            ["--branch", "branchA", "--ignore-no-upstream"],
        )

        self.assertEqual(self._getCountAheadOfUpstream("branchA"), 1)
        self.assertEqual(self._getCountAheadOfUpstream("branchB"), 1)
        self.assertEqual(self._getCountAheadOfUpstream("branchC"), 1)

    def testGitSquashBranchTreeAtomicFailure(self):
        self.assertEqual(self._getCountAheadOfUpstream("branchA"), 2)
        self.assertEqual(self._getCountAheadOfUpstream("branchB"), 2)
        self.assertEqual(self._getCountAheadOfUpstream("branchC"), 2)

        real_run = git_common.run

        def run_with_corrupted_transaction(*args, **kwargs):
            if args and args[0] == "update-ref" and "--stdin" in args:
                indata = kwargs.get("indata", b"").decode("utf-8")
                # Corrupt the expected old SHA for branchC to force transaction failure
                lines = indata.strip().split("\n")
                lines[-1] = lines[-1].rsplit(" ", 1)[0] + " " + ("0" * 40)
                kwargs["indata"] = ("\n".join(lines) + "\n").encode("utf-8")
            return real_run(*args, **kwargs)

        with mock.patch(
            "git_common.run", side_effect=run_with_corrupted_transaction
        ):
            with self.assertRaises(subprocess2.CalledProcessError):
                self.repo.run(
                    git_squash_branch_tree.main,
                    ["--branch", "branchA", "--ignore-no-upstream"],
                )

        # Assert that zero branches were modified due to the atomic rollback
        self.assertEqual(self._getCountAheadOfUpstream("branchA"), 2)
        self.assertEqual(self._getCountAheadOfUpstream("branchB"), 2)
        self.assertEqual(self._getCountAheadOfUpstream("branchC"), 2)

    def testGitSquashBranchTreeEmptyBranch(self):
        # Make branchB have no net changes relative to branchA
        self.repo.git("checkout", "branchB")
        self.repo.git("rm", "fileB1", "fileB2")
        self.repo.git_commit("Reverted branchB files")

        self.repo.run(
            git_squash_branch_tree.main,
            ["--branch", "branchA", "--ignore-no-upstream"],
        )

        self.assertEqual(self._getCountAheadOfUpstream("branchA"), 1)
        # branchB should now point to branchA
        self.assertEqual(
            self.repo.run(git_common.hash_one, "branchB"),
            self.repo.run(git_common.hash_one, "branchA"),
        )
        self.assertEqual(self._getCountAheadOfUpstream("branchC"), 1)

    # Creates a file with arbitrary contents and commit it to the current
    # branch.
    def _createFileAndCommit(self, filename):
        with self.repo.open(filename, "w") as f:
            f.write("content")
        self.repo.git("add", filename)
        self.repo.git_commit("Added file " + filename)

    # Returns the count of how many commits `branch` is ahead of its upstream.
    def _getCountAheadOfUpstream(self, branch):
        upstream = branch + "@{u}"
        output = self.repo.git(
            "rev-list", "--count", upstream + ".." + branch
        ).stdout
        return int(output)


if __name__ == "__main__":
    unittest.main()
