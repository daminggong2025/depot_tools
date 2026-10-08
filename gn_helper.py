# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""This provides an easy way to access args.gn."""

import os
import re
import sys


def _find_root(output_dir):
    curdir = output_dir
    while True:
        if os.path.exists(os.path.join(curdir, ".gn")):
            return curdir
        nextdir = os.path.join(curdir, "..")
        if os.path.abspath(curdir) == os.path.abspath(nextdir):
            raise Exception(
                "Could not find checkout in any parent of the current path."
            )
        curdir = nextdir


def _is_within(child, parent):
    real_child = os.path.realpath(child)
    real_parent = os.path.realpath(parent)
    try:
        return os.path.commonpath([real_child, real_parent]) == real_parent
    except ValueError:
        return False


def _gn_lines(output_dir, path, _seen=None):
    """
    Generator function that returns args.gn lines one at a time, following
    import directives as needed.
    """
    if _seen is None:
        _seen = set()
    real_path = os.path.realpath(path)
    if real_path in _seen:
        raise IOError(f"Recursive import cycle detected for {path}")
    _seen = _seen | {real_path}

    try:
        root = _find_root(output_dir)
    except Exception:
        root = None

    allowed_roots = [os.path.dirname(path), output_dir]
    if root is not None:
        allowed_roots.append(root)

    import_re = re.compile(r'\s*import\("(.*)"\)')
    with open(path, encoding="utf-8") as f:
        for line in f:
            match = import_re.match(line)
            if match:
                raw_import_path = match.groups()[0]
                if raw_import_path[:2] == "//":
                    if root is None:
                        root = _find_root(output_dir)
                        allowed_roots.append(root)
                    import_path = os.path.normpath(
                        os.path.join(root, raw_import_path[2:])
                    )
                elif raw_import_path.startswith("/"):
                    # GN uses "/"-prefix as absolute path,
                    # https://gn.googlesource.com/gn/+/main/docs/reference.md#labels
                    # e.g.
                    #  /usr/local/foo:bar
                    #  /C:/Program Files/MyLibs:bar
                    # but Win32's absolute path doesn't have "/"-prefix.
                    if sys.platform.startswith("win32"):
                        import_path = raw_import_path[1:]
                    else:
                        import_path = raw_import_path
                    if not os.path.isabs(import_path):
                        raise Exception(
                            "Wrong absolute path for import %s"
                            % raw_import_path
                        )
                else:
                    if os.path.isabs(raw_import_path):
                        raise Execption(  # noqa: F821
                            'Absolute path "%s" should start with "/" in GN'
                            % raw_import_path
                        )
                    import_path = os.path.normpath(
                        os.path.join(os.path.dirname(path), raw_import_path)
                    )
                if not any(
                    _is_within(import_path, base) for base in allowed_roots
                ):
                    raise IOError(
                        f"Import path {raw_import_path} escapes allowed directories"
                    )
                yield from _gn_lines(output_dir, import_path, _seen)
            else:
                yield line


_args_gn_lines = _gn_lines


def _path(output_dir):
    return os.path.join(output_dir, "args.gn")


def exists(output_dir):
    """Checks args.gn exists in output_dir."""
    return os.path.exists(_path(output_dir))


def lines(output_dir):
    """Generator of args.gn lines. comment is removed."""
    if not exists(output_dir):
        return
    for line in _gn_lines(output_dir, _path(output_dir)):
        line_without_comment = line.split("#")[0]
        yield line_without_comment


_gn_arg_pattern = re.compile(r"(^|\s*)([^=\s]*)\s*=\s*(\S*)\s*$")


def args(output_dir):
    """Generator of args.gn's key,value pair."""
    for line in lines(output_dir):
        m = _gn_arg_pattern.match(line)
        if not m:
            continue
        yield (m.group(2), m.group(3))
