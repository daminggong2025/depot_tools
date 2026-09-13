#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for GcsDependency and reference directory handling in gclient.py."""

import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import download_from_google_storage
import fs_util
import gclient
import git_cache


class GcsDependencyTest(unittest.TestCase):
    def setUp(self):
        super(GcsDependencyTest, self).setUp()
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

        parser = gclient.OptionParser()
        options, _ = parser.parse_args([])
        self.options = options
        self.client = gclient.GClient(root_dir=self.tmpdir, options=options)
        self.parent = gclient.Dependency(
            parent=self.client,
            name="src",
            url="https://example.com/repo.git",
            managed=False,
            custom_deps=None,
            custom_vars=None,
            custom_hooks=None,
            deps_file="DEPS",
            should_process=True,
            should_recurse=False,
            relative=False,
            condition=None,
        )

    def _make_gcs_dep(
        self,
        name="src/dep",
        bucket="test_bucket",
        object_name="pkg.tar.gz",
        sha256sum="1234567890abcdef" * 4,
        output_file=None,
        size_bytes=100,
        should_process=True,
    ):
        return gclient.GcsDependency(
            parent=self.parent,
            name=name,
            bucket=bucket,
            object_name=object_name,
            sha256sum=sha256sum,
            output_file=output_file,
            size_bytes=size_bytes,
            gcs_root=self.client.GetGcsRoot(),
            custom_vars=None,
            should_process=should_process,
            relative=False,
            condition=None,
        )

    def _create_tar_file(self, path, files_dict):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with tarfile.open(path, "w:gz") as tar:
            for arcname, content in files_dict.items():
                data = (
                    content.encode("utf-8")
                    if isinstance(content, str)
                    else content
                )
                ti = tarfile.TarInfo(name=arcname)
                ti.size = len(data)
                tar.addfile(ti, io.BytesIO(data))

    def _create_zip_file(self, path, files_dict):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with zipfile.ZipFile(path, "w") as zf:
            for arcname, content in files_dict.items():
                zf.writestr(arcname, content)

    def test_properties(self):
        dep = self._make_gcs_dep(
            name="src/custom_dir",
            object_name="subdir/file.bin",
            output_file="renamed.bin",
        )
        self.assertEqual(
            dep.output_dir, os.path.join(self.tmpdir, "src", "custom_dir")
        )
        self.assertEqual(dep.gcs_file_name, "subdir_file.bin")
        self.assertEqual(dep.file_prefix, "subdir_file_bin")
        self.assertEqual(
            dep.artifact_output_file,
            os.path.join(dep.output_dir, "renamed.bin"),
        )
        self.assertEqual(
            dep.hash_file,
            os.path.join(dep.output_dir, ".subdir_file_bin_hash"),
        )
        self.assertEqual(
            dep.migration_toggle_file,
            os.path.join(
                dep.output_dir,
                download_from_google_storage.construct_migration_file_name(
                    "subdir/file.bin"
                ),
            ),
        )
        self.assertEqual(dep.GetScmName(), "gcs")
        self.assertTrue(dep.verify_validity())

    def test_artifact_output_file_default_name(self):
        dep = self._make_gcs_dep(
            name="src/dep", object_name="tools/binary.tar.gz", output_file=None
        )
        self.assertEqual(
            dep.artifact_output_file,
            os.path.join(dep.output_dir, ".tools_binary.tar.gz"),
        )

    def test_get_reference_dir_cli_option_direct(self):
        ref_base = os.path.join(self.tmpdir, "ref_checkout")
        ref_target = os.path.join(ref_base, "src", "dep")
        os.makedirs(ref_target)
        self.options.reference_dir = ref_base
        dep = self._make_gcs_dep(name="src/dep")
        self.assertEqual(dep._GetReferenceDir(self.options), ref_target)

    def test_get_reference_dir_cli_option_without_src_prefix(self):
        ref_base = os.path.join(self.tmpdir, "ref_checkout")
        ref_target = os.path.join(ref_base, "dep")
        os.makedirs(ref_target)
        self.options.reference_dir = ref_base
        dep = self._make_gcs_dep(name="src/dep")
        self.assertEqual(dep._GetReferenceDir(self.options), ref_target)

    def test_get_reference_dir_cli_option_src_base(self):
        ref_src = os.path.join(self.tmpdir, "checkout", "src")
        ref_target = os.path.join(ref_src, "dep")
        os.makedirs(ref_target)
        self.options.reference_dir = ref_src
        dep = self._make_gcs_dep(name="src/dep")
        self.assertEqual(dep._GetReferenceDir(self.options), ref_target)

    def test_get_reference_dir_env_var(self):
        ref_base = os.path.join(self.tmpdir, "env_ref_checkout")
        ref_target = os.path.join(ref_base, "src", "dep")
        os.makedirs(ref_target)
        dep = self._make_gcs_dep(name="src/dep")
        with mock.patch.dict(os.environ, {"GCLIENT_REFERENCE_DIR": ref_base}):
            self.assertEqual(dep._GetReferenceDir(None), ref_target)

    def test_get_reference_dir_missing_config(self):
        dep = self._make_gcs_dep(name="src/dep")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(dep._GetReferenceDir(None))

    def test_get_reference_dir_nonexistent_dir(self):
        dep = self._make_gcs_dep(name="src/dep")
        self.options.reference_dir = os.path.join(self.tmpdir, "nonexistent")
        self.assertIsNone(dep._GetReferenceDir(self.options))

    def test_get_reference_dir_nonexistent_candidate(self):
        ref_base = os.path.join(self.tmpdir, "empty_ref")
        os.makedirs(ref_base)
        self.options.reference_dir = ref_base
        dep = self._make_gcs_dep(name="src/dep")
        self.assertIsNone(dep._GetReferenceDir(self.options))

    def test_get_matching_reference_dir_no_ref_dir(self):
        dep = self._make_gcs_dep()
        self.assertIsNone(dep.GetMatchingReferenceDir(self.options))

    def test_get_matching_reference_dir_missing_hash_file(self):
        ref_base = os.path.join(self.tmpdir, "ref_checkout")
        ref_target = os.path.join(ref_base, "src", "dep")
        os.makedirs(ref_target)
        self.options.reference_dir = ref_base
        dep = self._make_gcs_dep()
        self.assertIsNone(dep.GetMatchingReferenceDir(self.options))

    def test_get_matching_reference_dir_unreadable_hash_file(self):
        ref_base = os.path.join(self.tmpdir, "ref_checkout")
        ref_target = os.path.join(ref_base, "src", "dep")
        os.makedirs(ref_target)
        dep = self._make_gcs_dep()
        ref_hash_file = os.path.join(ref_target, f".{dep.file_prefix}_hash")
        with open(ref_hash_file, "w") as f:
            f.write(dep.sha256sum)
        self.options.reference_dir = ref_base

        with mock.patch(
            "builtins.open",
            mock.mock_open(),
            create=True,
        ) as mock_open:
            mock_open.side_effect = OSError("Permission denied")
            self.assertIsNone(dep.GetMatchingReferenceDir(self.options))

    def test_get_matching_reference_dir_mismatched_hash(self):
        ref_base = os.path.join(self.tmpdir, "ref_checkout")
        ref_target = os.path.join(ref_base, "src", "dep")
        os.makedirs(ref_target)
        dep = self._make_gcs_dep()
        ref_hash_file = os.path.join(ref_target, f".{dep.file_prefix}_hash")
        with open(ref_hash_file, "w") as f:
            f.write(
                "wronghash00000000000000000000000000000000000000000000000000000000\n"
            )
        self.options.reference_dir = ref_base
        self.assertIsNone(dep.GetMatchingReferenceDir(self.options))

    def test_get_matching_reference_dir_missing_artifact(self):
        ref_base = os.path.join(self.tmpdir, "ref_checkout")
        ref_target = os.path.join(ref_base, "src", "dep")
        os.makedirs(ref_target)
        dep = self._make_gcs_dep()
        ref_hash_file = os.path.join(ref_target, f".{dep.file_prefix}_hash")
        with open(ref_hash_file, "w") as f:
            f.write(dep.sha256sum + "\n")
        self.options.reference_dir = ref_base
        self.assertIsNone(dep.GetMatchingReferenceDir(self.options))

    def test_get_matching_reference_dir_mismatched_size(self):
        ref_base = os.path.join(self.tmpdir, "ref_checkout")
        ref_target = os.path.join(ref_base, "src", "dep")
        os.makedirs(ref_target)
        dep = self._make_gcs_dep(size_bytes=999)
        ref_hash_file = os.path.join(ref_target, f".{dep.file_prefix}_hash")
        with open(ref_hash_file, "w") as f:
            f.write(dep.sha256sum + "\n")
        ref_artifact = os.path.join(
            ref_target, dep.output_file or f".{dep.gcs_file_name}"
        )
        with open(ref_artifact, "w") as f:
            f.write("small")
        self.options.reference_dir = ref_base
        self.assertIsNone(dep.GetMatchingReferenceDir(self.options))

    def test_get_matching_reference_dir_success(self):
        ref_base = os.path.join(self.tmpdir, "ref_checkout")
        ref_target = os.path.join(ref_base, "src", "dep")
        os.makedirs(ref_target)
        dep = self._make_gcs_dep(size_bytes=14)
        ref_hash_file = os.path.join(ref_target, f".{dep.file_prefix}_hash")
        with open(ref_hash_file, "w") as f:
            f.write(dep.sha256sum + "\n")
        ref_artifact = os.path.join(
            ref_target, dep.output_file or f".{dep.gcs_file_name}"
        )
        with open(ref_artifact, "w") as f:
            f.write("exact 14 bytes")
        self.options.reference_dir = ref_base
        self.assertEqual(dep.GetMatchingReferenceDir(self.options), ref_target)

    def test_get_matching_reference_dir_success_none_size_bytes(self):
        ref_base = os.path.join(self.tmpdir, "ref_checkout")
        ref_target = os.path.join(ref_base, "src", "dep")
        os.makedirs(ref_target)
        dep = self._make_gcs_dep(size_bytes=None)
        ref_hash_file = os.path.join(ref_target, f".{dep.file_prefix}_hash")
        with open(ref_hash_file, "w") as f:
            f.write(dep.sha256sum + "\n")
        ref_artifact = os.path.join(
            ref_target, dep.output_file or f".{dep.gcs_file_name}"
        )
        with open(ref_artifact, "w") as f:
            f.write("any arbitrary length bytes")
        self.options.reference_dir = ref_base
        self.assertEqual(dep.GetMatchingReferenceDir(self.options), ref_target)

    def test_copy_from_reference_plain_file(self):
        ref_dir = os.path.join(self.tmpdir, "ref_dir")
        os.makedirs(ref_dir)
        dep = self._make_gcs_dep(
            object_name="plain.txt", output_file="plain.txt"
        )
        ref_artifact = os.path.join(ref_dir, "plain.txt")
        with open(ref_artifact, "w") as f:
            f.write("plain content")

        os.makedirs(dep.output_dir, exist_ok=True)
        stale_file = os.path.join(dep.output_dir, "plain.txt")
        with open(stale_file, "w") as f:
            f.write("old stale content")
        with open(dep.hash_file, "w") as f:
            f.write("old_hash")

        result = dep.CopyFromReference(ref_dir)
        self.assertTrue(result)
        self.assertTrue(os.path.isfile(dep.artifact_output_file))
        with open(dep.artifact_output_file, "r") as f:
            self.assertEqual(f.read(), "plain content")
        with open(dep.hash_file, "r") as f:
            self.assertEqual(f.read().strip(), dep.sha256sum)
        with open(dep.migration_toggle_file, "r") as f:
            self.assertEqual(f.read().strip(), "1")

    def test_copy_from_reference_archive_with_content_names(self):
        ref_dir = os.path.join(self.tmpdir, "ref_dir")
        os.makedirs(os.path.join(ref_dir, "extracted_folder"))
        dep = self._make_gcs_dep(object_name="archive.tar.gz")
        ref_artifact = os.path.join(ref_dir, f".{dep.gcs_file_name}")
        self._create_tar_file(
            ref_artifact, {"extracted_folder/sub.txt": "extracted data"}
        )
        with open(
            os.path.join(ref_dir, "extracted_folder", "sub.txt"), "w"
        ) as f:
            f.write("extracted data")
        ref_content_names = os.path.join(
            ref_dir, f".{dep.file_prefix}_content_names"
        )
        with open(ref_content_names, "w") as f:
            json.dump(["extracted_folder/sub.txt"], f)

        result = dep.CopyFromReference(ref_dir)
        self.assertTrue(result)
        self.assertTrue(os.path.isfile(dep.artifact_output_file))
        dest_file = os.path.join(dep.output_dir, "extracted_folder", "sub.txt")
        self.assertTrue(os.path.isfile(dest_file))
        with open(dest_file, "r") as f:
            self.assertEqual(f.read(), "extracted data")
        dest_content_names = os.path.join(
            dep.output_dir, f".{dep.file_prefix}_content_names"
        )
        self.assertTrue(os.path.isfile(dest_content_names))

    def test_copy_from_reference_archive_without_content_names(self):
        ref_dir = os.path.join(self.tmpdir, "ref_dir")
        os.makedirs(ref_dir)
        dep = self._make_gcs_dep(object_name="archive.tar.gz")
        ref_artifact = os.path.join(ref_dir, f".{dep.gcs_file_name}")
        self._create_tar_file(
            ref_artifact, {"pkg/archive_file.txt": "extracted directly"}
        )

        result = dep.CopyFromReference(ref_dir)
        self.assertTrue(result)
        dest_file = os.path.join(dep.output_dir, "pkg", "archive_file.txt")
        self.assertTrue(os.path.isfile(dest_file))
        with open(dest_file, "r") as f:
            self.assertEqual(f.read(), "extracted directly")

    def test_copy_from_reference_corrupt_content_names(self):
        ref_dir = os.path.join(self.tmpdir, "ref_dir")
        os.makedirs(ref_dir)
        dep = self._make_gcs_dep(object_name="archive.tar.gz")
        ref_artifact = os.path.join(ref_dir, f".{dep.gcs_file_name}")
        self._create_tar_file(
            ref_artifact, {"pkg/archive_file.txt": "extracted from fallback"}
        )
        ref_content_names = os.path.join(
            ref_dir, f".{dep.file_prefix}_content_names"
        )
        with open(ref_content_names, "w") as f:
            f.write("{invalid json content}")

        result = dep.CopyFromReference(ref_dir)
        self.assertTrue(result)
        dest_file = os.path.join(dep.output_dir, "pkg", "archive_file.txt")
        self.assertTrue(os.path.isfile(dest_file))
        with open(dest_file, "r") as f:
            self.assertEqual(f.read(), "extracted from fallback")

    def test_copy_from_reference_missing_extracted_file_in_ref(self):
        ref_dir = os.path.join(self.tmpdir, "ref_dir")
        os.makedirs(ref_dir)
        dep = self._make_gcs_dep(object_name="archive.tar.gz")
        ref_artifact = os.path.join(ref_dir, f".{dep.gcs_file_name}")
        self._create_tar_file(
            ref_artifact, {"pkg/archive_file.txt": "extracted from fallback"}
        )
        ref_content_names = os.path.join(
            ref_dir, f".{dep.file_prefix}_content_names"
        )
        with open(ref_content_names, "w") as f:
            json.dump(["pkg/archive_file.txt"], f)

        result = dep.CopyFromReference(ref_dir)
        self.assertTrue(result)
        dest_file = os.path.join(dep.output_dir, "pkg", "archive_file.txt")
        self.assertTrue(os.path.isfile(dest_file))
        with open(dest_file, "r") as f:
            self.assertEqual(f.read(), "extracted from fallback")

    def test_copy_from_reference_reflink_extracted_file_fails(self):
        ref_dir = os.path.join(self.tmpdir, "ref_dir")
        os.makedirs(os.path.join(ref_dir, "pkg"))
        dep = self._make_gcs_dep(object_name="archive.tar.gz")
        ref_artifact = os.path.join(ref_dir, f".{dep.gcs_file_name}")
        self._create_tar_file(
            ref_artifact, {"pkg/archive_file.txt": "extracted from fallback"}
        )
        with open(os.path.join(ref_dir, "pkg", "archive_file.txt"), "w") as f:
            f.write("content")
        ref_content_names = os.path.join(
            ref_dir, f".{dep.file_prefix}_content_names"
        )
        with open(ref_content_names, "w") as f:
            json.dump(["pkg/archive_file.txt"], f)

        orig_reflink = gclient.gclient_utils.reflink_or_copy

        def fake_reflink(s, d):
            if "pkg" in s:
                return False
            return orig_reflink(s, d)

        with mock.patch(
            "gclient_utils.reflink_or_copy", side_effect=fake_reflink
        ):
            result = dep.CopyFromReference(ref_dir)
            self.assertTrue(result)
            dest_file = os.path.join(dep.output_dir, "pkg", "archive_file.txt")
            self.assertTrue(os.path.isfile(dest_file))
            with open(dest_file, "r") as f:
                self.assertEqual(f.read(), "extracted from fallback")

    @mock.patch("gclient_utils.reflink_or_copy", return_value=False)
    def test_copy_from_reference_reflink_failure(self, mock_copy):
        ref_dir = os.path.join(self.tmpdir, "ref_dir")
        os.makedirs(ref_dir)
        dep = self._make_gcs_dep()
        ref_artifact = os.path.join(
            ref_dir, dep.output_file or f".{dep.gcs_file_name}"
        )
        with open(ref_artifact, "w") as f:
            f.write("content")
        self.assertFalse(dep.CopyFromReference(ref_dir))

    @mock.patch("fs_util.snapshot_btrfs_subvolume", return_value=True)
    @mock.patch("fs_util.is_btrfs_subvolume", return_value=True)
    def test_copy_from_reference_btrfs_subvolume_snapshot_success(
        self, mock_is_subvol, mock_snapshot
    ):
        ref_dir = os.path.join(self.tmpdir, "ref_dir")
        os.makedirs(ref_dir)
        dep = self._make_gcs_dep(object_name="archive.tar.gz")
        os.makedirs(dep.output_dir, exist_ok=True)

        result = dep.CopyFromReference(ref_dir)
        self.assertTrue(result)
        mock_is_subvol.assert_called_once_with(ref_dir)
        mock_snapshot.assert_called_once_with(ref_dir, dep.output_dir)
        with open(dep.hash_file, "r") as f:
            self.assertEqual(f.read().strip(), dep.sha256sum)
        with open(dep.migration_toggle_file, "r") as f:
            self.assertEqual(f.read().strip(), "1")

    @mock.patch("fs_util.snapshot_btrfs_subvolume", return_value=False)
    @mock.patch("fs_util.is_btrfs_subvolume", return_value=True)
    def test_copy_from_reference_btrfs_subvolume_snapshot_fallback(
        self, mock_is_subvol, mock_snapshot
    ):
        ref_dir = os.path.join(self.tmpdir, "ref_dir")
        os.makedirs(ref_dir)
        dep = self._make_gcs_dep(
            object_name="plain.txt", output_file="plain.txt"
        )
        ref_artifact = os.path.join(ref_dir, "plain.txt")
        with open(ref_artifact, "w") as f:
            f.write("content")

        result = dep.CopyFromReference(ref_dir)
        self.assertTrue(result)
        mock_is_subvol.assert_called_once_with(ref_dir)
        mock_snapshot.assert_called_once_with(ref_dir, dep.output_dir)
        self.assertTrue(os.path.isfile(dep.artifact_output_file))
        with open(dep.artifact_output_file, "r") as f:
            self.assertEqual(f.read(), "content")

    def test_maybe_extract_artifact_plain_file(self):
        dep = self._make_gcs_dep(object_name="bundle.txt")
        os.makedirs(dep.output_dir, exist_ok=True)
        with open(dep.artifact_output_file, "w") as f:
            f.write("plain text")
        dep._MaybeExtractArtifact()
        # Does not create content names file for non-archive
        content_names_file = os.path.join(
            dep.output_dir, f".{dep.file_prefix}_content_names"
        )
        self.assertFalse(os.path.exists(content_names_file))

    def test_maybe_extract_artifact_tar(self):
        dep = self._make_gcs_dep(object_name="pkg.tar.gz")
        self._create_tar_file(
            dep.artifact_output_file,
            {
                "pkg/file1.txt": "content 1",
                "pkg/file2.txt": "content 2",
            },
        )
        dep._MaybeExtractArtifact()
        file1 = os.path.join(dep.output_dir, "pkg", "file1.txt")
        file2 = os.path.join(dep.output_dir, "pkg", "file2.txt")
        self.assertTrue(os.path.isfile(file1))
        self.assertTrue(os.path.isfile(file2))
        with open(file1, "r") as f:
            self.assertEqual(f.read(), "content 1")
        with open(file2, "r") as f:
            self.assertEqual(f.read(), "content 2")

        content_names_file = os.path.join(
            dep.output_dir, f".{dep.file_prefix}_content_names"
        )
        self.assertTrue(os.path.isfile(content_names_file))
        with open(content_names_file, "r") as f:
            names = json.loads(f.read())
        self.assertIn("pkg/file1.txt", names)
        self.assertIn("pkg/file2.txt", names)

    def test_maybe_extract_artifact_zip(self):
        dep = self._make_gcs_dep(object_name="pkg.zip")
        self._create_zip_file(
            dep.artifact_output_file,
            {
                "pkg/zip1.txt": "zip content 1",
                "pkg/zip2.txt": "zip content 2",
            },
        )
        dep._MaybeExtractArtifact()
        file1 = os.path.join(dep.output_dir, "pkg", "zip1.txt")
        file2 = os.path.join(dep.output_dir, "pkg", "zip2.txt")
        self.assertTrue(os.path.isfile(file1))
        self.assertTrue(os.path.isfile(file2))
        with open(file1, "r") as f:
            self.assertEqual(f.read(), "zip content 1")
        with open(file2, "r") as f:
            self.assertEqual(f.read(), "zip content 2")

        content_names_file = os.path.join(
            dep.output_dir, f".{dep.file_prefix}_content_names"
        )
        self.assertTrue(os.path.isfile(content_names_file))
        with open(content_names_file, "r") as f:
            names = json.loads(f.read())
        self.assertIn("pkg/zip1.txt", names)
        self.assertIn("pkg/zip2.txt", names)

    def test_maybe_extract_artifact_tar_traversal_rejected(self):
        dep = self._make_gcs_dep(object_name="bad.tar.gz")
        os.makedirs(dep.output_dir, exist_ok=True)
        with tarfile.open(dep.artifact_output_file, "w:gz") as tar:
            data = b"malicious"
            ti = tarfile.TarInfo("../escaped.txt")
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))

        with self.assertRaises(Exception):
            dep._MaybeExtractArtifact()

    def test_is_download_needed(self):
        dep = self._make_gcs_dep()
        self.assertTrue(dep.IsDownloadNeeded())

        os.makedirs(dep.output_dir, exist_ok=True)
        with open(dep.artifact_output_file, "w") as f:
            f.write("content")
        self.assertTrue(dep.IsDownloadNeeded())

        with open(dep.hash_file, "w") as f:
            f.write(dep.sha256sum + "\n")
        self.assertTrue(dep.IsDownloadNeeded())

        with open(dep.migration_toggle_file, "w") as f:
            f.write("1\n")
        self.assertFalse(dep.IsDownloadNeeded())

        with open(dep.hash_file, "w") as f:
            f.write("mismatched_hash\n")
        self.assertTrue(dep.IsDownloadNeeded())

    def test_download_google_storage_reused_from_reference(self):
        ref_base = os.path.join(self.tmpdir, "ref_checkout")
        ref_target = os.path.join(ref_base, "src", "dep")
        os.makedirs(ref_target)
        dep = self._make_gcs_dep(
            object_name="plain.txt", output_file="plain.txt"
        )
        ref_artifact = os.path.join(ref_target, "plain.txt")
        with open(ref_artifact, "w") as f:
            f.write("reference payload")
        dep.size_bytes = os.path.getsize(ref_artifact)
        with open(
            os.path.join(ref_target, f".{dep.file_prefix}_hash"), "w"
        ) as f:
            f.write(dep.sha256sum + "\n")

        self.options.reference_dir = ref_base
        dep.DownloadGoogleStorage(self.options)

        self.assertTrue(os.path.isfile(dep.artifact_output_file))
        with open(dep.artifact_output_file, "r") as f:
            self.assertEqual(f.read(), "reference payload")
        self.assertFalse(dep.IsDownloadNeeded())

    def test_download_google_storage_no_download_needed(self):
        dep = self._make_gcs_dep()
        os.makedirs(dep.output_dir, exist_ok=True)
        with open(dep.artifact_output_file, "w") as f:
            f.write("artifact")
        with open(dep.hash_file, "w") as f:
            f.write(dep.sha256sum + "\n")
        with open(dep.migration_toggle_file, "w") as f:
            f.write("1\n")

        with mock.patch.object(dep, "GetMatchingReferenceDir") as mock_ref:
            dep.DownloadGoogleStorage(self.options)
            mock_ref.assert_not_called()

    def test_download_google_storage_fallback_to_test_download(self):
        dep = self._make_gcs_dep(
            object_name="archive.tar.gz",
            sha256sum="abcd123",
            size_bytes=10000,
        )
        with mock.patch.dict(os.environ, {"GCLIENT_TEST": "1"}):
            dep.DownloadGoogleStorage(self.options)

        self.assertTrue(os.path.isfile(dep.artifact_output_file))
        self.assertTrue(os.path.isfile(dep.hash_file))
        self.assertTrue(os.path.isfile(dep.migration_toggle_file))
        with open(dep.hash_file, "r") as f:
            self.assertEqual(f.read().strip(), "abcd123")

    @mock.patch(
        "fs_util.ensure_directory_or_subvolume",
        wraps=fs_util.ensure_directory_or_subvolume,
    )
    def test_download_google_storage_ensures_directory_or_subvolume(
        self, mock_ensure
    ):
        dep = self._make_gcs_dep(
            object_name="archive.tar.gz",
            sha256sum="abcd123",
            size_bytes=10000,
        )
        with mock.patch.dict(os.environ, {"GCLIENT_TEST": "1"}):
            dep.DownloadGoogleStorage(self.options)
        mock_ensure.assert_called_once_with(dep.output_dir)

    def test_run_skips_runhooks_and_revinfo(self):
        dep = self._make_gcs_dep()
        with mock.patch.object(dep, "DownloadGoogleStorage") as mock_download:
            dep.run(None, "runhooks", [], None, self.options, None, None, None)
            mock_download.assert_not_called()

            dep.run(None, "revinfo", [], None, self.options, None, None, None)
            mock_download.assert_not_called()

    def test_run_skips_when_should_process_false(self):
        dep = self._make_gcs_dep(should_process=False)
        with mock.patch.object(dep, "DownloadGoogleStorage") as mock_download:
            dep.run(None, "sync", [], None, self.options, None, None, None)
            mock_download.assert_not_called()

    def test_run_executes_download(self):
        dep = self._make_gcs_dep(should_process=True)
        with mock.patch.object(dep, "DownloadGoogleStorage") as mock_download:
            with mock.patch.object(
                gclient.Dependency, "run", return_value=None
            ):
                dep.run(None, "sync", [], None, self.options, None, None, None)
                mock_download.assert_called_once_with(self.options)

    def test_get_cache_dir_cli_option(self):
        cache_dir = os.path.join(self.tmpdir, "cli_cache")
        self.options.cache_dir = cache_dir
        dep = self._make_gcs_dep()
        self.assertEqual(
            dep._GetCacheDir(self.options), os.path.abspath(cache_dir)
        )

    def test_get_cache_dir_client_property(self):
        cache_dir = os.path.join(self.tmpdir, "client_cache")
        self.client._cache_dir = cache_dir
        dep = self._make_gcs_dep()
        self.assertEqual(dep._GetCacheDir(None), os.path.abspath(cache_dir))

    def test_get_cache_dir_git_cache_mirror(self):
        cache_dir = os.path.join(self.tmpdir, "mirror_cache")
        git_cache.Mirror.SetCachePath(cache_dir)
        dep = self._make_gcs_dep()
        self.assertEqual(dep._GetCacheDir(None), os.path.abspath(cache_dir))

    def test_get_cache_dir_env_var(self):
        cache_dir = os.path.join(self.tmpdir, "env_cache")
        dep = self._make_gcs_dep()
        with mock.patch.object(
            git_cache.Mirror,
            "GetCachePath",
            side_effect=RuntimeError("unset"),
        ):
            with mock.patch.dict(os.environ, {"GCLIENT_CACHE_DIR": cache_dir}):
                self.assertEqual(
                    dep._GetCacheDir(None), os.path.abspath(cache_dir)
                )

    def test_get_cache_artifact_dir(self):
        cache_dir = os.path.join(self.tmpdir, "cache")
        dep = self._make_gcs_dep(
            bucket="my_bucket",
            object_name="tools/rust.tar.xz",
            sha256sum="deadbeef123",
        )
        expected = os.path.join(
            cache_dir,
            "artifacts",
            "my_bucket",
            "tools/rust.tar.xz",
            "deadbeef123",
        )
        self.assertEqual(dep._GetCacheArtifactDir(cache_dir), expected)

    def test_is_cache_valid_when_ready_and_matching(self):
        cache_dir = os.path.join(self.tmpdir, "cache")
        dep = self._make_gcs_dep(
            object_name="pkg.tar.gz",
            sha256sum="hash123",
            size_bytes=4,
        )
        cache_art_dir = dep._GetCacheArtifactDir(cache_dir)
        os.makedirs(cache_art_dir, exist_ok=True)
        with open(os.path.join(cache_art_dir, ".ready"), "w") as f:
            f.write("ready\n")
        with open(
            os.path.join(cache_art_dir, f".{dep.file_prefix}_hash"), "w"
        ) as f:
            f.write("hash123\n")
        with open(
            os.path.join(cache_art_dir, f".{dep.gcs_file_name}"), "w"
        ) as f:
            f.write("data")

        self.assertTrue(dep._IsCacheValid(cache_art_dir))

    def test_is_cache_valid_when_not_ready(self):
        cache_dir = os.path.join(self.tmpdir, "cache")
        dep = self._make_gcs_dep()
        cache_art_dir = dep._GetCacheArtifactDir(cache_dir)
        os.makedirs(cache_art_dir, exist_ok=True)
        self.assertFalse(dep._IsCacheValid(cache_art_dir))

    def test_is_cache_valid_when_hash_mismatched(self):
        cache_dir = os.path.join(self.tmpdir, "cache")
        dep = self._make_gcs_dep(sha256sum="hash123")
        cache_art_dir = dep._GetCacheArtifactDir(cache_dir)
        os.makedirs(cache_art_dir, exist_ok=True)
        with open(os.path.join(cache_art_dir, ".ready"), "w") as f:
            f.write("ready\n")
        with open(
            os.path.join(cache_art_dir, f".{dep.file_prefix}_hash"), "w"
        ) as f:
            f.write("different_hash\n")
        self.assertFalse(dep._IsCacheValid(cache_art_dir))

    def test_ensure_in_cache_skips_when_valid(self):
        cache_dir = os.path.join(self.tmpdir, "cache")
        self.options.cache_dir = cache_dir
        dep = self._make_gcs_dep(sha256sum="hash123", size_bytes=4)
        cache_art_dir = dep._GetCacheArtifactDir(cache_dir)
        os.makedirs(cache_art_dir, exist_ok=True)
        with open(os.path.join(cache_art_dir, ".ready"), "w") as f:
            f.write("ready\n")
        with open(
            os.path.join(cache_art_dir, f".{dep.file_prefix}_hash"), "w"
        ) as f:
            f.write("hash123\n")
        with open(
            os.path.join(cache_art_dir, f".{dep.gcs_file_name}"), "w"
        ) as f:
            f.write("test")

        with mock.patch.object(dep, "_DownloadAndExtract") as mock_dl:
            self.assertTrue(dep._EnsureInCache(cache_art_dir, self.options))
            mock_dl.assert_not_called()

    def test_ensure_in_cache_downloads_and_extracts_when_missing(self):
        cache_dir = os.path.join(self.tmpdir, "cache")
        self.options.cache_dir = cache_dir
        dep = self._make_gcs_dep(
            object_name="archive.tar.gz",
            sha256sum="abcd123",
            size_bytes=10000,
        )
        cache_art_dir = dep._GetCacheArtifactDir(cache_dir)

        with mock.patch.dict(os.environ, {"GCLIENT_TEST": "1"}):
            self.assertTrue(dep._EnsureInCache(cache_art_dir, self.options))

        self.assertTrue(os.path.exists(os.path.join(cache_art_dir, ".ready")))
        self.assertTrue(
            os.path.exists(
                os.path.join(cache_art_dir, f".{dep.file_prefix}_hash")
            )
        )
        self.assertTrue(
            os.path.exists(
                os.path.join(cache_art_dir, "extracted_dir", "extracted_file")
            )
        )

    def test_download_google_storage_reuses_from_cache(self):
        cache_dir = os.path.join(self.tmpdir, "cache")
        self.options.cache_dir = cache_dir
        dep = self._make_gcs_dep(
            object_name="pkg.tar.gz",
            sha256sum="abcd123",
            size_bytes=10,
        )
        cache_art_dir = dep._GetCacheArtifactDir(cache_dir)
        os.makedirs(cache_art_dir, exist_ok=True)
        with open(os.path.join(cache_art_dir, ".ready"), "w") as f:
            f.write("ready\n")
        with open(
            os.path.join(cache_art_dir, f".{dep.file_prefix}_hash"), "w"
        ) as f:
            f.write("abcd123\n")
        with open(
            os.path.join(cache_art_dir, f".{dep.gcs_file_name}"), "w"
        ) as f:
            f.write("0123456789")

        with mock.patch.object(
            dep, "CopyFromReference", return_value=True
        ) as mock_copy:
            with mock.patch.object(dep, "_DownloadAndExtract") as mock_dl:
                dep.DownloadGoogleStorage(self.options)
                mock_copy.assert_called_once_with(cache_art_dir)
                mock_dl.assert_not_called()

    def test_download_google_storage_populates_cache_on_miss(self):
        cache_dir = os.path.join(self.tmpdir, "cache")
        self.options.cache_dir = cache_dir
        dep = self._make_gcs_dep(
            object_name="archive.tar.gz",
            sha256sum="abcd123",
            size_bytes=10000,
        )
        with mock.patch.dict(os.environ, {"GCLIENT_TEST": "1"}):
            dep.DownloadGoogleStorage(self.options)

        cache_art_dir = dep._GetCacheArtifactDir(cache_dir)
        self.assertTrue(os.path.exists(os.path.join(cache_art_dir, ".ready")))
        self.assertTrue(
            os.path.exists(
                os.path.join(dep.output_dir, "extracted_dir", "extracted_file")
            )
        )

    def test_get_cache_mode(self):
        dep = self._make_gcs_dep()
        self.assertEqual(dep._GetCacheMode(self.options), "auto")

        self.options.gcs_cache_mode = "COMPRESSED"
        self.assertEqual(dep._GetCacheMode(self.options), "compressed")

        self.options.gcs_cache_mode = None
        with mock.patch.dict(os.environ, {"GCS_CACHE_MODE": "UNCOMPRESSED"}):
            self.assertEqual(dep._GetCacheMode(self.options), "uncompressed")

    def test_should_use_uncompressed_cache(self):
        cache_dir = os.path.join(self.tmpdir, "cache")
        dep = self._make_gcs_dep()

        # Explicit mode overrides
        self.options.gcs_cache_mode = "compressed"
        self.assertFalse(
            dep._ShouldUseUncompressedCache(cache_dir, self.options)
        )

        self.options.gcs_cache_mode = "uncompressed"
        self.assertTrue(
            dep._ShouldUseUncompressedCache(cache_dir, self.options)
        )

        # Auto mode: non-CoW provider
        self.options.gcs_cache_mode = "auto"
        mock_provider = mock.MagicMock(supports_cow=False)
        with mock.patch(
            "fs_util.get_filesystem_provider", return_value=mock_provider
        ):
            self.assertFalse(
                dep._ShouldUseUncompressedCache(cache_dir, self.options)
            )

        # Auto mode: CoW provider on same filesystem
        mock_provider_cow = mock.MagicMock(supports_cow=True)
        with mock.patch(
            "fs_util.get_filesystem_provider", return_value=mock_provider_cow
        ):
            self.assertTrue(
                dep._ShouldUseUncompressedCache(cache_dir, self.options)
            )

        # Auto mode: CoW provider on different filesystem device
        def fake_stat(path, *args, **kwargs):
            if "cache" in str(path):
                return mock.MagicMock(st_dev=100)
            return mock.MagicMock(st_dev=200)

        with mock.patch(
            "fs_util.get_filesystem_provider", return_value=mock_provider_cow
        ):
            with mock.patch("os.stat", side_effect=fake_stat):
                self.assertFalse(
                    dep._ShouldUseUncompressedCache(cache_dir, self.options)
                )

    def test_download_google_storage_populates_archive_cache(self):
        cache_dir = os.path.join(self.tmpdir, "cache")
        self.options.cache_dir = cache_dir
        self.options.gcs_cache_mode = "compressed"
        dep = self._make_gcs_dep(
            object_name="archive.tar.gz",
            sha256sum="abcd123",
            size_bytes=10000,
        )
        with mock.patch.dict(os.environ, {"GCLIENT_TEST": "1"}):
            dep.DownloadGoogleStorage(self.options)

        cache_archive = dep._GetCacheArchiveFile(cache_dir)
        self.assertTrue(os.path.isfile(cache_archive))
        self.assertTrue(os.path.exists(f"{cache_archive}.ready"))

        # Uncompressed artifacts tree should NOT exist in compressed mode
        cache_art_dir = dep._GetCacheArtifactDir(cache_dir)
        self.assertFalse(os.path.exists(cache_art_dir))

        # Workspace target should have extracted files and hash
        self.assertTrue(
            os.path.exists(
                os.path.join(dep.output_dir, "extracted_dir", "extracted_file")
            )
        )
        self.assertTrue(os.path.exists(dep.hash_file))
        with open(dep.hash_file, "r") as f:
            self.assertEqual(f.read().strip(), "abcd123")

    def test_download_google_storage_reuses_from_archive_cache(self):
        cache_dir = os.path.join(self.tmpdir, "cache")
        self.options.cache_dir = cache_dir
        self.options.gcs_cache_mode = "compressed"
        dep = self._make_gcs_dep(
            object_name="pkg.tar.gz",
            sha256sum="abcd123",
            size_bytes=10,
        )
        cache_archive = dep._GetCacheArchiveFile(cache_dir)
        os.makedirs(os.path.dirname(cache_archive), exist_ok=True)
        with open(cache_archive, "w") as f:
            f.write("0123456789")
        with open(f"{cache_archive}.ready", "w") as f:
            f.write("ready\n")

        with mock.patch.object(
            dep, "_PopulateFromArchiveCache", return_value=True
        ) as mock_pop:
            with mock.patch.object(dep, "_DownloadAndExtract") as mock_dl:
                dep.DownloadGoogleStorage(self.options)
                mock_pop.assert_called_once_with(cache_archive, self.options)
                mock_dl.assert_not_called()


if __name__ == "__main__":
    unittest.main()

# vim: ts=2:sw=2:tw=80:et:
