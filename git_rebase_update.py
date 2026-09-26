#!/usr/bin/env python3
# Copyright 2014 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Tool to update all branches to have the latest changes from their upstreams.
"""

import argparse
import collections
import logging
import sys
import textwrap
import os

from fnmatch import fnmatch
from pprint import pformat

import gclient_utils
import git_common as git
import setup_color
import from_third_party

colorama = from_third_party.import_module("colorama")

STARTING_BRANCH_KEY = "depot-tools.rebase-update.starting-branch"
STARTING_WORKDIR_KEY = "depot-tools.rebase-update.starting-workdir"
# Branches skipped with --skip-branch for the current run (space separated).
SKIPPED_BRANCHES_KEY = "depot-tools.rebase-update.skipped-branches"
# The branch the current run stopped on because of a conflict.
CONFLICT_BRANCH_KEY = "depot-tools.rebase-update.conflict-branch"

RESET = colorama.Fore.RESET + colorama.Back.RESET + colorama.Style.RESET_ALL
BRIGHT = colorama.Style.BRIGHT


def find_return_branch_workdir():
    """Finds the branch and working directory which we should return to after
    rebase-update completes.

    These values may persist across multiple invocations of rebase-update, if
    rebase-update runs into a conflict mid-way.
    """
    return_branch = git.get_config(STARTING_BRANCH_KEY)
    workdir = git.get_config(STARTING_WORKDIR_KEY)
    if not return_branch:
        workdir = os.getcwd()
        git.set_config(STARTING_WORKDIR_KEY, workdir)
        return_branch = git.current_branch()
        if return_branch != "HEAD":
            git.set_config(STARTING_BRANCH_KEY, return_branch)

    return return_branch, workdir


def get_skipped_branches():
    """Returns the branches skipped with --skip-branch for the current run."""
    return (git.get_config(SKIPPED_BRANCHES_KEY) or "").split()


def add_skipped_branch(branch):
    """Skips `branch` for the rest of the current run."""
    branches = get_skipped_branches()
    if branch not in branches:
        branches.append(branch)
    git.set_config(SKIPPED_BRANCHES_KEY, " ".join(branches))


def clear_run_state():
    """Forgets everything about the current run."""
    for key in (
        STARTING_BRANCH_KEY,
        STARTING_WORKDIR_KEY,
        SKIPPED_BRANCHES_KEY,
        CONFLICT_BRANCH_KEY,
    ):
        git.set_config(key, "")


def restore_starting_branch(return_branch, return_workdir):
    """Checks out the branch the run started from, restores the uncommitted
    changes frozen at the start of the run, and returns to the starting
    working directory."""
    # return_branch may not be there any more.
    if return_branch in git.branches(use_limit=False):
        git.run("checkout", return_branch)
        git.thaw()
    else:
        root_branch = git.root()
        if return_branch != "HEAD":
            print(
                "%s was merged with its parent, checking out %s instead."
                % (
                    git.unicode_repr(return_branch),
                    git.unicode_repr(root_branch),
                )
            )
        git.run("checkout", root_branch)

    # return_workdir may also not be there any more.
    if return_workdir:
        try:
            os.chdir(return_workdir)
        except OSError as e:
            print(
                "Unable to return to original workdir %r: %s"
                % (return_workdir, e)
            )


def get_branches_in_other_worktrees():
    """Returns a dict mapping branches checked out in other worktrees to the
    path of the worktree they are checked out in.

    This includes branches being rebased in other worktrees, which `git
    worktree list` reports as detached."""
    output = git.run("worktree", "list", "--porcelain")
    branches = {}
    current_worktree_path = os.path.realpath(
        git.run("rev-parse", "--show-toplevel").strip()
    )

    worktree_path = None
    for line in output.splitlines():
        if line.startswith("worktree "):
            worktree_path = line[len("worktree ") :]
            continue
        if (
            not worktree_path
            or os.path.realpath(worktree_path) == current_worktree_path
        ):
            continue
        if line.startswith("branch refs/heads/"):
            branches[line[len("branch refs/heads/") :]] = worktree_path
        elif line == "detached" and os.path.isdir(worktree_path):
            worktree_branch = git.rebasing_branch(cwd=worktree_path)
            if worktree_branch:
                branches[worktree_branch] = worktree_path

    return branches


def fetch_remotes(branch_tree):
    """Fetches all remotes which are needed to update |branch_tree|."""
    tag_set = git.tags()
    if any(parent in tag_set for parent in branch_tree.values()):
        # Need to fetch all because we don't know what remote the tag comes from.
        git.run_with_stderr(
            "fetch", "--all", stdout=sys.stdout, stderr=sys.stderr
        )
        return

    remotes = set()
    parents = set(branch_tree.values())
    if parents:
        fetchspec_map = {}
        all_fetchspec_configs = git.get_config_regexp(r"^remote\..*\.fetch$")
        for key, fetchspec in all_fetchspec_configs:
            dest_spec = fetchspec.partition(":")[2]
            remote_name = key[len("remote.") : -len(".fetch")]
            fetchspec_map[dest_spec] = remote_name

        full_refs = git.run(
            "rev-parse", "--symbolic-full-name", "--revs-only", *parents
        ).splitlines()
        for full_ref in full_refs:
            for dest_spec, remote_name in fetchspec_map.items():
                if fnmatch(full_ref, dest_spec):
                    remotes.add(remote_name)
                    break

    git.run_with_stderr(
        "fetch",
        "--multiple",
        *sorted(remotes),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def remove_empty_branches(branch_tree, worktree_branches):
    tag_set = git.tags()
    ensure_root_checkout = git.once(lambda: git.run("checkout", git.root()))

    # Resolve tree SHAs for all branches and parents to check for empty diffs.
    all_refs = list(set(branch_tree.keys()).union(branch_tree.values()))
    tree_hashes = {}
    if all_refs:
        tree_hashes = dict(
            zip(all_refs, git.hash_multi(*[f"{r}:" for r in all_refs]))
        )

    deletions = {}
    reparents = {}
    downstreams = collections.defaultdict(list)
    for branch, parent in git.topo_iter(branch_tree, top_down=False):
        if git.is_dormant(branch):
            continue

        downstreams[parent].append(branch)
        branch_tree_hash = tree_hashes[branch]
        parent_tree_hash = tree_hashes[parent]

        # If branch and parent have the same tree, then branch has to be marked
        # for deletion and its children and grand-children reparented to parent.
        if branch_tree_hash == parent_tree_hash:
            if branch in worktree_branches:
                print(
                    "Skipping deletion of branch checked out in another worktree",
                    format_branch_name(branch),
                )
                continue

            ensure_root_checkout()

            logging.debug("branch %s merged to %s", branch, parent)

            # Mark branch for deletion while remembering the ordering, then add
            # all its children as grand-children of its parent and record
            # reparenting information if necessary.
            deletions[branch] = len(deletions)

            for down in downstreams[branch]:
                if down in deletions:
                    continue

                # Record the new and old parent for down, or update such a
                # record if it already exists. Keep track of the ordering so
                # that reparenting happen in topological order.
                downstreams[parent].append(down)
                if down not in reparents:
                    reparents[down] = (len(reparents), parent, branch)
                else:
                    order, _, old_parent = reparents[down]
                    reparents[down] = (order, parent, old_parent)

    # Apply all reparenting recorded, in order.
    for branch, value in sorted(reparents.items(), key=lambda x: x[1][0]):
        _, parent, old_parent = value
        if parent in tag_set:
            git.set_branch_config(branch, "remote", ".")
            git.set_branch_config(branch, "merge", "refs/tags/%s" % parent)
            print(
                "Reparented %s to track %s [tag] (was tracking %s)"
                % (branch, parent, old_parent)
            )
        else:
            git.run("branch", "--set-upstream-to", parent, branch)
            print(
                "Reparented %s to track %s (was tracking %s)"
                % (branch, parent, old_parent)
            )

    # Apply all deletions recorded, in order.
    if deletions:
        branches_to_delete = [
            branch
            for branch, _ in sorted(deletions.items(), key=lambda x: x[1])
        ]
        print(git.run("branch", "-d", *branches_to_delete))


def format_branch_name(branch):
    return BRIGHT + branch + RESET


def rebase_branch_in_worktree(branch, parent, start_hash, worktree_path):
    """Rebases `branch` by running `git rebase` inside `worktree_path`, the
    worktree where `branch` is checked out.

    The worktree is left alone if it is missing, has uncommitted changes, or
    has a rebase in progress. If the rebase fails (e.g. conflicts), it is
    aborted, which restores the worktree to its original commit (the files
    touched by the rebase get rewritten, so their mtimes change).

    Returns:
        None if the branch was rebased, or the reason it wasn't.
    """
    if not os.path.isdir(worktree_path):
        return "worktree %s is missing (see `git worktree prune`)" % (
            worktree_path
        )
    if git.in_rebase(cwd=worktree_path):
        return "worktree %s has a rebase in progress" % worktree_path
    if git.is_worktree_dirty(worktree_path):
        return "worktree %s has uncommitted changes" % worktree_path
    if not git.rebase(
        parent, start_hash, branch, abort=True, cwd=worktree_path
    ).success:
        return (
            "rebase in worktree %s failed (e.g. conflicts) and was aborted"
            % worktree_path
        )
    return None


def rebase_branch(
    branch,
    parent,
    start_hash,
    no_squash,
    branches_in_other_worktrees,
    unrebased_worktree_branches,
):
    """Rebases `branch` onto `parent` from `start_hash`.

    Attempts fast in-memory rebasing via `git replay` for background branches
    to avoid disk I/O and checkout churn. Falls back to standard porcelain
    `git rebase` (and optional commit squashing) when conflicts occur, or when
    operating on active/worktree branches or repositories with GPG signing enabled.

    Branches checked out in other worktrees are rebased inside their worktree,
    or left alone (and appended to `unrebased_worktree_branches`) if that isn't
    safe or fails.

    Args:
        branch: The local branch name to rebase.
        parent: The upstream parent ref or branch name.
        start_hash: The merge-base commit SHA between branch and parent.
        no_squash: If True, disables automated commit squashing on rebase failure.
        branches_in_other_worktrees: Dict mapping branch names checked out in
            other worktrees to their worktree path.
        unrebased_worktree_branches: List to which (branch, reason) pairs are
            appended for worktree branches that were not rebased.

    Returns:
        True if the branch rebased successfully (or is a worktree branch that
        was left alone), False on failure.
    """
    logging.debug(
        "considering %s(%s) -> %s(%s) : %s",
        branch,
        git.hash_one(branch),
        parent,
        git.hash_one(parent),
        start_hash,
    )

    # If parent has FROZEN commits, don't base branch on top of them. Instead,
    # base branch on top of whatever commit is before them.
    back_ups = 0
    orig_parent = parent
    while git.run("log", "-n1", "--format=%s", parent, "--").startswith(
        git.FREEZE
    ):
        back_ups += 1
        parent = git.run("rev-parse", parent + "~")

    if back_ups:
        logging.debug(
            "Backed parent up by %d from %s to %s",
            back_ups,
            orig_parent,
            parent,
        )

    if git.hash_one(parent) != start_hash:
        worktree_path = branches_in_other_worktrees.get(branch)
        # Try a plain rebase first
        if worktree_path:
            print(
                "Rebasing: %s (in worktree %s)"
                % (format_branch_name(branch), worktree_path)
            )
        else:
            print("Rebasing:", format_branch_name(branch))

        # Only attempt in-memory fast-forward/replay for background branches
        # (not the currently checked-out branch, nor branches in other worktrees).
        # We also bypass git replay when commit.gpgsign is enabled because
        # git replay does not sign commits and would produce unsigned commits.
        # Note: git replay is experimental in upstream Git; any failure or conflict
        # gracefully falls back to standard porcelain git.rebase below.
        if (
            branch != git.current_branch()
            and branch not in branches_in_other_worktrees
            and not git.get_gpg_sign_args()
        ):
            old_sha = git.hash_one(branch)
            if old_sha == start_hash:
                new_sha = git.hash_one(parent)
            else:
                new_sha = git.replay_rebase(parent, start_hash, branch)

            if new_sha:
                git.update_refs_atomic(
                    [(branch, new_sha, old_sha)], "rebase-update"
                )
                git.remove_merge_base(branch)
                git.get_or_create_merge_base(branch, orig_parent)
                return True

        # A branch checked out in another worktree can't be rebased from here.
        # Rebase it inside its own worktree instead. If that isn't safe, or if
        # the rebase fails (e.g. conflicts), leave the branch alone and report
        # it at the end of the run.
        if worktree_path:
            reason = rebase_branch_in_worktree(
                branch, parent, start_hash, worktree_path
            )
            if reason:
                print("Skipping %s: %s" % (format_branch_name(branch), reason))
                unrebased_worktree_branches.append((branch, reason))
                return True
            git.remove_merge_base(branch)
            git.get_or_create_merge_base(branch, orig_parent)
            return True

        consider_squashing = git.get_num_commits(branch) != 1 and not (
            no_squash
        )
        rebase_ret = git.rebase(
            parent, start_hash, branch, abort=consider_squashing
        )
        if not rebase_ret.success:
            mid_rebase_message = textwrap.dedent(
                """\
                Your working copy is in mid-rebase. Either:
                 * completely resolve like a normal git-rebase, then run
                       git rebase-update --continue
                   to resume; OR
                 * skip this branch for this run only and keep going:
                       git rebase-update --skip-branch
                 * abort the rebase and mark this branch as dormant (skipped on
                   every run):
                       git rebase --abort && \\
                       git config branch.%s.dormant true
                   and then run `git rebase-update --continue` to resume.
                 * give up on the whole run:
                       git rebase-update --abort
                """
                % branch
            )
            if not consider_squashing:
                print(mid_rebase_message)
                return False
            print(
                "Failed! Attempting to squash",
                format_branch_name(branch),
                "...",
                end=" ",
            )
            sys.stdout.flush()
            squash_branch = branch + "_squash_attempt"
            git.run("checkout", "-b", squash_branch)
            git.squash_current_branch(merge_base=start_hash)

            # Try to rebase the branch_squash_attempt branch to see if it's
            # empty.
            squash_ret = git.rebase(
                parent, start_hash, squash_branch, abort=True
            )
            empty_rebase = git.hash_one(squash_branch) == git.hash_one(parent)
            git.run("checkout", branch)
            git.run("branch", "-D", squash_branch)
            if squash_ret.success and empty_rebase:
                print("Success!")
                git.squash_current_branch(merge_base=start_hash)
                git.rebase(parent, start_hash, branch)
            else:
                print("Failed!")
                print()

                # rebase and leave in mid-rebase state.
                # This second rebase attempt should always fail in the same
                # way that the first one does.  If it magically succeeds then
                # something very strange has happened.
                second_rebase_ret = git.rebase(parent, start_hash, branch)
                if second_rebase_ret.success:  # pragma: no cover
                    print("Second rebase succeeded unexpectedly!")
                    print("Please see: http://crbug.com/425696")
                    print("First rebased failed with:")
                    print(rebase_ret.stderr)
                else:
                    print("Here's what git-rebase (squashed) had to say:")
                    print()
                    print(squash_ret.stdout)
                    print(squash_ret.stderr)
                    print(
                        textwrap.dedent("""\
          Squashing failed. You probably have a real merge conflict.
          """)
                    )
                    print(mid_rebase_message)
                    return False
    else:
        print("%s up-to-date" % format_branch_name(branch))

    git.remove_merge_base(branch)
    git.get_or_create_merge_base(branch, orig_parent)

    return True


def with_downstream_branches(base_branches, branch_tree):
    """Returns a set of base_branches and all downstream branches."""
    downstream_branches = set()
    for branch, parent in git.topo_iter(branch_tree):
        if parent in base_branches or parent in downstream_branches:
            downstream_branches.add(branch)

    return downstream_branches.union(base_branches)


def main(args=None):
    if gclient_utils.IsEnvCog():
        print(
            "rebase-update command is not supported. Please navigate to source "
            "control view in the activity bar to rebase your changes.",
            file=sys.stderr,
        )
        return 1
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--keep-going",
        "-k",
        action="store_true",
        help="Keep processing past failed rebases.",
    )
    parser.add_argument(
        "--no_fetch",
        "--no-fetch",
        "-n",
        action="store_true",
        help="Skip fetching remotes.",
    )
    parser.add_argument(
        "--current", action="store_true", help="Only rebase the current branch."
    )
    parser.add_argument(
        "--tree",
        action="store_true",
        help="Rebase all branches downstream from the selected branch(es).",
    )
    parser.add_argument(
        "branches",
        nargs="*",
        help="Branches to be rebased. All branches are assumed "
        "if none specified.",
    )
    parser.add_argument(
        "--keep-empty",
        "-e",
        action="store_true",
        help="Do not automatically delete empty branches.",
    )
    parser.add_argument(
        "--no-squash",
        action="store_true",
        help="Will not try to squash branches if rebasing fails.",
    )
    parser.add_argument(
        "--skip-worktrees",
        action="store_true",
        help="Skip branches checked out in a different worktree. By default, "
        "such branches are rebased inside their worktree, unless it has "
        "uncommitted changes. If that rebase fails, it is aborted and the "
        "branch is reported at the end of the run.",
    )
    run_control = parser.add_mutually_exclusive_group()
    run_control.add_argument(
        "--continue",
        dest="continue_run",
        action="store_true",
        help="Continue a run that stopped on a conflict, after the conflict "
        "was resolved. Implies --no-fetch.",
    )
    run_control.add_argument(
        "--skip-branch",
        action="store_true",
        help="Skip the branch the run stopped on for the rest of this run, "
        "and continue. Implies --no-fetch.",
    )
    run_control.add_argument(
        "--abort",
        action="store_true",
        help="Give up on a run that stopped on a conflict: abort the rebase "
        "and return to the starting branch.",
    )

    opts = parser.parse_args(args)

    if opts.verbose:  # pragma: no cover
        logging.getLogger().setLevel(logging.DEBUG)

    # TODO(iannucci): snapshot all branches somehow, so we can implement
    #                 `git rebase-update --undo`.
    #   * Perhaps just copy packed-refs + refs/ + logs/ to the side?
    #     * commit them to a secret ref?
    #       * Then we could view a summary of each run as a
    #         `diff --stat` on that secret ref.

    resuming = opts.continue_run or opts.skip_branch or opts.abort
    if resuming:
        if not git.get_config(STARTING_WORKDIR_KEY):
            print("No git rebase-update in progress.")
            return 1
        opts.no_fetch = True

    if opts.abort:
        if git.in_rebase():
            git.run_with_retcode("rebase", "--abort")
        return_branch, return_workdir = find_return_branch_workdir()
        os.chdir(git.run("rev-parse", "--show-toplevel"))
        restore_starting_branch(return_branch, return_workdir)
        clear_run_state()
        return 0

    if opts.skip_branch:
        conflict_branch = git.get_config(CONFLICT_BRANCH_KEY)
        if not conflict_branch:
            print("git rebase-update is not stopped on a conflict.")
            return 1
        if git.in_rebase():
            git.run_with_retcode("rebase", "--abort")
        add_skipped_branch(conflict_branch)

    if git.in_rebase():
        print(
            textwrap.dedent("""\
            Rebase in progress. Finish it with `git rebase --continue`, then
            run `git rebase-update --continue`. Or run
            `git rebase-update --skip-branch` to skip this branch for this run
            only and keep going, or `git rebase-update --abort`.""")
        )
        return 1

    if resuming:
        git.set_config(CONFLICT_BRANCH_KEY, "")
    else:
        # A new run forgets the branches skipped by any previous run.
        git.set_config(SKIPPED_BRANCHES_KEY, "")
        git.set_config(CONFLICT_BRANCH_KEY, "")

    return_branch, return_workdir = find_return_branch_workdir()
    os.chdir(git.run("rev-parse", "--show-toplevel"))

    if git.current_branch() == "HEAD":
        if git.run("status", "--porcelain", "--ignore-submodules=all"):
            print(
                "Cannot rebase-update with detached head + uncommitted changes."
            )
            return 1
    else:
        git.freeze()  # just in case there are any local changes.

    branches_to_rebase = set(opts.branches)
    if opts.current:
        branches_to_rebase.add(git.current_branch())

    skipped, branch_tree = git.get_branch_tree(use_limit=not opts.current)
    if opts.tree:
        branches_to_rebase = with_downstream_branches(
            branches_to_rebase, branch_tree
        )

    if branches_to_rebase:
        skipped = set(skipped).intersection(branches_to_rebase)
    for branch in skipped:
        print("Skipping %s: No upstream specified" % format_branch_name(branch))

    if not opts.no_fetch:
        fetch_remotes(branch_tree)

    merge_base = {}
    for branch, parent in branch_tree.items():
        merge_base[branch] = git.get_or_create_merge_base(branch, parent)

    logging.debug("branch_tree: %s" % pformat(branch_tree))
    logging.debug("merge_base: %s" % pformat(merge_base))

    retcode = 0
    unrebased_branches = []
    skipped_branches = get_skipped_branches()
    unrebased_worktree_branches = []
    branches_in_other_worktrees = get_branches_in_other_worktrees()
    worktree_branches = (
        branches_in_other_worktrees if opts.skip_worktrees else {}
    )
    # Rebase each branch starting with the root-most branches and working
    # towards the leaves.
    for branch, parent in git.topo_iter(branch_tree):
        # Only rebase specified branches, unless none specified.
        if branches_to_rebase and branch not in branches_to_rebase:
            continue
        if branch in worktree_branches:
            print(
                "Skipping branch checked out in another worktree",
                format_branch_name(branch),
            )
            continue
        if branch in skipped_branches:
            print("Skipping %s for this run" % format_branch_name(branch))
            continue
        if git.is_dormant(branch):
            print("Skipping dormant branch", format_branch_name(branch))
        else:
            ret = rebase_branch(
                branch,
                parent,
                merge_base[branch],
                opts.no_squash,
                branches_in_other_worktrees,
                unrebased_worktree_branches,
            )
            if not ret:
                retcode = 1

                if opts.keep_going:
                    print("--keep-going set, continuing with next branch.")
                    unrebased_branches.append(branch)
                    if git.in_rebase():
                        git.run_with_retcode("rebase", "--abort")
                    if git.in_rebase():  # pragma: no cover
                        print(
                            "Failed to abort rebase. Something is really wrong."
                        )
                        break
                else:
                    # Remember where we stopped for --skip-branch.
                    git.set_config(CONFLICT_BRANCH_KEY, branch)
                    break

    if unrebased_branches:
        print()
        print("The following branches could not be cleanly rebased:")
        for branch in unrebased_branches:
            print("  %s" % format_branch_name(branch))

    if unrebased_worktree_branches:
        print()
        print(
            "The following branches checked out in other worktrees were not "
            "rebased:"
        )
        for branch, reason in unrebased_worktree_branches:
            print("  %s: %s" % (format_branch_name(branch), reason))

    if not retcode:
        if not opts.keep_empty:
            # Branches checked out in another worktree can never be deleted.
            remove_empty_branches(branch_tree, branches_in_other_worktrees)

    # Restore return_branch and thaw uncommitted changes if everything succeeded
    # OR if --keep-going was used (since --keep-going aborts mid-rebase conflicts,
    # ensuring the working tree is clean and ready to return to starting state).
    if not retcode or (opts.keep_going and not git.in_rebase()):
        if skipped_branches:
            print()
            print("The following branches were skipped for this run:")
            for branch in skipped_branches:
                print("  %s" % format_branch_name(branch))

        restore_starting_branch(return_branch, return_workdir)
        clear_run_state()

        print()
        print("Running `git gc --auto` - Ctrl-C to abort is OK.")
        git.run("gc", "--auto")

    return retcode


if __name__ == "__main__":  # pragma: no cover
    setup_color.init()
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("interrupted\n")
        sys.exit(1)
