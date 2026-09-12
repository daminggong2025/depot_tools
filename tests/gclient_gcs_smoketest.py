#!/usr/bin/env vpython3
# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Smoke tests for gclient.py.

Shell out 'gclient' and run gcs tests.
"""

import logging
import os
import sys
import unittest

import gclient_smoketest_base

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GClientSmokeGcs(gclient_smoketest_base.GClientSmokeBase):
    def setUp(self):
        super(GClientSmokeGcs, self).setUp()
        self.enabled = self.FAKE_REPOS.set_up_git()
        if not self.enabled:
            self.skipTest("git fake repos not available")
        self.env["PATH"] = (
            os.path.join(ROOT_DIR, "testing_support")
            + os.pathsep
            + self.env["PATH"]
        )

    def testSyncGcs(self):
        self.gclient(
            ["config", self.git_base + "repo_22", "--name", "src", "--managed"]
        )
        self.gclient(["sync"])

        tree = self.mangle_git_tree(("repo_22@1", "src"))
        tree.update(
            {
                "src/another_gcs_dep/extracted_dir/extracted_file": "extracted text",
                "src/zip_gcs_dep/extracted_dir/extracted_file": "extracted text",
                "src/gcs_dep/extracted_dir/extracted_file": "extracted text",
                "src/gcs_dep_with_output_file/clang-format-no-extract": "non-extractable file",
            }
        )
        self.assertTree(tree)

    def testRevInfo(self):
        self.gclient(
            ["config", self.git_base + "repo_22", "--name", "src", "--managed"]
        )
        self.gclient(["sync"])
        results = self.gclient(["revinfo"])
        out = (
            "src: %(base)srepo_22\n"
            "src/another_gcs_dep:Linux/llvmfile.tar.gz: "
            "gs://456bucket/Linux/llvmfile.tar.gz\n"
            "src/gcs_dep:deadbeef: gs://123bucket/deadbeef\n"
            "src/gcs_dep_with_output_file:clang-format-version123: "
            "gs://789bucket/clang-format-version123\n"
            "src/zip_gcs_dep:windows/payload.zip: "
            "gs://456bucket/windows/payload.zip\n"
            % {
                "base": self.git_base,
            }
        )
        self.check((out, "", 0), results)

    def testRevInfoActual(self):
        self.gclient(
            ["config", self.git_base + "repo_22", "--name", "src", "--managed"]
        )
        self.gclient(["sync"])
        results = self.gclient(["revinfo", "--actual"])
        out = (
            "src: %(base)srepo_22@%(hash1)s\n"
            "src/another_gcs_dep:Linux/llvmfile.tar.gz: "
            "gs://456bucket/Linux/llvmfile.tar.gz\n"
            "src/gcs_dep:deadbeef: gs://123bucket/deadbeef\n"
            "src/gcs_dep_with_output_file:clang-format-version123: "
            "gs://789bucket/clang-format-version123\n"
            "src/zip_gcs_dep:windows/payload.zip: "
            "gs://456bucket/windows/payload.zip\n"
            % {
                "base": self.git_base,
                "hash1": self.githash("repo_22", 1),
            }
        )
        self.check((out, "", 0), results)

    def testSyncGcsWithReferenceDir(self):
        # Initial sync to populate GCS deps in reference directory
        self.gclient(
            ["config", self.git_base + "repo_22", "--name", "src", "--managed"]
        )
        self.gclient(["sync"])

        ref_dir = os.path.join(self.root_dir, "reference_src")
        src_dir = os.path.join(self.root_dir, "src")
        # Rename initial checkout to act as the reference checkout
        os.rename(src_dir, ref_dir)

        # Sync a fresh checkout using --reference-dir
        self.gclient(["sync", f"--reference-dir={ref_dir}"])

        self.assertTrue(
            os.path.exists(
                os.path.join(
                    src_dir, "gcs_dep", "extracted_dir", "extracted_file"
                )
            )
        )
        self.assertTrue(
            os.path.exists(
                os.path.join(
                    src_dir,
                    "gcs_dep_with_output_file",
                    "clang-format-no-extract",
                )
            )
        )


if __name__ == "__main__":
    if "-v" in sys.argv:
        logging.basicConfig(level=logging.DEBUG)
    unittest.main()
