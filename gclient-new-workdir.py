#!/usr/bin/env python3
# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# Usage:
#    gclient-new-workdir.py [options] <repository> <new_workdir>
#

import argparse
import os
import random
import shutil
import subprocess
import sys
import textwrap

import gclient_utils
import git_common


def parse_options():
    if sys.platform == 'win32':
        print(
            'ERROR: This script cannot run on Windows because it uses symlinks.'
        )
        sys.exit(1)
    if gclient_utils.IsEnvCog():
        print('ERROR: This script cannot run in non-git environment.')
        sys.exit(1)

    parser = argparse.ArgumentParser(description='''Clone an existing '''
    '''gclient workspace, taking care of all sub-repositories.''')
    parser.add_argument('repository',
                        type=os.path.abspath,
                        help='should contain a .gclient file')
    parser.add_argument('new_workdir', help='must not exist')
    parser.add_argument(
        "--use-git-worktree",
        action="store_true",
        default=True,
        help="""Use git worktree instead of linking .git folders.""",
    )
    parser.add_argument(
        "--no-use-git-worktree",
        action="store_false",
        dest="use_git_worktree",
        help="""Force not to use git worktree and link .git folders instead.""",
    )
    parser.add_argument(
        "--reflink",
        action="store_true",
        default=None,
        help="""Force use of a copy-on-write flag when copying for better """
        """performance and disk utilization. This is the default behavior on """
        """supported copy-on-write FS like btrfs, ZFS, or APFS.""",
    )
    parser.add_argument(
        '--no-reflink',
        action='store_false',
        dest='reflink',
        help='''Force not to use a copy-on-write flag when copying even on a '''
        '''supported copy-on-write FS like btrfs, ZFS, or APFS.''')
    parser.add_argument(
        '--link-root-git-repo-only',
        action='store_true',
        default=None,
        help='''Force linking only root repository's .git e.g. chromium/src '''
        '''for better performance. This is the default behavior on supported '''
        '''copy-on-write FS like btrfs, ZFS, or APFS.''')
    parser.add_argument('--link-all-git-sub-repos',
                        action='store_false',
                        dest='link_root_git_repo_only',
                        help='''Force linking .git for all sub-repositories.''')
    args = parser.parse_args()

    if not os.path.exists(args.repository):
        parser.error('Repository "%s" does not exist.' % args.repository)

    gclient = os.path.join(args.repository, '.gclient')
    if not os.path.exists(gclient):
        parser.error('No .gclient file at "%s".' % gclient)

    if os.path.exists(args.new_workdir):
        parser.error('New workdir "%s" already exists.' % args.new_workdir)

    return args


def get_cp_copy_on_write_flag():
    return '-c' if sys.platform == 'darwin' else '--reflink'


