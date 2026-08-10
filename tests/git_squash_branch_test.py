#!/usr/bin/env vpython3
# coding=utf-8
# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for git_squash_branch."""

import os
import sys
import unittest
from unittest import mock

DEPOT_TOOLS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DEPOT_TOOLS_ROOT)

from testing_support import git_test_utils

import git_squash_branch
import git_common
import subprocess2

git_common.TEST_MODE = True


class GitSquashBranchTest(git_test_utils.GitRepoReadWriteTestBase):
    REPO_SCHEMA = """
  """

    def setUp(self):
        super(GitSquashBranchTest, self).setUp()

        # Create a repo with the following schema:
        #
        # main <- branchA <- branchB <- branchC
        #            ^
        #            \ branchD
        #
        # where each branch has 2 commits.
        self.repo.git("commit", "-m", "First commit", "--allow-empty")

        self.repo.git("checkout", "-B", "branchA", "--track", "main")
        self._createFileAndCommit("fileA1")
        self._createFileAndCommit("fileA2")

        self.repo.git("checkout", "-B", "branchB", "--track", "branchA")
        self._createFileAndCommit("fileB1")
        self._createFileAndCommit("fileB2")

        self.repo.git("checkout", "-B", "branchC", "--track", "branchB")
        self._createFileAndCommit("fileC1")
        self._createFileAndCommit("fileC2")

        self.repo.git("checkout", "-B", "branchD", "--track", "branchA")
        self._createFileAndCommit("fileD1")
        self._createFileAndCommit("fileD2")

    # =========================================================================
    # Core Functionality Tests
    # =========================================================================

    def testSquashRoot(self):
        self._assertCounts(
            {"branchA": 2, "branchB": 2, "branchC": 2, "branchD": 2}
        )

        self.repo.git("checkout", "branchA")
        self.repo.run(git_squash_branch.main, [])

        self._assertCounts(
            {"branchA": 1, "branchB": 2, "branchC": 2, "branchD": 2}
        )

    def testSquashLeaf(self):
        self._assertCounts(
            {"branchA": 2, "branchB": 2, "branchC": 2, "branchD": 2}
        )

        self.repo.git("checkout", "branchD")
        self.repo.run(git_squash_branch.main, [])

        self._assertCounts(
            {"branchA": 2, "branchB": 2, "branchC": 2, "branchD": 1}
        )

    def testSquashSequential(self):
        self._assertCounts(
            {"branchA": 2, "branchB": 2, "branchC": 2, "branchD": 2}
        )

        self.repo.git("checkout", "branchA")
        self.repo.run(git_squash_branch.main, [])
        self._assertCounts(
            {"branchA": 1, "branchB": 2, "branchC": 2, "branchD": 2}
        )

        self.repo.git("checkout", "branchB")
        self.repo.run(git_squash_branch.main, [])
        self._assertCounts(
            {"branchA": 1, "branchB": 1, "branchC": 2, "branchD": 2}
        )

    def testSquashCustomMessage(self):
        self.repo.git("checkout", "branchD")
        self.repo.run(
            git_squash_branch.main, ["-m", "Custom squash header message"]
        )
        commit_msg = self.repo.git("log", "-1", "--format=%B", "branchD").stdout
        self.assertTrue(commit_msg.startswith("Custom squash header message"))
        self.assertIn("Added file fileD1", commit_msg)
        self.assertIn("Added file fileD2", commit_msg)

    def testPreservesDownstreamMetadata(self):
        self.repo.git("checkout", "branchA")
        self.repo.run(git_squash_branch.main, [])

        self._assertCounts(
            {"branchA": 1, "branchB": 2, "branchC": 2, "branchD": 2}
        )

        # Verify downstream commits on branchB, branchC preserved commit messages
        log_b = self.repo.git("log", "--format=%B", "branchA..branchB").stdout
        self.assertIn("Added file fileB1", log_b)
        self.assertIn("Added file fileB2", log_b)

        log_c = self.repo.git("log", "--format=%B", "branchB..branchC").stdout
        self.assertIn("Added file fileC1", log_c)
        self.assertIn("Added file fileC2", log_c)

    # =========================================================================
    # Edge Cases & Error Handling Tests
    # =========================================================================

    def testAtomicRollbackOnFailure(self):
        self._assertCounts(
            {"branchA": 2, "branchB": 2, "branchC": 2, "branchD": 2}
        )

        self.repo.git("checkout", "branchA")
        real_run = git_common.run

        def run_with_corrupted_transaction(*args, **kwargs):
            if args and args[0] == "update-ref" and "--stdin" in args:
                indata = kwargs.get("indata", b"").decode("utf-8")
                tokens = indata.rstrip("\0").split("\0")
                tokens[-1] = (
                    "0" * 40
                )  # Corrupt expected old SHA to force failure
                kwargs["indata"] = ("\0".join(tokens) + "\0").encode("utf-8")
            return real_run(*args, **kwargs)

        with mock.patch(
            "git_common.run", side_effect=run_with_corrupted_transaction
        ):
            with self.assertRaises(subprocess2.CalledProcessError):
                self.repo.run(git_squash_branch.main, [])

        # Assert zero branches were modified due to atomic rollback
        self._assertCounts(
            {"branchA": 2, "branchB": 2, "branchC": 2, "branchD": 2}
        )

    def testEmptyBranch(self):
        self.repo.git("checkout", "-B", "emptyBranch", "--track", "main")
        self._createFileAndCommit("tempFile")
        self.repo.git("rm", "tempFile")
        self.repo.git_commit("Removed tempFile")

        self.assertFalse(self.repo.run(git_common.squash_branch, "emptyBranch"))
        self.assertEqual(
            self.repo.run(git_common.hash_one, "emptyBranch"),
            self.repo.run(git_common.hash_one, "main"),
        )

    def testUpstreamAdvanced(self):
        self.repo.git("checkout", "-B", "featureBranch", "--track", "main")
        self._createFileAndCommit("featureFile1")
        self._createFileAndCommit("featureFile2")
        self.assertEqual(self._getCountAheadOfUpstream("featureBranch"), 2)

        # Advance upstream 'main' with a commit that featureBranch does not have
        self.repo.git("checkout", "main")
        self._createFileAndCommit("mainFileAfterBranching")

        # Checkout featureBranch and squash it
        self.repo.git("checkout", "featureBranch")
        merge_base_before = self.repo.run(
            git_common.get_or_create_merge_base, "featureBranch"
        )
        self.assertTrue(
            self.repo.run(git_common.squash_branch, "featureBranch")
        )

        # Parent of squash commit must be the merge base, not the advanced tip of main
        parent_of_squash = self.repo.git(
            "rev-parse", "featureBranch^"
        ).stdout.strip()
        self.assertEqual(parent_of_squash, merge_base_before)
        feature_files = self.repo.git(
            "ls-tree", "--name-only", "featureBranch"
        ).stdout
        self.assertIn("featureFile1", feature_files)
        self.assertNotIn("mainFileAfterBranching", feature_files)

    def testFailsWithDivergedBranch(self):
        self._assertCounts(
            {"branchA": 2, "branchB": 2, "branchC": 2, "branchD": 2}
        )

        self.repo.git("checkout", "branchB")
        self._createFileAndCommit("fileB3")
        self.repo.git("checkout", "branchA")

        # branchC has now diverged from branchB
        output, _ = self.repo.capture_stdio(git_squash_branch.main, [])
        self.assertIn("some children have diverged", output)

    def testFailsWithNoUpstream(self):
        self.repo.git("checkout", "--orphan", "orphanBranch")
        self._createFileAndCommit("orphanFile")
        ret = self.repo.run(git_squash_branch.main, [])
        self.assertEqual(ret, 1)

    def testFailsWhenTreeDirty(self):
        self.repo.git("checkout", "branchD")
        with self.repo.open("fileD1", "w") as f:
            f.write("dirty content")
        with self.assertRaisesRegex(RuntimeError, "uncommitted changes"):
            self.repo.run(git_common.squash_branch, "branchD")

    def testFailsWhenInRebase(self):
        self.repo.git("checkout", "branchD")
        with mock.patch("git_common.in_rebase", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "rebase is in progress"):
                self.repo.run(git_common.squash_branch, "branchD")

    def testFailsInDetachedHead(self):
        self.repo.git("checkout", "HEAD~0")
        with self.assertRaises(AssertionError):
            self.repo.run(git_common.squash_current_branch)

    # =========================================================================
    # Helpers
    # =========================================================================

    def _assertCounts(self, expected):
        for branch, count in expected.items():
            self.assertEqual(
                self._getCountAheadOfUpstream(branch),
                count,
                f"Branch {branch} expected {count} commits ahead of upstream",
            )

    def _createFileAndCommit(self, filename):
        with self.repo.open(filename, "w") as f:
            f.write("content")
        self.repo.git("add", filename)
        self.repo.git_commit("Added file " + filename)

    def _getCountAheadOfUpstream(self, branch):
        upstream = branch + "@{u}"
        output = self.repo.git(
            "rev-list", "--count", upstream + ".." + branch
        ).stdout
        return int(output)


if __name__ == "__main__":
    unittest.main()
