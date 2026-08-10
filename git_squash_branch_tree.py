#!/usr/bin/env python3
# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Tool to squash all branches and their downstream branches. Useful to avoid
potential conflicts during a git rebase-update with multiple stacked CLs.
"""

import argparse
import sys

import gclient_utils
import git_common as git


def squash_branch(branch, tree, new_shas, original_shas, ref_updates):
    print(f"Squashing branch {branch}.")
    assert branch in tree
    parent = tree[branch]
    parent_sha = new_shas.get(parent) or git.hash_one(
        git.get_or_create_merge_base(branch, parent)
    )
    msg = git.get_squash_message(branch, parent)
    commit_sha = git.create_squash_commit(
        branch,
        parent_sha,
        msg,
    )
    new_shas[branch] = commit_sha
    ref_updates.append((branch, commit_sha, original_shas[branch]))


# Squashes all branches that are part of the subtree starting at `branch`.
def squash_subtree(
    branch,
    tree,
    new_shas,
    downstream_branches,
    original_shas,
    ref_updates,
):
    # The upstream default never has to be squashed (e.g. origin/main).
    if branch != git.upstream_default():
        squash_branch(
            branch,
            tree,
            new_shas,
            original_shas,
            ref_updates,
        )

    # Recurse on downstream branches, if any.
    for downstream_branch in downstream_branches[branch]:
        squash_subtree(
            downstream_branch,
            tree,
            new_shas,
            downstream_branches,
            original_shas,
            ref_updates,
        )


def main(args=None):
    if gclient_utils.IsEnvCog():
        print(
            "squash-branch-tree command is not supported in non-git environment.",
            file=sys.stderr,
        )
        return 1
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ignore-no-upstream",
        action="store_true",
        help="Allows proceeding if any branch has no upstreams.",
    )
    parser.add_argument(
        "--branch",
        "-b",
        type=str,
        default=git.current_branch(),
        help="The name of the branch whose subtree must be "
        "squashed. Defaults to the current branch.",
    )
    opts = parser.parse_args(args)

    if git.is_dirty_git_tree("squash-branch-tree"):
        return 1

    branches_without_upstream, tree = git.get_branch_tree()

    if not opts.ignore_no_upstream and branches_without_upstream:
        print(
            "Cannot use `git squash-branch-tree` since the following\n"
            "branches don't have an upstream:"
        )
        for branch in branches_without_upstream:
            print(f"  - {branch}")
        print("Use --ignore-no-upstream to ignore this check and proceed.")
        return 1

    if opts.branch != git.upstream_default() and opts.branch not in tree:
        print(
            f"Cannot squash `{opts.branch}` because it has no upstream.",
            file=sys.stderr,
        )
        return 1

    diverged_branches = git.get_diverged_branches(tree)
    if diverged_branches:
        print(
            "Cannot use `git squash-branch-tree` since the following\n"
            "branches have diverged from their upstream and could cause\n"
            "conflicts:"
        )
        for diverged_branch in diverged_branches:
            print(f"  - {diverged_branch}")
        return 1

    downstream_branches = git.get_downstream_branches(tree)
    original_shas = git.get_hashes(tree)
    new_shas = {}
    ref_updates = []
    squash_subtree(
        opts.branch,
        tree,
        new_shas,
        downstream_branches,
        original_shas,
        ref_updates,
    )

    if ref_updates:
        payload = "".join(
            f"update {b if b.startswith('refs/') else f'refs/heads/{b}'}\0{new}\0{old}\0"
            for b, new, old in ref_updates
        )
        git.run("update-ref", "-z", "--stdin", indata=payload.encode("utf-8"))

    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.stderr.write("interrupted\n")
        sys.exit(1)
