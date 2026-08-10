#!/usr/bin/env python3
# Copyright 2014 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import os
import sys

import gclient_utils
import git_common


def reparent_commit(commit, new_parent):
    """Creates a new commit pointing to `new_parent` while replicating the tree,
    author metadata, and commit message of `commit`.

    Author name, email, and date are preserved via GIT_AUTHOR_* environment
    variables, while committer metadata defaults to the current user and timestamp
    (standard Git rebase semantics).

    Note: Assumes a linear commit history (single parent).

    Args:
        commit: The commit SHA whose tree, author, and message will be replicated.
        new_parent: The commit SHA to set as the new parent.

    Returns:
        The SHA of the newly created commit object.
    """
    assert commit
    assert new_parent

    raw = git_common.run(
        "log",
        "-1",
        "--format=%T%x00%an%x00%ae%x00%aI%x00%B",
        commit,
        autostrip=False,
    )
    tree_sha, author_name, author_email, author_date, message = raw.split(
        "\0", 4
    )
    message = message.rstrip("\r\n") + "\n"
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_AUTHOR_DATE": author_date,
    }
    return git_common.run(
        "commit-tree",
        tree_sha,
        "-p",
        new_parent,
        indata=message.encode("utf-8"),
        env=env,
    )


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
        parent = reparent_commit(commit, parent)
    return parent


def reparent_subtree(
    branch, tree, downstream_branches, initial_hashes, new_shas
):
    """Recursively reparents `branch` and all of its downstream branches.

    Returns:
        List of (branch, new_sha, old_sha) tuples.
    """
    upstream_branch = tree[branch]
    assert upstream_branch in initial_hashes
    assert upstream_branch in new_shas

    old_upstream = initial_hashes[upstream_branch]
    new_upstream = new_shas[upstream_branch]

    new_branch_sha = reparent_branch(branch, old_upstream, new_upstream)
    new_shas[branch] = new_branch_sha
    ref_updates = [(branch, new_branch_sha, initial_hashes[branch])]

    for downstream_branch in downstream_branches[branch]:
        ref_updates.extend(
            reparent_subtree(
                downstream_branch,
                tree,
                downstream_branches,
                initial_hashes,
                new_shas,
            )
        )
    return ref_updates


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


def main(args=None):
    if gclient_utils.IsEnvCog():
        print(
            "squash-branch command is not supported in non-git environment.",
            file=sys.stderr,
        )
        return 1
    if git_common.in_rebase():
        print(
            "Cannot squash while a rebase is in progress. Please finish or abort the rebase first.",
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
    if not target_branch or target_branch == "HEAD":
        print("Cannot squash in detached HEAD.", file=sys.stderr)
        return 1

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
    if not merge_base:
        print(
            f"Unable to determine merge base for `{target_branch}` on `{upstream_branch}`.",
            file=sys.stderr,
        )
        return 1

    # Phase 1: Construct squashed commit and reparent downstream branches in the
    # Git object store without mutating branch references.
    parent_sha = git_common.hash_one(merge_base)
    msg = git_common.get_squash_message(
        target_branch, merge_base, header=opts.message
    )
    new_squash_sha = git_common.create_squash_commit(
        target_branch, parent_sha, msg
    )
    if new_squash_sha is None:
        print("Nothing to commit; squashed branch is empty")
    target_sha = new_squash_sha or parent_sha
    new_shas = {target_branch: target_sha}
    ref_updates = [(target_branch, target_sha, initial_hashes[target_branch])]

    for branch in downstream_branches[target_branch]:
        ref_updates.extend(
            reparent_subtree(
                branch,
                tree,
                downstream_branches,
                initial_hashes,
                new_shas,
            )
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