def support_copy_on_write(src, dest):
    # Use of a copy-on-write flag always succeeds when 'src' is a symlink or a directory
    assert os.path.isfile(src) and not os.path.islink(src)
    try:
        subprocess.check_output(
            ['cp', '-a', get_cp_copy_on_write_flag(), src, dest],
            stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError:
        return False
    finally:
        if os.path.isfile(dest):
            os.remove(dest)
    return True


def try_btrfs_subvol_snapshot(src, dest):
    try:
        subprocess.check_call(['btrfs', 'subvol', 'snapshot', src, dest],
                              stderr=subprocess.STDOUT)
    except (subprocess.CalledProcessError, OSError):
        return False
    return True


def main():
    args = parse_options()

    gclient = os.path.join(args.repository, '.gclient')
    if os.path.islink(gclient):
        gclient = os.path.realpath(gclient)
    new_gclient = os.path.join(args.new_workdir, '.gclient')

    if try_btrfs_subvol_snapshot(args.repository, args.new_workdir):
        # If btrfs is being used, reflink support is always present, and there's
        # no benefit to not using it.
        args.reflink = True
    else:
        os.makedirs(args.new_workdir)
        if args.reflink is None:
            args.reflink = support_copy_on_write(gclient, new_gclient)
            if args.reflink:
                print('Copy-on-write support is detected.')
        os.symlink(gclient, new_gclient)

    if args.reflink and args.link_root_git_repo_only is None:
        # Since we're doing a btrfs subvolume snapshot or reflink copy, the
        # sub-repositories will already be present in the copy, and we only need
        # to deal with linking the .git directory for the root repository.
        args.link_root_git_repo_only = True

    for root, dirs, _ in os.walk(args.repository):
        if '.git' in dirs:
            workdir = root.replace(args.repository, args.new_workdir, 1)

            if args.reflink:
                if not os.path.exists(workdir):
                    print('Copying: %s' % workdir)
                    subprocess.check_call([
                        'cp', '-a',
                        get_cp_copy_on_write_flag(), root, workdir
                    ])
                shutil.rmtree(os.path.join(workdir, '.git'))

            if args.use_git_worktree:
                if args.reflink:
                    assert os.path.exists(workdir)
                    # Move the existing directory to a temporary directory since
                    # `git worktree add` won't work if the worktree directory
                    # already exists even with the --force flag.
                    tmp_workdir = os.path.join(
                        os.path.dirname(workdir),
                        "tmp-"
                        + os.path.basename(workdir)
                        + "-"
                        + "%08x" % random.getrandbits(32),
                    )
                    os.rename(workdir, tmp_workdir)
                    # Run `git worktree add` with --no-checkout to create the
                    # worktree without touching any files in the worktree.
                    print(
                        "Running: git worktree add %s --no-checkout -d -f"
                        % workdir
                    )
                    subprocess.check_call(
                        [
                            "git",
                            "worktree",
                            "add",
                            workdir,
                            "--no-checkout",
                            "-d",
                            "-f",
                        ],
                        cwd=root,
                    )
                    # Move the .git directory from the worktree to the temporary
                    # directory and move the temporary directory back to the
                    # worktree directory.
                    shutil.move(os.path.join(workdir, ".git"), tmp_workdir)
                    shutil.rmtree(workdir)
                    os.rename(tmp_workdir, workdir)

                    # Copy over the index so that the worktree is aware of the
                    # files in the repository.
                    worktree_git_dir = (
                        subprocess.check_output(
                            ["git", "rev-parse", "--git-dir"], cwd=workdir
                        )
                        .decode()
                        .strip()
                    )
                    subprocess.check_call(
                        [
                            "cp",
                            "-a",
                            get_cp_copy_on_write_flag(),
                            os.path.join(root, ".git", "index"),
                            os.path.join(worktree_git_dir, "index"),
                        ]
                    )
                else:
                    print("Running: git worktree add %s -d -f" % workdir)
                    subprocess.check_call(
                        ["git", "worktree", "add", workdir, "-d", "-f"],
                        cwd=root,
                    )
            else:
                print("Linking: %s/.git" % workdir)
                git_common.make_workdir(
                    os.path.join(root, ".git"), os.path.join(workdir, ".git")
                )
                if args.reflink:
                    subprocess.check_call(
                        [
                            "cp",
                            "-a",
                            get_cp_copy_on_write_flag(),
                            os.path.join(root, ".git", "index"),
                            os.path.join(workdir, ".git", "index"),
                        ]
                    )
                else:
                    print("Running: git checkout -f %s" % workdir)
                    subprocess.check_call(
                        ["git", "checkout", "-f"], cwd=workdir
                    )

            # Break out of the for loop if we're only linking the root git
            # repository's .git folder and using copy-on-write since all the
            # sub-repositories will already be copied and we are done here.
            # Otherwise, we can't avoid visiting all sub-repositories for
            # checking out the files with git.
            if args.reflink and args.link_root_git_repo_only:
                break

    if args.reflink:
        print(
            textwrap.dedent('''\
      The repo was copied using copy-on-write, and the artifacts were retained.
      More details on http://crbug.com/721585.

      Depending on your usage pattern, you might want to do "gn gen"
      on the output directories. More details: http://crbug.com/723856.'''))


if __name__ == '__main__':
    sys.exit(main())
