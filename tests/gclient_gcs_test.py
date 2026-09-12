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
import gclient


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

    def test_should_extract_artifact(self):
        dep = self._make_gcs_dep(object_name="bundle.tar.gz")
        self.assertFalse(dep.ShouldExtractArtifact())

        os.makedirs(dep.output_dir, exist_ok=True)
        with open(dep.artifact_output_file, "w") as f:
            f.write("plain text")
        self.assertFalse(dep.ShouldExtractArtifact())

        self._create_tar_file(dep.artifact_output_file, {"f.txt": "tar"})
        self.assertTrue(dep.ShouldExtractArtifact())

        dep_zip = self._make_gcs_dep(object_name="bundle.zip")
        self._create_zip_file(dep_zip.artifact_output_file, {"f.txt": "zip"})
        self.assertTrue(dep_zip.ShouldExtractArtifact())

    def test_extract_artifact_tar(self):
        dep = self._make_gcs_dep(object_name="pkg.tar.gz")
        self._create_tar_file(
            dep.artifact_output_file,
            {
                "pkg/file1.txt": "content 1",
                "pkg/file2.txt": "content 2",
            },
        )
        dep.ExtractArtifact()
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

    def test_extract_artifact_zip(self):
        dep = self._make_gcs_dep(object_name="pkg.zip")
        self._create_zip_file(
            dep.artifact_output_file,
            {
                "pkg/zip1.txt": "zip content 1",
                "pkg/zip2.txt": "zip content 2",
            },
        )
        dep.ExtractArtifact()
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

    def test_extract_artifact_tar_traversal_rejected(self):
        dep = self._make_gcs_dep(object_name="bad.tar.gz")
        os.makedirs(dep.output_dir, exist_ok=True)
        with tarfile.open(dep.artifact_output_file, "w:gz") as tar:
            data = b"malicious"
            ti = tarfile.TarInfo("../escaped.txt")
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))

        with self.assertRaises(Exception):
            dep.ExtractArtifact()

    def test_maybe_extract_artifact_compatibility(self):
        dep = self._make_gcs_dep(object_name="pkg.tar.gz")
        self._create_tar_file(
            dep.artifact_output_file,
            {"pkg/compat.txt": "compat content"},
        )
        dep._MaybeExtractArtifact()
        extracted = os.path.join(dep.output_dir, "pkg", "compat.txt")
        self.assertTrue(os.path.isfile(extracted))
        with open(extracted, "r") as f:
            self.assertEqual(f.read(), "compat content")

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


if __name__ == "__main__":
    unittest.main()
