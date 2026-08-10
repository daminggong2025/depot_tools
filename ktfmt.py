#!/usr/bin/env python3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Redirects to the version of ktfmt checked into the Chrome tree.

ktfmt executable is pulled down from the cipd storage whenever
you sync Chrome. This script finds and runs the executable.
"""

import gclient_paths
import os
import subprocess
import sys


def FindKtfmt():
    """Returns the path to the ktfmt executable."""
    # Allow non-chromium projects to use a custom location.
    primary_solution_path = gclient_paths.GetPrimarySolutionPath()
    if primary_solution_path:
        override = os.environ.get("KTFMT_PATH")
        if override:
            # Make relative to solution root if not an absolute path.
            return os.path.join(primary_solution_path, override)

        bin_path = os.path.join(
            primary_solution_path,
            "third_party",
            "ktfmt",
            "ktfmt",
        )
        cipd_path = os.path.join(
            primary_solution_path,
            "third_party",
            "ktfmt",
            "cipd",
            "ktfmt.jar",
        )

        # Check that the .jar exists, since it is conditionally downloaded via
        # DEPS conditions.
        if os.path.exists(bin_path) and (
            os.path.exists(bin_path + ".jar") or os.path.exists(cipd_path)
        ):
            return bin_path
    return None


def main(args):
    ktfmt = FindKtfmt()
    if ktfmt is None:
        print(
            'ktfmt not found. Please run "gclient sync" to '
            "download build tools.",
            file=sys.stderr,
        )
        return 2

    # Add some visibility to --help showing where the tool lives, since this
    # redirection can be a little opaque.
    help_syntax = ("-h", "--help", "-help", "-help-list", "--help-list")
    if any(match in args for match in help_syntax):
        print(
            "\nDepot tools redirects you to the ktfmt at:\n"
            + "    %s\n" % ktfmt
        )

    return subprocess.call([ktfmt] + args)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.stderr.write("interrupted\n")
        sys.exit(1)
