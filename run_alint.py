#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import re
import subprocess
import sys


def strip_ansi_codes(text):
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def main():
    if len(sys.argv) < 2:
        print("Usage: run_alint.py <repo_root> [alint_args...]")
        return 3

    repo_root = sys.argv[1]
    # Change CWD to the repository root. This is necessary because
    # presubmit_support.py has a workaround when testing depot_tools that
    # changes the process CWD to the parent directory of depot_tools.
    # alint requires running from inside a git repository to detect changes.
    os.chdir(repo_root)

    alint_path = '/google/bin/releases/alint/alint'
    if not os.path.exists(alint_path):
        return 0

    cmd = [alint_path, '--'] + sys.argv[2:]
    try:
        p = subprocess.Popen(cmd,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
        stdout, _ = p.communicate()
        output_str = stdout.decode('utf-8', 'ignore')
        if not output_str.strip():
            return 0

        clean_output = strip_ansi_codes(output_str)
        print(clean_output)
        return 1
    except Exception as e:
        print(f"Error running alint wrapper: {e}")
        return 2


if __name__ == '__main__':
    sys.exit(main())
