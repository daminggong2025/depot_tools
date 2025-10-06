#!/usr/bin/env python3
# Copyright (c) 2023 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""A git pre-commit hook to drop staged gitlink changes.

To bypass this hook, set SKIP_GITLINK_PRECOMMIT=1.
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

import git_common
from gclient_eval import SYNC

SKIP_VAR = 'SKIP_GITLINK_PRECOMMIT'
TESTING_ANSWER = 'TESTING_ANSWER'
GIT_CONFIG_VAR = 'precommit.commit-gitlinks'

def main():
    commit_gitlinks = git_common.run("config", GIT_CONFIG_VAR)
    if commit_gitlinks is not None:
        if commit_gitlinks.lower() == "true":
            commit_gitlinks = True
        elif commit_gitlinks.lower() == "false":
            commit_gitlinks = False
        else:
            print(
                f"{GIT_CONFIG_VAR} is set to {commit_gitlinks}, which is not a correct parameter."
            )
            commit_gitlinks = None
    if os.getenv(SKIP_VAR) == '1':
        print(f'{SKIP_VAR} is set. Committing gitlinks, if any.')
        exit(0)
    if commit_gitlinks is True:
        print(f'{GIT_CONFIG_VAR} config is True. Committing gitlinks, if any.')
        exit(0)

    has_deps_diff = False
    staged_gitlinks = []
    diff = git_common.run('diff-index', '--cached', '--ignore-submodules=dirty',
                          'HEAD')
    for line in diff.splitlines():
        path = line.split()[-1]
        if path == 'DEPS':
            has_deps_diff = True
            continue
        if line.startswith(':160000 160000'):
            staged_gitlinks.append(path)

    if not staged_gitlinks or has_deps_diff:
        exit(0)

    # There are staged gitlinks and DEPS wasn't changed. Get git_dependencies
    # migration state in DEPS.
    state = None
    try:
        with open('DEPS', 'r') as f:
            for l in f.readlines():
                if l.startswith('git_dependencies'):
                    state = l.split()[-1].strip(' "\'')
                    break
    except OSError:
        # Don't abort the commit if DEPS wasn't found.
        exit(0)

    if state != SYNC:
        # DEPS only has to be in sync with gitlinks when state is SYNC.
        exit(0)

    found_message = (
        f'Found no change to DEPS, but found staged gitlink(s) in diff:\n{diff}\n'
    )
    print(found_message)

    disable_msg = None
    if os.getenv(TESTING_ANSWER) is not None:
        unstage = os.getenv(TESTING_ANSWER) == "n"
    elif commit_gitlinks is False:
        unstage = commit_gitlinks is False
        print(f'{commit_gitlinks} config is False. Unstaging gitlinks.')
    else:
        prompt_message = (
            'Press Enter/Return if you intended to include them or "n" to unstage '
            '(exclude from commit) the gitlink(s): ')
        print(prompt_message)
        try:
            sys.stdin = open("/dev/tty", "r")
        except (FileNotFoundError, OSError):
            try:
                sys.stdin = open('CON')
            except:
                print(
                    'Unable to acquire input handle, proceeding without modifications'
                )
                exit(0)
        unstage = input()

        disable_msg = (
            f'To disable this hook, execute `git config set {GIT_CONFIG_VAR} True`. '
            f'To always unstage gitlinks, execute `git config set {GIT_CONFIG_VAR} False`. '
        )
    if unstage:
        print(
            f'\nUnstaging {len(staged_gitlinks)} staged gitlink(s) found in diff'
        )
        git_common.run('restore', '--staged', '--', *staged_gitlinks)
        if len(staged_gitlinks) == len(diff.splitlines()):
            print(
                '\nFound no changes after unstaging gitlinks, aborting commit.')
            print(disable_msg)
            exit(1)

    if disable_msg:
        print(disable_msg)


if __name__ == "__main__":
    main()
