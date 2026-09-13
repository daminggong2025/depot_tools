#!/usr/bin/env python3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for fs_util.py."""

import os
import shutil
import stat
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


class BtrfsFilesystemProviderTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mock_fallback = mock.MagicMock(spec=fs_util.FilesystemProvider)
        self.provider = fs_util.BtrfsFilesystemProvider(
            fallback=self.mock_fallback
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_properties(self):
        self.assertTrue(self.provider.supports_cow)
        self.assertTrue(self.provider.supports_snapshots)
        self.assertEqual(self.provider.fstype, "btrfs")

    @mock.patch("fs_util.snapshot_btrfs_subvolume", return_value=True)
    @mock.patch("fs_util.is_btrfs_subvolume", return_value=True)
    def test_copy_subvolume_snapshot_success(
        self, mock_is_subvol, mock_snapshot
    ):
        src = os.path.join(self.tmpdir, "subvol_src")
        dst = os.path.join(self.tmpdir, "subvol_dst")
        os.makedirs(src)

        self.assertTrue(self.provider.copy(src, dst))
        mock_is_subvol.assert_called_once_with(src)
        mock_snapshot.assert_called_once_with(src, dst)
        self.mock_fallback.copy.assert_not_called()

    @mock.patch("fs_util.snapshot_btrfs_subvolume", return_value=False)
    @mock.patch("fs_util.is_btrfs_subvolume", return_value=True)
    @mock.patch("subprocess.run")
    def test_copy_subvolume_snapshot_failure_falls_back_to_reflink(
        self, mock_run, mock_is_subvol, mock_snapshot
    ):
        mock_run.return_value.returncode = 0
        src = os.path.join(self.tmpdir, "subvol_src")
        dst = os.path.join(self.tmpdir, "subvol_dst")
        os.makedirs(src)

        self.assertTrue(self.provider.copy(src, dst))
        mock_is_subvol.assert_called_once_with(src)
        mock_snapshot.assert_called_once_with(src, dst)
        mock_run.assert_called_once_with(
            ["cp", "-a", "--reflink=auto", os.path.join(src, "."), dst],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @mock.patch("fs_util.is_btrfs_subvolume", return_value=False)
    @mock.patch("subprocess.run")
    def test_copy_non_subvolume_uses_reflink(self, mock_run, mock_is_subvol):
        mock_run.return_value.returncode = 0
        src = os.path.join(self.tmpdir, "regular_file.txt")
        dst = os.path.join(self.tmpdir, "dest_file.txt")
        with open(src, "w") as f:
            f.write("content")

        self.assertTrue(self.provider.copy(src, dst))
        mock_is_subvol.assert_called_once_with(src)
        mock_run.assert_called_once_with(
            ["cp", "-a", "--reflink=auto", src, dst],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


class BtrfsSubvolumeUtilsTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_is_btrfs_subvolume_nonexistent(self):
        self.assertFalse(
            fs_util.is_btrfs_subvolume(os.path.join(self.tmpdir, "missing"))
        )

    def test_is_btrfs_subvolume_file(self):
        f = os.path.join(self.tmpdir, "file.txt")
        with open(f, "w") as fp:
            fp.write("hello")
        self.assertFalse(fs_util.is_btrfs_subvolume(f))

    def test_is_btrfs_subvolume_symlink(self):
        target = os.path.join(self.tmpdir, "target_dir")
        link = os.path.join(self.tmpdir, "link_dir")
        os.makedirs(target)
        os.symlink(target, link)
        self.assertFalse(fs_util.is_btrfs_subvolume(link))

    def test_is_btrfs_subvolume_inode_not_256(self):
        d = os.path.join(self.tmpdir, "test_dir")
        os.makedirs(d)
        mock_stat = os.stat_result(
            (stat.S_IFDIR | 0o755, 12345, 0, 0, 0, 0, 0, 0, 0, 0)
        )
        with mock.patch("os.stat", return_value=mock_stat):
            self.assertFalse(fs_util.is_btrfs_subvolume(d))

    def test_is_btrfs_subvolume_inode_256_not_btrfs(self):
        d = os.path.join(self.tmpdir, "test_dir")
        os.makedirs(d)
        mock_stat = os.stat_result(
            (stat.S_IFDIR | 0o755, 256, 0, 0, 0, 0, 0, 0, 0, 0)
        )
        with (
            mock.patch("os.stat", return_value=mock_stat),
            mock.patch("fs_util.get_filesystem_type", return_value="ext4"),
        ):
            self.assertFalse(fs_util.is_btrfs_subvolume(d))

    def test_is_btrfs_subvolume_inode_256_btrfs(self):
        d = os.path.join(self.tmpdir, "test_subvol")
        os.makedirs(d)
        mock_stat = os.stat_result(
            (stat.S_IFDIR | 0o755, 256, 0, 0, 0, 0, 0, 0, 0, 0)
        )
        with (
            mock.patch("os.stat", return_value=mock_stat),
            mock.patch("fs_util.get_filesystem_type", return_value="btrfs"),
        ):
            self.assertTrue(fs_util.is_btrfs_subvolume(d))

    def test_is_btrfs_subvolume_stat_error(self):
        d = os.path.join(self.tmpdir, "test_dir")
        os.makedirs(d)
        with mock.patch("os.stat", side_effect=OSError("stat error")):
            self.assertFalse(fs_util.is_btrfs_subvolume(d))

    @mock.patch("subprocess.run")
    def test_create_btrfs_subvolume_success(self, mock_run):
        mock_run.return_value.returncode = 0
        target = os.path.join(self.tmpdir, "new_subvol")
        self.assertTrue(fs_util.create_btrfs_subvolume(target))
        mock_run.assert_called_once_with(
            ["btrfs", "subvolume", "create", target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    @mock.patch("subprocess.run")
    def test_create_btrfs_subvolume_failure(self, mock_run):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "Operation not permitted"
        target = os.path.join(self.tmpdir, "new_subvol")
        self.assertFalse(fs_util.create_btrfs_subvolume(target))

    @mock.patch("subprocess.run")
    def test_delete_btrfs_subvolume_success(self, mock_run):
        mock_run.return_value.returncode = 0
        target = os.path.join(self.tmpdir, "subvol_to_delete")
        os.makedirs(target)
        self.assertTrue(fs_util.delete_btrfs_subvolume(target, recursive=True))
        mock_run.assert_called_once_with(
            ["btrfs", "subvolume", "delete", "-R", target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def test_delete_btrfs_subvolume_nonexistent(self):
        self.assertTrue(
            fs_util.delete_btrfs_subvolume(
                os.path.join(self.tmpdir, "nonexistent")
            )
        )

    @mock.patch("subprocess.run")
    def test_delete_btrfs_subvolume_fallback(self, mock_run):
        target = os.path.join(self.tmpdir, "subvol")
        nested = os.path.join(target, "nested")
        os.makedirs(nested)
        res_fail = mock.MagicMock(returncode=1, stderr="Directory not empty")
        res_ok = mock.MagicMock(returncode=0)
        mock_run.side_effect = [res_fail, res_ok, res_ok]

        with mock.patch("fs_util.is_btrfs_subvolume") as mock_is_subvol:
            mock_is_subvol.side_effect = lambda p: p == nested
            self.assertTrue(
                fs_util.delete_btrfs_subvolume(target, recursive=True)
            )

    def test_remove_path_file(self):
        f = os.path.join(self.tmpdir, "file.txt")
        with open(f, "w") as fp:
            fp.write("content")
        self.assertTrue(fs_util.remove_path(f))
        self.assertFalse(os.path.exists(f))

    def test_remove_path_directory(self):
        d = os.path.join(self.tmpdir, "dir")
        os.makedirs(os.path.join(d, "sub"))
        self.assertTrue(fs_util.remove_path(d))
        self.assertFalse(os.path.exists(d))

    @mock.patch("fs_util.delete_btrfs_subvolume", return_value=True)
    @mock.patch("fs_util.is_btrfs_subvolume", return_value=True)
    def test_remove_path_subvolume(self, _mock_is_sub, mock_delete):
        d = os.path.join(self.tmpdir, "subvol")
        os.makedirs(d)
        self.assertTrue(fs_util.remove_path(d))
        mock_delete.assert_called_once_with(d, recursive=True)

    @mock.patch("subprocess.run")
    def test_snapshot_btrfs_subvolume_success(self, mock_run):
        mock_run.return_value.returncode = 0
        src = os.path.join(self.tmpdir, "src_subvol")
        dst = os.path.join(self.tmpdir, "dst_subvol")
        os.makedirs(src)

        self.assertTrue(fs_util.snapshot_btrfs_subvolume(src, dst))
        mock_run.assert_called_once_with(
            ["btrfs", "subvolume", "snapshot", src, dst],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    @mock.patch("subprocess.run")
    def test_snapshot_btrfs_subvolume_existing_dst_removed(self, mock_run):
        mock_run.return_value.returncode = 0
        src = os.path.join(self.tmpdir, "src_subvol")
        dst = os.path.join(self.tmpdir, "dst_subvol")
        os.makedirs(src)
        os.makedirs(dst)

        with mock.patch("fs_util.remove_path", return_value=True) as mock_rm:
            self.assertTrue(fs_util.snapshot_btrfs_subvolume(src, dst))
            mock_rm.assert_called_once_with(dst)

    def test_ensure_directory_or_subvolume_existing(self):
        d = os.path.join(self.tmpdir, "existing_dir")
        os.makedirs(d)
        self.assertTrue(fs_util.ensure_directory_or_subvolume(d))

    @mock.patch("fs_util.create_btrfs_subvolume", return_value=True)
    @mock.patch("fs_util.get_filesystem_type", return_value="btrfs")
    def test_ensure_directory_or_subvolume_btrfs(self, _mock_fs, mock_create):
        target = os.path.join(self.tmpdir, "new_subvol")
        self.assertTrue(fs_util.ensure_directory_or_subvolume(target))
        mock_create.assert_called_once_with(target)

    @mock.patch("fs_util.create_btrfs_subvolume", return_value=False)
    @mock.patch("fs_util.get_filesystem_type", return_value="btrfs")
    def test_ensure_directory_or_subvolume_btrfs_fallback(
        self, _mock_fs, _mock_create
    ):
        target = os.path.join(self.tmpdir, "new_fallback_dir")
        self.assertTrue(fs_util.ensure_directory_or_subvolume(target))
        self.assertTrue(os.path.isdir(target))

    @mock.patch("fs_util.get_filesystem_type", return_value="ext4")
    def test_ensure_directory_or_subvolume_non_btrfs(self, _mock_fs):
        target = os.path.join(self.tmpdir, "normal_dir")
        self.assertTrue(fs_util.ensure_directory_or_subvolume(target))
        self.assertTrue(os.path.isdir(target))


if __name__ == "__main__":
    unittest.main()

# vim: ts=2:sw=2:tw=80:et:
