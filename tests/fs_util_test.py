#!/usr/bin/env python3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for fs_util.py."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fs_util


class DefaultFilesystemProviderTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.provider = fs_util.DefaultFilesystemProvider()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_properties(self):
        self.assertFalse(self.provider.supports_cow)
        self.assertEqual(self.provider.fstype, "default")

    def test_file_copy(self):
        src = os.path.join(self.tmpdir, "src.txt")
        dst = os.path.join(self.tmpdir, "sub", "dst.txt")
        with open(src, "w") as f:
            f.write("content")
        self.assertTrue(self.provider.copy(src, dst))
        self.assertTrue(os.path.isfile(dst))
        with open(dst, "r") as f:
            self.assertEqual(f.read(), "content")

    def test_dir_copy(self):
        src_dir = os.path.join(self.tmpdir, "src_dir")
        dst_dir = os.path.join(self.tmpdir, "dst_dir")
        nested = os.path.join(src_dir, "nested")
        os.makedirs(nested)
        with open(os.path.join(src_dir, "f1.txt"), "w") as f:
            f.write("f1")
        with open(os.path.join(nested, "f2.txt"), "w") as f:
            f.write("f2")

        self.assertTrue(self.provider.copy(src_dir, dst_dir))
        self.assertTrue(os.path.isfile(os.path.join(dst_dir, "f1.txt")))
        self.assertTrue(
            os.path.isfile(os.path.join(dst_dir, "nested", "f2.txt"))
        )

    @mock.patch("shutil.copy2", side_effect=OSError("permission denied"))
    def test_copy_failure(self, _mock_copy):
        src = os.path.join(self.tmpdir, "src.txt")
        dst = os.path.join(self.tmpdir, "dst.txt")
        with open(src, "w") as f:
            f.write("data")
        self.assertFalse(self.provider.copy(src, dst))


class LinuxFilesystemProviderTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mock_fallback = mock.MagicMock(spec=fs_util.FilesystemProvider)
        self.provider = fs_util.LinuxFilesystemProvider(
            fallback=self.mock_fallback
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_properties(self):
        self.assertTrue(self.provider.supports_cow)
        self.assertEqual(self.provider.fstype, "linux")

    @mock.patch("subprocess.run")
    def test_file_copy_success(self, mock_run):
        mock_run.return_value.returncode = 0
        src = os.path.join(self.tmpdir, "src.txt")
        dst = os.path.join(self.tmpdir, "dst.txt")
        with open(src, "w") as f:
            f.write("data")

        self.assertTrue(self.provider.copy(src, dst))
        mock_run.assert_called_once_with(
            ["cp", "-a", "--reflink=auto", src, dst],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.mock_fallback.copy.assert_not_called()

    @mock.patch("subprocess.run")
    def test_dir_copy_success(self, mock_run):
        mock_run.return_value.returncode = 0
        src = os.path.join(self.tmpdir, "src_dir")
        dst = os.path.join(self.tmpdir, "dst_dir")
        os.makedirs(src)

        self.assertTrue(self.provider.copy(src, dst))
        mock_run.assert_called_once_with(
            ["cp", "-a", "--reflink=auto", os.path.join(src, "."), dst],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.mock_fallback.copy.assert_not_called()

    @mock.patch("subprocess.run")
    def test_fallback_on_nonzero_exit(self, mock_run):
        mock_run.return_value.returncode = 1
        self.mock_fallback.copy.return_value = True
        src = os.path.join(self.tmpdir, "src.txt")
        dst = os.path.join(self.tmpdir, "dst.txt")
        with open(src, "w") as f:
            f.write("data")

        self.assertTrue(self.provider.copy(src, dst))
        self.mock_fallback.copy.assert_called_once_with(src, dst)

    @mock.patch(
        "subprocess.run",
        side_effect=subprocess.SubprocessError("reflink failed"),
    )
    def test_fallback_on_exception(self, _mock_run):
        self.mock_fallback.copy.return_value = True
        src = os.path.join(self.tmpdir, "src.txt")
        dst = os.path.join(self.tmpdir, "dst.txt")
        with open(src, "w") as f:
            f.write("data")

        self.assertTrue(self.provider.copy(src, dst))
        self.mock_fallback.copy.assert_called_once_with(src, dst)


class APFSFilesystemProviderTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mock_fallback = mock.MagicMock(spec=fs_util.FilesystemProvider)
        self.provider = fs_util.APFSFilesystemProvider(
            fallback=self.mock_fallback
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_properties(self):
        self.assertTrue(self.provider.supports_cow)
        self.assertEqual(self.provider.fstype, "apfs")

    @mock.patch("subprocess.run")
    def test_file_copy_success(self, mock_run):
        mock_run.return_value.returncode = 0
        src = os.path.join(self.tmpdir, "src.txt")
        dst = os.path.join(self.tmpdir, "dst.txt")
        with open(src, "w") as f:
            f.write("data")

        self.assertTrue(self.provider.copy(src, dst))
        mock_run.assert_called_once_with(
            ["cp", "-p", "-c", src, dst],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.mock_fallback.copy.assert_not_called()

    @mock.patch("subprocess.run")
    def test_dir_copy_success(self, mock_run):
        mock_run.return_value.returncode = 0
        src = os.path.join(self.tmpdir, "src_dir")
        dst = os.path.join(self.tmpdir, "dst_dir")
        os.makedirs(src)

        self.assertTrue(self.provider.copy(src, dst))
        mock_run.assert_called_once_with(
            ["cp", "-R", "-p", "-c", os.path.join(src, "."), dst],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.mock_fallback.copy.assert_not_called()

    @mock.patch("subprocess.run", side_effect=OSError("cp not supported"))
    def test_fallback_on_error(self, _mock_run):
        self.mock_fallback.copy.return_value = True
        src = os.path.join(self.tmpdir, "src.txt")
        dst = os.path.join(self.tmpdir, "dst.txt")
        with open(src, "w") as f:
            f.write("data")

        self.assertTrue(self.provider.copy(src, dst))
        self.mock_fallback.copy.assert_called_once_with(src, dst)


class WindowsBlockCloneProviderTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mock_fallback = mock.MagicMock(spec=fs_util.FilesystemProvider)
        self.provider = fs_util.WindowsBlockCloneProvider(
            fallback=self.mock_fallback
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_properties(self):
        self.assertTrue(self.provider.supports_cow)
        self.assertEqual(self.provider.fstype, "refs")

    def test_non_windows_fallback(self):
        src = os.path.join(self.tmpdir, "src.txt")
        dst = os.path.join(self.tmpdir, "dst.txt")
        with open(src, "w") as f:
            f.write("data")
        self.mock_fallback.copy.return_value = True

        with mock.patch("ctypes.windll", None, create=True):
            self.assertTrue(self.provider.copy(src, dst))
            self.mock_fallback.copy.assert_called_once_with(src, dst)

    def test_mock_kernel32_block_clone(self):
        src = os.path.join(self.tmpdir, "src.bin")
        dst = os.path.join(self.tmpdir, "dst.bin")
        tail_bytes = b"TAIL123456"
        with open(src, "wb") as f:
            f.write(b"A" * 65536 + tail_bytes)

        mock_kernel32 = mock.MagicMock()
        mock_kernel32.CreateFileW.side_effect = [1001, 1002]

        def fake_get_file_size(_handle, ref):
            ref._obj.QuadPart = 65536 + len(tail_bytes)
            return True

        mock_kernel32.GetFileSizeEx.side_effect = fake_get_file_size
        mock_kernel32.SetFilePointerEx.return_value = True
        mock_kernel32.SetEndOfFile.return_value = True
        mock_kernel32.DeviceIoControl.return_value = True
        mock_kernel32.CloseHandle.return_value = True

        fake_windll = mock.MagicMock()
        fake_windll.kernel32 = mock_kernel32

        with mock.patch("ctypes.windll", fake_windll, create=True):
            result = self.provider.copy(src, dst)

        self.assertTrue(result)
        self.assertEqual(mock_kernel32.CloseHandle.call_count, 2)
        self.assertTrue(os.path.exists(dst))
        with open(dst, "rb") as f:
            content = f.read()
        self.assertEqual(content[65536:], tail_bytes)
        self.mock_fallback.copy.assert_not_called()

    def test_fallback_on_os_error(self):
        src = os.path.join(self.tmpdir, "src.txt")
        dst = os.path.join(self.tmpdir, "dst.txt")
        with open(src, "w") as f:
            f.write("content")
        self.mock_fallback.copy.return_value = True

        with mock.patch.object(
            self.provider, "_clone_file", side_effect=OSError("not ReFS")
        ):
            self.assertTrue(self.provider.copy(src, dst))
            self.mock_fallback.copy.assert_called_once_with(src, dst)

    def test_cross_volume_fallback(self):
        src = "C:\\path\\to\\src.txt"
        dst = "D:\\path\\to\\dst.txt"
        self.mock_fallback.copy.return_value = True

        res = self.provider.copy(src, dst)
        self.assertTrue(res)
        self.mock_fallback.copy.assert_called_once_with(src, dst)


class WindowsVolumeInfoTest(unittest.TestCase):
    def test_non_windows_raises_os_error(self):
        with mock.patch("ctypes.windll", None, create=True):
            with self.assertRaises(OSError):
                fs_util.get_windows_volume_info("C:\\test")

    def test_get_windows_volume_info_refs_success(self):
        mock_kernel32 = mock.MagicMock()

        def fake_get_volume_info(
            _root, _vname, _vlen, _snum, _maxlen, flags_ref, fsname_buf, _flen
        ):
            flags_ref._obj.value = fs_util.FILE_SUPPORTS_BLOCK_CLONING | 0x10
            fsname_buf.value = "ReFS"
            return True

        mock_kernel32.GetVolumeInformationW.side_effect = fake_get_volume_info
        fake_windll = mock.MagicMock()
        fake_windll.kernel32 = mock_kernel32

        with mock.patch("ctypes.windll", fake_windll, create=True):
            flags, fsname = fs_util.get_windows_volume_info(
                "C:\\devdrive\\project"
            )

        self.assertEqual(fsname, "ReFS")
        self.assertTrue(flags & fs_util.FILE_SUPPORTS_BLOCK_CLONING)

    def test_get_windows_volume_info_failure(self):
        mock_kernel32 = mock.MagicMock()
        mock_kernel32.GetVolumeInformationW.return_value = False
        fake_windll = mock.MagicMock()
        fake_windll.kernel32 = mock_kernel32

        with mock.patch("ctypes.windll", fake_windll, create=True):
            with self.assertRaises(OSError):
                fs_util.get_windows_volume_info("C:\\")


class FilesystemDetectionTest(unittest.TestCase):
    @mock.patch("subprocess.run")
    def test_linux_findmnt_success(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "btrfs\n"

        fstype = fs_util.get_linux_filesystem_type("/some/mount/path")
        self.assertEqual(fstype, "btrfs")

    @mock.patch("subprocess.run", side_effect=OSError("no findmnt"))
    def test_linux_proc_mounts_fallback(self, _mock_run):
        mount_data = (
            "sysfs /sys sysfs rw 0 0\n"
            "/dev/sda1 / ext4 rw 0 0\n"
            "/dev/sdb1 /mnt/data btrfs rw 0 0\n"
        )
        with (
            mock.patch("os.path.exists", return_value=True),
            mock.patch("os.path.isfile", return_value=True),
            mock.patch("builtins.open", mock.mock_open(read_data=mount_data)),
        ):
            fstype = fs_util.get_linux_filesystem_type(
                "/mnt/data/repo/file.txt"
            )
            self.assertEqual(fstype, "btrfs")

    @mock.patch("subprocess.run")
    def test_darwin_stat_success(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "apfs\n"

        fstype = fs_util.get_darwin_filesystem_type("/Users/test")
        self.assertEqual(fstype, "apfs")

    @mock.patch("fs_util.get_windows_volume_info", return_value=(0, "NTFS"))
    def test_get_filesystem_type_windows(self, _mock_info):
        fstype = fs_util.get_filesystem_type("C:\\test", platform="win32")
        self.assertEqual(fstype, "ntfs")

    @mock.patch("fs_util.get_linux_filesystem_type", return_value="ext4")
    def test_get_filesystem_type_linux(self, _mock_linux):
        fstype = fs_util.get_filesystem_type("/test", platform="linux")
        self.assertEqual(fstype, "ext4")

    @mock.patch("fs_util.get_darwin_filesystem_type", return_value="apfs")
    def test_get_filesystem_type_darwin(self, _mock_darwin):
        fstype = fs_util.get_filesystem_type("/test", platform="darwin")
        self.assertEqual(fstype, "apfs")


class ProviderFactoryTest(unittest.TestCase):
    def test_get_filesystem_provider(self):
        p_linux = fs_util.get_filesystem_provider("linux")
        self.assertIsInstance(p_linux, fs_util.LinuxFilesystemProvider)

        p_darwin = fs_util.get_filesystem_provider("darwin")
        self.assertIsInstance(p_darwin, fs_util.APFSFilesystemProvider)

        p_win = fs_util.get_filesystem_provider("win32")
        self.assertIsInstance(p_win, fs_util.WindowsBlockCloneProvider)

        p_cygwin = fs_util.get_filesystem_provider("cygwin")
        self.assertIsInstance(p_cygwin, fs_util.WindowsBlockCloneProvider)

        p_default = fs_util.get_filesystem_provider("other")
        self.assertIsInstance(p_default, fs_util.DefaultFilesystemProvider)

    def test_get_windows_filesystem_provider_refs(self):
        with mock.patch(
            "fs_util.get_windows_volume_info",
            return_value=(fs_util.FILE_SUPPORTS_BLOCK_CLONING, "ReFS"),
        ):
            p = fs_util.get_windows_filesystem_provider("D:\\workspace")
            self.assertIsInstance(p, fs_util.WindowsBlockCloneProvider)
            self.assertTrue(p.supports_cow)
            self.assertEqual(p.fstype, "refs")

    def test_get_windows_filesystem_provider_ntfs(self):
        with mock.patch(
            "fs_util.get_windows_volume_info",
            return_value=(0, "NTFS"),
        ):
            p = fs_util.get_windows_filesystem_provider("C:\\workspace")
            self.assertIsInstance(p, fs_util.DefaultFilesystemProvider)
            self.assertFalse(p.supports_cow)
            self.assertEqual(p.fstype, "ntfs")

    def test_get_filesystem_provider_with_path_linux(self):
        with mock.patch(
            "fs_util.get_linux_filesystem_type", return_value="btrfs"
        ):
            p = fs_util.get_filesystem_provider(
                path="/mnt/btrfs", platform="linux"
            )
            self.assertIsInstance(p, fs_util.LinuxFilesystemProvider)
            self.assertTrue(p.supports_cow)
            self.assertEqual(p.fstype, "btrfs")

        with mock.patch(
            "fs_util.get_linux_filesystem_type", return_value="ext4"
        ):
            p = fs_util.get_filesystem_provider(path="/home", platform="linux")
            self.assertIsInstance(p, fs_util.LinuxFilesystemProvider)
            self.assertFalse(p.supports_cow)
            self.assertEqual(p.fstype, "ext4")

    def test_reflink_or_copy_facade(self):
        mock_provider = mock.MagicMock(spec=fs_util.FilesystemProvider)
        mock_provider.copy.return_value = True

        res = fs_util.reflink_or_copy("a", "b", provider=mock_provider)
        self.assertTrue(res)
        mock_provider.copy.assert_called_once_with("a", "b")

    def test_reflink_or_copy_default_provider(self):
        with mock.patch("fs_util.get_filesystem_provider") as mock_get_provider:
            mock_provider = mock.MagicMock(spec=fs_util.FilesystemProvider)
            mock_provider.copy.return_value = True
            mock_get_provider.return_value = mock_provider

            res = fs_util.reflink_or_copy("src_file", "dst_file")
            self.assertTrue(res)
            mock_get_provider.assert_called_once_with(path="src_file")
            mock_provider.copy.assert_called_once_with("src_file", "dst_file")


if __name__ == "__main__":
    unittest.main()

# vim: ts=2:sw=2:tw=80:et:
