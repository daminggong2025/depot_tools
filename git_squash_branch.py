#!/usr/bin/env python3
# Copyright 2014 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import sys

import gclient_utils
import git_common


def reparent_branch(branch, old_upstream_sha, new_upstream_sha):
    """Re-parents all commits on `branch` (from `old_upstream_sha`) onto `new_upstream_sha`.

    Reusing existing tree SHAs is valid because squashing the upstream branch
    retains the exact tree snapshot of its original tip commit.

    Args:
        branch: The branch whose commits are being reparented.
        old_upstream_sha: The original tip commit SHA of the upstream branch.
        new_upstream_sha: The new squashed commit SHA of the upstream branch.

    Returns:
        The new tip commit SHA for `branch`.
    """
    assert branch and old_upstream_sha and new_upstream_sha
    commits = git_common.run(
        "rev-list", "--reverse", f"{old_upstream_sha}..{branch}"
    ).splitlines()
    parent = new_upstream_sha
    for commit in commits:
        parent = git_common.reparent_commit(commit, parent)
    return parent


def reparent_subtree(
    branch, tree, new_shas, initial_hashes, downstream_branches, ref_updates
):
    """Recursively reparents `branch` and all of its downstream branches."""
    upstream_branch = tree[branch]
    assert upstream_branch in initial_hashes
    assert upstream_branch in new_shas

    old_upstream = initial_hashes[upstream_branch]
    new_upstream = new_shas[upstream_branch]

    new_branch_sha = reparent_branch(branch, old_upstream, new_upstream)
    new_shas[branch] = new_branch_sha
    ref_updates.append((branch, new_branch_sha, initial_hashes[branch]))

    for downstream_branch in downstream_branches[branch]:
        reparent_subtree(
            downstream_branch,
            tree,
            new_shas,
            initial_hashes,
            downstream_branches,
            ref_updates,
        )


def children_have_diverged(branch, downstream_branches, diverged_branches):
    # If we have no diverged branches, then no children have diverged.
    if not diverged_branches:
        return False

    # If we have diverged, then our children have diverged.
    if branch in diverged_branches:
        return True

    # If any of our children have diverged, then we need to return true.
    for downstream_branch in downstream_branches[branch]:
        if children_have_diverged(
            downstream_branch, downstream_branches, diverged_branches
        ):
            return True

    return False


def main(args):
    if gclient_utils.IsEnvCog():
        print(
            "squash-branch command is not supported in non-git environment.",
            file=sys.stderr,
        )
        return 1
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-m",
        "--message",
        metavar="<msg>",
        default=None,
        help="Use the given <msg> as the first line of the commit message.",
    )
    opts = parser.parse_args(args)
    if git_common.is_dirty_git_tree("squash-branch"):
        return 1

    target_branch = git_common.current_branch()

    # Save the hashes before we mutate the tree so that we have all of the
    # necessary rebasing information.
    _, tree = git_common.get_branch_tree()
    if target_branch not in tree:
        print(
            f"Cannot squash `{target_branch}` because it has no upstream.",
            file=sys.stderr,
        )
        return 1

    initial_hashes = git_common.get_hashes(tree)
    downstream_branches = git_common.get_downstream_branches(tree)
    diverged_branches = git_common.get_diverged_branches(tree)

    if target_branch in diverged_branches:
        print(
            f"Cannot use `git squash-branch` because `{target_branch}` is "
            "behind its upstream. Please rebase first.",
            file=sys.stderr,
        )
        return 1

    for branch in downstream_branches[target_branch]:
        if children_have_diverged(
            branch, downstream_branches, diverged_branches
        ):
            print(
                "Cannot use `git squash-branch` since some children have "
                "diverged from their upstream and could cause conflicts.",
                file=sys.stderr,
            )
            return 1

    upstream_branch = tree[target_branch]
    merge_base = git_common.get_or_create_merge_base(
        target_branch, parent=upstream_branch
    )
    assert merge_base

    # Phase 1: Construct squashed commit and reparent downstream branches in the
    # Git object store without mutating branch references.
    parent_sha = git_common.hash_one(merge_base)
    msg = git_common.get_squash_message(
        target_branch, merge_base, header=opts.message
    )
    new_squash_sha = git_common.create_squash_commit(
        target_branch, parent_sha, msg
    )
    if new_squash_sha == parent_sha:
        print("Nothing to commit; squashed branch is empty")
    new_shas = {target_branch: new_squash_sha}
    ref_updates = [
        (target_branch, new_squash_sha, initial_hashes[target_branch])
    ]

    for branch in downstream_branches[target_branch]:
        reparent_subtree(
            branch,
            tree,
            new_shas,
            initial_hashes,
            downstream_branches,
            ref_updates,
        )

    # Phase 2: Apply all reference updates in a single atomic transaction.
    # If any compare-and-swap check fails, all ref updates roll back.
    git_common.update_refs_atomic(ref_updates)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.stderr.write("interrupted\n")
        sys.exit(1)
