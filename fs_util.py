# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Filesystem copy-on-write (reflink/block-cloning) utilities and providers."""

import abc
import ctypes
import logging
import os
import shutil
import subprocess
import sys


class FilesystemProvider(abc.ABC):
    """Abstract base class for filesystem copy providers."""

    @abc.abstractmethod
    def copy(self, src, dst):
        """Copies or reflinks src to dst.

        Args:
            src: Source file or directory path.
            dst: Destination file or directory path.

        Returns:
            True if the copy/clone succeeded, False otherwise.
        """

    @property
    @abc.abstractmethod
    def supports_cow(self):
        """Returns True if the provider supports copy-on-write optimizations."""

    @property
    @abc.abstractmethod
    def fstype(self):
        """Returns the filesystem or provider type name."""

    @property
    def supports_snapshots(self):
        """Returns True if the provider supports subvolume snapshots."""
        return False


class DefaultFilesystemProvider(FilesystemProvider):
    """Fallback filesystem provider using standard file copies."""

    def __init__(self, fstype=None):
        self._fstype = fstype or "default"

    def copy(self, src, dst):
        try:
            if os.path.isdir(src):
                os.makedirs(dst, exist_ok=True)
                for item in os.listdir(src):
                    s = os.path.join(src, item)
                    d = os.path.join(dst, item)
                    if os.path.isdir(s):
                        shutil.copytree(s, d, dirs_exist_ok=True)
                    else:
                        shutil.copy2(s, d)
            else:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
            return True
        except (OSError, shutil.Error) as e:
            logging.debug("Default copy failed from %s to %s: %s", src, dst, e)
            return False

    @property
    def supports_cow(self):
        return False

    @property
    def fstype(self):
        return self._fstype


