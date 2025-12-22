# /usr/bin/env python3

# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""A general-purpose formatter that dispatches to language-specific formatters.

This is the open-source equivalent of `google3format`

Similar to google3, no configuration is supported, as this instead provides a
precise spec.

* The first and only argument must be the name of the file
* The old contents of the file must be passed to stdin
* The new contents of the file must be output to stdout
* The formatter may make no assumptions about files being present
  * But if certain files (eg. rustfmt.toml) are present it may use them.
* This script is not allowed to error for known extensions, but the formatters
  it dispatches to are
* The script must work regardless of which repo it is running in, or even if it
  is not running in a repo at all.
"""

import argparse
import fnmatch
import pathlib
import os
import shutil
import sys

import clang_format
import gclient_paths
import gn
import google_java_format
import rustfmt
import swift_format

DEPOT_TOOLS = pathlib.Path(__file__).parent


def run_formatter(base: list[str | pathlib.Path | None] | None = None):
    """Runs a formatter with given arguments"""
    base = base or []
    if os.environ.get("DEBUG", False):
        print("Running", base, file=sys.stderr)
    if base and all(base):
        os.execvp(base[0], base)
    else:
        # We couldn't find a formatter. So don't format at all.
        shutil.copyfileobj(sys.stdin, sys.stdout)


def components(path: pathlib.Path):
    """Splits a path into components until the repo boundary."""
    d = path.parent
    while True:
        yield d
        # Formatting configuration should not cross repository boundaries.
        if d.parent == d or (d / '.git').exists() or (d / '.jj').exists():
            break
        d = d.parent


def _yapf_style(path: pathlib.Path) -> str:
    """Calculates the name of the yapf file for a given path"""
    for d in components(path):
        if (d / '.style.yapf').exists():
            return str(d / '.style.yapf')
    return 'pep8'


def _yapf_ignore_patterns(
        path: pathlib.Path) -> tuple[pathlib.Path, list[str]]:
    """Calculates a list of patterns and the directory they are relative to"""
    for d in components(path):
        if (d / '.yapfignore').exists():
            lines = [
                line.strip()
                for line in (d / '.yapfignore').read_text().split('\n')
            ]
            # Strip comments
            return (d, [
                line for line in lines if line and not line.startswith('#')
            ])
    # Don't ignore anything
    return (path, [])


def handle_python(path: pathlib.Path):
    base, ignore = _yapf_ignore_patterns(path)
    rel = str(path.relative_to(base))
    if any(fnmatch.fnmatch(rel, p) for p in ignore):
        run_formatter()
    else:
        run_formatter([
            'vpython3',
            DEPOT_TOOLS / 'yapf',
            f'--style={_yapf_style(path)}',
        ])


def handle_gn(path: pathlib.Path):
    run_formatter([gn.FindGnTool(), 'format', '--stdin'])


def handle_swift(path: pathlib.Path):
    run_formatter([
        swift_format.FindSwiftFormatTool(), 'format',
        f'--assume-filename={path}'
    ])


def handle_rust(path: pathlib.Path):
    run_formatter([rustfmt.FindRustfmtTool(), *rustfmt.GetRustfmtArgs()])


def handle_star(path: pathlib.Path):
    # For now, we just assume that all `.star` files passed to this formatter
    # are lucicfg, to match the behavior of `git cl format`.
    # Tooling should be configured to only pass lucicfg files here.
    fname = 'lucicfg.bat' if sys.platform == 'win32' else 'lucicfg'
    lucicfg = DEPOT_TOOLS / fname
    run_formatter([lucicfg, 'fmt', '-stdio', path])


def handle_java(path: pathlib.Path):
    run_formatter([google_java_format.FindGoogleJavaFormat(), '-'])


def handle_mojom(path: pathlib.Path):
    primary_solution_path = gclient_paths.GetPrimarySolutionPath()
    if not primary_solution_path:
        return run_formatter()
    mojom_format_path = pathlib.Path(
        primary_solution_path, 'mojo/public/tools/mojom/mojom_format.py')
    if not mojom_format_path.exists():
        return run_formatter()
    run_formatter(['vpython3', mojom_format_path])


def handle_go(path: pathlib.Path):
    gofmt = shutil.which('gofmt')
    if gofmt is None:
        print(
            f"Unable to format {path}: gofmt is not available",
            file=sys.stderr,
        )
    run_formatter([gofmt])


def handle_clang_format(path: pathlib.Path):
    run_formatter(
        [clang_format.FindClangFormatTool(), f'--assume-filename={path}'])


# We could in the future have it inspect shebangs as well as file extensions,
# but we want to keep the behavior in line with `git cl format`.
HANDLERS = [
    (handle_gn, ['gn', 'gni', 'typemap']),
    (handle_star, ['star']),
    (handle_swift, ['swift']),
    (handle_mojom, ['mojom', 'test-mojom']),
    (handle_java, ['java']),
    (handle_go, ['go']),
    (handle_clang_format, ['cc', 'cpp', 'h', 'm', 'mm', 'proto', 'js', 'ts']),
    (handle_python, ['py']),
]

EXT_TO_HANDLER = {v: k for k, vs in HANDLERS for v in vs}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--assume-filename',
        required=True,
        help=
        'The filename used to determine the language and find formatter configuration.',
        type=pathlib.Path,
    )
    args = parser.parse_args(argv)
    fname = args.assume_filename

    suffix = fname.suffix.lstrip('.')
    if suffix not in EXT_TO_HANDLER:
        raise NotImplementedError(
            f'Unsupported file extension: {suffix}. Change your configuration to not pass this file to the formatter.'
        )
    EXT_TO_HANDLER[suffix](fname)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