class LinuxFilesystemProvider(FilesystemProvider):
    """Linux filesystem provider using GNU cp --reflink=auto."""

    def __init__(self, fallback=None, fstype=None):
        self._fallback = fallback or DefaultFilesystemProvider()
        self._fstype = fstype

    def copy(self, src, dst):
        try:
            if os.path.isdir(src):
                os.makedirs(dst, exist_ok=True)
                res = subprocess.run(
                    ["cp", "-a", "--reflink=auto", os.path.join(src, "."), dst],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if res.returncode == 0:
                    return True
            else:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                res = subprocess.run(
                    ["cp", "-a", "--reflink=auto", src, dst],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if res.returncode == 0:
                    return True
        except (subprocess.SubprocessError, OSError) as e:
            logging.debug("Linux reflink failed, falling back to copy: %s", e)

        return self._fallback.copy(src, dst)

    @property
    def supports_cow(self):
        if self._fstype in ("ext4", "ext3", "ext2", "vfat", "fat", "ntfs"):
            return False
        return True

    @property
    def fstype(self):
        return self._fstype or "linux"


class BtrfsFilesystemProvider(LinuxFilesystemProvider):
    """Btrfs filesystem provider supporting subvolume snapshots and reflink cloning."""

    def __init__(self, fallback=None):
        super().__init__(fallback=fallback, fstype="btrfs")

    def copy(self, src, dst):
        if is_btrfs_subvolume(src):
            if snapshot_btrfs_subvolume(src, dst):
                return True
            logging.debug(
                "Btrfs subvolume snapshot failed from %s to %s, "
                "falling back to Linux reflink copy",
                src,
                dst,
            )
        return super().copy(src, dst)

    @property
    def supports_snapshots(self):
        return True

    @property
    def fstype(self):
        return "btrfs"


class APFSFilesystemProvider(FilesystemProvider):
    """macOS filesystem provider using APFS copy-on-write (cp -c)."""

    def __init__(self, fallback=None):
        self._fallback = fallback or DefaultFilesystemProvider()

    def copy(self, src, dst):
        try:
            if os.path.isdir(src):
                os.makedirs(dst, exist_ok=True)
                res = subprocess.run(
                    ["cp", "-R", "-p", "-c", os.path.join(src, "."), dst],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if res.returncode == 0:
                    return True
            else:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                res = subprocess.run(
                    ["cp", "-p", "-c", src, dst],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if res.returncode == 0:
                    return True
        except (subprocess.SubprocessError, OSError) as e:
            logging.debug(
                "macOS APFS clone failed, falling back to copy: %s", e
            )

        return self._fallback.copy(src, dst)

    @property
    def supports_cow(self):
        return True

    @property
    def fstype(self):
        return "apfs"


class WindowsBlockCloneProvider(FilesystemProvider):
    """Windows provider using ReFS / Dev Drive block cloning."""

    def __init__(self, fallback=None):
        self._fallback = fallback or DefaultFilesystemProvider()

    def _clone_file(self, src, dst):
        """Clones a single file using FSCTL_DUPLICATE_EXTENTS_TO_FILE."""
        from ctypes import wintypes

        kernel32 = getattr(ctypes, "windll", None)
        if not kernel32:
            return False
        kernel32 = kernel32.kernel32

        generic_read = 0x80000000
        generic_write = 0x40000000
        file_share_read = 0x00000001
        file_share_write = 0x00000002
        open_existing = 3
        create_always = 2
        file_attribute_normal = 0x80
        invalid_handle_value = wintypes.HANDLE(-1).value
        fsctl_duplicate_extents = 0x00098344

        class LARGE_INTEGER(ctypes.Union):
            _fields_ = [("QuadPart", ctypes.c_longlong)]

        class DUPLICATE_EXTENTS_DATA(ctypes.Structure):
            _fields_ = [
                ("FileHandle", wintypes.HANDLE),
                ("SourceByteOffset", LARGE_INTEGER),
                ("TargetByteOffset", LARGE_INTEGER),
                ("ByteCount", LARGE_INTEGER),
            ]

        # Configure Win32 function signatures for 64-bit handle safety
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE

        kernel32.GetFileSizeEx.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(LARGE_INTEGER),
        ]
        kernel32.GetFileSizeEx.restype = wintypes.BOOL

        kernel32.SetFilePointerEx.argtypes = [
            wintypes.HANDLE,
            LARGE_INTEGER,
            ctypes.POINTER(LARGE_INTEGER),
            wintypes.DWORD,
        ]
        kernel32.SetFilePointerEx.restype = wintypes.BOOL

        kernel32.SetEndOfFile.argtypes = [wintypes.HANDLE]
        kernel32.SetEndOfFile.restype = wintypes.BOOL

        kernel32.DeviceIoControl.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        kernel32.DeviceIoControl.restype = wintypes.BOOL

        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        src_handle = kernel32.CreateFileW(
            src,
            generic_read,
            file_share_read | file_share_write,
            None,
            open_existing,
            file_attribute_normal,
            None,
        )
        if not src_handle or src_handle == invalid_handle_value:
            raise ctypes.WinError()

        dest_handle = None
        try:
            size = LARGE_INTEGER()
            if not kernel32.GetFileSizeEx(src_handle, ctypes.byref(size)):
                raise ctypes.WinError()

            dest_handle = kernel32.CreateFileW(
                dst,
                generic_write,
                file_share_read | file_share_write,
                None,
                create_always,
                file_attribute_normal,
                None,
            )
            if not dest_handle or dest_handle == invalid_handle_value:
                raise ctypes.WinError()

            if not kernel32.SetFilePointerEx(dest_handle, size, None, 0):
                raise ctypes.WinError()
            if not kernel32.SetEndOfFile(dest_handle):
                raise ctypes.WinError()

            alignment = 64 * 1024
            aligned_size = (size.QuadPart // alignment) * alignment
            chunk_size = 1024 * 1024 * 1024

            if aligned_size > 0:
                zero = LARGE_INTEGER()
                zero.QuadPart = 0
                if not kernel32.SetFilePointerEx(dest_handle, zero, None, 0):
                    raise ctypes.WinError()

                offset = 0
                while offset < aligned_size:
                    bytes_to_clone = min(chunk_size, aligned_size - offset)
                    data = DUPLICATE_EXTENTS_DATA()
                    data.FileHandle = src_handle
                    data.SourceByteOffset.QuadPart = offset
                    data.TargetByteOffset.QuadPart = offset
                    data.ByteCount.QuadPart = bytes_to_clone

                    bytes_returned = wintypes.DWORD()
                    res = kernel32.DeviceIoControl(
                        dest_handle,
                        fsctl_duplicate_extents,
                        ctypes.byref(data),
                        ctypes.sizeof(data),
                        None,
                        0,
                        ctypes.byref(bytes_returned),
                        None,
                    )
                    if not res:
                        raise ctypes.WinError()
                    offset += bytes_to_clone
        finally:
            if dest_handle and dest_handle != invalid_handle_value:
                kernel32.CloseHandle(dest_handle)
            if src_handle and src_handle != invalid_handle_value:
                kernel32.CloseHandle(src_handle)

        remaining = size.QuadPart - aligned_size
        if remaining > 0:
            mode = "r+b" if os.path.exists(dst) else "w+b"
            with open(src, "rb") as sf, open(dst, mode) as df:
                sf.seek(aligned_size)
                df.seek(aligned_size)
                df.write(sf.read())
        return True

    def _clone_dir(self, src, dst):
        """Recursively clones a directory using block cloning."""
        os.makedirs(dst, exist_ok=True)
        for root, dirs, files in os.walk(src):
            rel_path = os.path.relpath(root, src)
            dest_dir = os.path.join(dst, rel_path) if rel_path != "." else dst
            for d in dirs:
                os.makedirs(os.path.join(dest_dir, d), exist_ok=True)
            for f in files:
                s_file = os.path.join(root, f)
                d_file = os.path.join(dest_dir, f)
                self._clone_file(s_file, d_file)
        return True

    def copy(self, src, dst):
        try:
            src_drive = os.path.splitdrive(os.path.abspath(src))[0].lower()
            dst_drive = os.path.splitdrive(os.path.abspath(dst))[0].lower()
            if src_drive != dst_drive:
                logging.debug(
                    "Block cloning across different volumes is not supported "
                    "(%s -> %s), falling back to standard copy.",
                    src,
                    dst,
                )
                return self._fallback.copy(src, dst)

            if os.path.isdir(src):
                if self._clone_dir(src, dst):
                    return True
            else:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if self._clone_file(src, dst):
                    return True
        except (OSError, AttributeError, ValueError) as e:
            logging.debug(
                "Windows block clone failed, falling back to copy: %s", e
            )

        return self._fallback.copy(src, dst)

    @property
    def supports_cow(self):
        return True

    @property
    def fstype(self):
        return "refs"


FILE_SUPPORTS_BLOCK_CLONING = 0x00800000


def get_windows_volume_info(path):
    """Queries Windows volume flags and filesystem name for the given path.

    Args:
        path: File or directory path on a Windows volume.

    Returns:
        tuple of (flags: int, file_system_name: str).
    """
    kernel32 = getattr(ctypes, "windll", None)
    if not kernel32:
        raise OSError("ctypes.windll is not available on this platform")
    kernel32 = kernel32.kernel32

    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if drive and not drive.endswith(("\\", "/")):
        drive += "\\"

    volume_name = ctypes.create_unicode_buffer(1024)
    file_system_name = ctypes.create_unicode_buffer(1024)
    serial_number = ctypes.c_ulong()
    max_component_len = ctypes.c_ulong()
    flags = ctypes.c_ulong()

    res = kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(drive) if drive else None,
        volume_name,
        1024,
        ctypes.byref(serial_number),
        ctypes.byref(max_component_len),
        ctypes.byref(flags),
        file_system_name,
        1024,
    )
    if not res:
        win_error = getattr(ctypes, "WinError", OSError)
        raise win_error()
    return flags.value, file_system_name.value


def get_linux_filesystem_type(path):
    """Detects the filesystem type for a path on Linux."""
    target = os.path.abspath(path)
    while not os.path.exists(target):
        parent = os.path.dirname(target)
        if parent == target:
            break
        target = parent
    try:
        res = subprocess.run(
            ["findmnt", "-no", "FSTYPE", "--target", target],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip().lower()
    except (subprocess.SubprocessError, OSError) as e:
        logging.debug("Failed to query filesystem type via findmnt: %s", e)

    # Fallback to reading /proc/mounts if findmnt is unavailable
    if os.path.isfile("/proc/mounts"):
        try:
            norm_target = target.replace("\\", "/")
            if (
                len(norm_target) >= 2
                and norm_target[1] == ":"
                and norm_target[0].isalpha()
            ):
                norm_target = norm_target[2:]
            best_match = ""
            best_fstype = None
            with open("/proc/mounts", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3:
                        mountpoint, fstype = parts[1], parts[2]
                        if norm_target == mountpoint or norm_target.startswith(
                            mountpoint.rstrip("/") + "/"
                        ):
                            if len(mountpoint) > len(best_match):
                                best_match = mountpoint
                                best_fstype = fstype
            if best_fstype:
                return best_fstype.lower()
        except OSError as e:
            logging.debug("Failed to read /proc/mounts: %s", e)

    return None


def get_darwin_filesystem_type(path):
    """Detects the filesystem type for a path on macOS."""
    target = os.path.abspath(path)
    while not os.path.exists(target):
        parent = os.path.dirname(target)
        if parent == target:
            break
        target = parent
    try:
        res = subprocess.run(
            ["stat", "-f", "%t", target],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip().lower()
    except (subprocess.SubprocessError, OSError) as e:
        logging.debug("Failed to query Darwin filesystem type via stat: %s", e)

    return None


def get_filesystem_type(path=None, platform=None):
    """Detects the filesystem type for the specified path and platform.

    Args:
        path: Path to inspect (defaults to current working directory).
        platform: Platform name override (defaults to sys.platform).

    Returns:
        Filesystem name string in lowercase (e.g. 'refs', 'ntfs', 'btrfs',
        'ext4', 'apfs', 'zfs') or None if undetected.
    """
    plat = platform or sys.platform
    target = path or os.getcwd()

    if plat.startswith(("cygwin", "win")):
        try:
            _, fs_name = get_windows_volume_info(target)
            return fs_name.lower()
        except (OSError, AttributeError) as e:
            logging.debug(
                "Failed to get Windows volume info for %s: %s", target, e
            )
            return None
    if plat.startswith("linux"):
        return get_linux_filesystem_type(target)
    if plat == "darwin":
        return get_darwin_filesystem_type(target)
    return None


def is_btrfs_subvolume(path):
    """Returns True if path is the root of a Btrfs subvolume or snapshot."""
    try:
        if os.path.islink(path) or not os.path.isdir(path):
            return False
        st = os.stat(path)
        if st.st_ino != 256:
            return False
        return get_filesystem_type(path) == "btrfs"
    except OSError as e:
        logging.debug("Error checking if %s is btrfs subvolume: %s", path, e)
        return False


def create_btrfs_subvolume(path):
    """Creates a Btrfs subvolume at path.

    Args:
        path: Path where the subvolume will be created.

    Returns:
        True if the subvolume was created successfully, False otherwise.
    """
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        res = subprocess.run(
            ["btrfs", "subvolume", "create", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            return True
        logging.debug(
            "btrfs subvolume create failed for %s: %s", path, res.stderr
        )
        return False
    except (subprocess.SubprocessError, OSError) as e:
        logging.debug("Exception creating btrfs subvolume at %s: %s", path, e)
        return False


def delete_btrfs_subvolume(path, recursive=True):
    """Deletes a Btrfs subvolume at path.

    Args:
        path: Path to the subvolume to delete.
        recursive: If True, delete nested subvolumes recursively.

    Returns:
        True if the subvolume was deleted successfully, False otherwise.
    """
    if not os.path.exists(path) and not os.path.islink(path):
        return True
    try:
        cmd = ["btrfs", "subvolume", "delete"]
        if recursive:
            cmd.append("-R")
        cmd.append(path)
        res = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            return True
        logging.debug(
            "btrfs subvolume delete failed for %s: %s", path, res.stderr
        )
        # If recursive deletion failed with -R (e.g. older btrfs-progs or
        # non-root iteration limits), traverse bottom-up to delete nested
        # subvolumes first, then retry deleting the root subvolume.
        if recursive and os.path.exists(path):
            for root, dirs, _ in os.walk(path, topdown=False):
                for d in dirs:
                    sub = os.path.join(root, d)
                    if is_btrfs_subvolume(sub):
                        delete_btrfs_subvolume(sub, recursive=False)
            res2 = subprocess.run(
                ["btrfs", "subvolume", "delete", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if res2.returncode == 0:
                return True
            logging.debug(
                "btrfs subvolume delete retry failed for %s: %s",
                path,
                res2.stderr,
            )
        return False
    except (subprocess.SubprocessError, OSError) as e:
        logging.debug("Exception deleting btrfs subvolume at %s: %s", path, e)
        return False


def remove_path(path):
    """Removes a file, directory, or subvolume at path."""
    if not os.path.exists(path) and not os.path.islink(path):
        return True
    if is_btrfs_subvolume(path):
        return delete_btrfs_subvolume(path, recursive=True)
    if os.path.isdir(path) and not os.path.islink(path):
        try:
            shutil.rmtree(path)
            return True
        except OSError as e:
            logging.debug(
                "shutil.rmtree failed for %s: %s, cleaning nested subvolumes",
                path,
                e,
            )
            for root, dirs, _ in os.walk(path, topdown=False):
                for d in dirs:
                    sub = os.path.join(root, d)
                    if is_btrfs_subvolume(sub):
                        delete_btrfs_subvolume(sub, recursive=True)
            try:
                shutil.rmtree(path)
                return True
            except OSError as e2:
                logging.debug(
                    "Second shutil.rmtree failed for %s: %s", path, e2
                )
                return False
    else:
        try:
            os.remove(path)
            return True
        except OSError as e:
            logging.debug("Failed to remove %s: %s", path, e)
            return False


def snapshot_btrfs_subvolume(src, dst):
    """Creates a Btrfs snapshot of src at dst.

    If dst exists, it is removed prior to creating the snapshot.

    Args:
        src: Path to source subvolume.
        dst: Path to destination snapshot.

    Returns:
        True if the snapshot succeeded, False otherwise.
    """
    try:
        if os.path.exists(dst) or os.path.islink(dst):
            if not remove_path(dst):
                return False
        parent = os.path.dirname(os.path.abspath(dst))
        if parent:
            os.makedirs(parent, exist_ok=True)
        res = subprocess.run(
            ["btrfs", "subvolume", "snapshot", src, dst],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            return True
        logging.debug(
            "btrfs subvolume snapshot failed from %s to %s: %s",
            src,
            dst,
            res.stderr,
        )
        return False
    except (subprocess.SubprocessError, OSError) as e:
        logging.debug(
            "Exception snapshotting btrfs subvolume from %s to %s: %s",
            src,
            dst,
            e,
        )
        return False


def ensure_directory_or_subvolume(path):
    """Ensures path exists as a directory or Btrfs subvolume.

    If path does not exist and its parent resides on Btrfs, attempts to create
    it as a Btrfs subvolume so subsequent operations can use subvolume snapshots.
    Falls back to os.makedirs if subvolume creation is not supported or fails.

    Args:
        path: Path to create.

    Returns:
        True if path exists as a directory or subvolume, False otherwise.
    """
    if os.path.isdir(path):
        return True
    try:
        fstype = get_filesystem_type(os.path.dirname(os.path.abspath(path)))
        if fstype == "btrfs":
            if create_btrfs_subvolume(path):
                return True
            logging.debug(
                "Failed to create btrfs subvolume at %s, falling back to makedirs",
                path,
            )
        os.makedirs(path, exist_ok=True)
        return os.path.isdir(path)
    except OSError as e:
        logging.debug(
            "Failed to ensure directory or subvolume at %s: %s", path, e
        )
        return False


def get_windows_filesystem_provider(path=None):
    """Resolves the appropriate Windows filesystem provider."""
    if path:
        try:
            flags, fs_name = get_windows_volume_info(path)
            if flags & FILE_SUPPORTS_BLOCK_CLONING:
                return WindowsBlockCloneProvider()
            return DefaultFilesystemProvider(fstype=fs_name.lower())
        except (OSError, AttributeError) as e:
            logging.debug(
                "Failed to get Windows filesystem provider for %s: %s", path, e
            )
            return WindowsBlockCloneProvider()
    return WindowsBlockCloneProvider()


def get_filesystem_provider(path=None, platform=None):
    """Factory returning the appropriate filesystem provider for the path/platform.

    Args:
        path: Optional path to inspect filesystem capabilities.
        platform: Optional platform string override.

    Returns:
        FilesystemProvider instance.
    """
    if (
        path
        and platform is None
        and path
        in (
            "linux",
            "linux2",
            "darwin",
            "win32",
            "cygwin",
            "other",
        )
    ):
        platform = path
        path = None

    plat = platform or sys.platform
    if plat.startswith("linux"):
        fstype = get_linux_filesystem_type(path) if path else None
        if fstype == "btrfs":
            return BtrfsFilesystemProvider()
        return LinuxFilesystemProvider(fstype=fstype)
    if plat == "darwin":
        return APFSFilesystemProvider()
    if plat.startswith(("cygwin", "win")):
        return get_windows_filesystem_provider(path)
    return DefaultFilesystemProvider()


def reflink_or_copy(src, dst, provider=None):
    """Reflinks or copies src to dst using CoW where supported, with fallback."""
    provider = provider or get_filesystem_provider(path=src)
    return provider.copy(src, dst)


# vim: ts=2:sw=2:tw=80:et:
