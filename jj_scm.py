import contextlib
import logging
import shutil
import gclient_scm
import gclient_utils
import pathlib
import subprocess
import os

def Capture(args):
    return 

# This revset:
# 1) Gets all unsubmitted commits beneath us
# 2) Find the ones whose parents are submitted
# 3) Gets the parents
_SYNC_POINT = 'roots(mutable()::@)-'


class JjWrapper(gclient_scm.GitWrapper):
    def __init__(self, jj_root: pathlib.Path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.repo = pathlib.Path(jj_root, self.relpath)
        self.workspace = pathlib.Path(self._root_dir, self.relpath)
        self.is_workspace = self.workspace != self.repo
        self.workspace_name = pathlib.Path(self._root_dir).name
    
    def jj(self, args: list[str], repo=None, snapshot=False, stderr = None):
        repo = self.workspace if repo is None else repo
        snapshot_args = [] if snapshot else ['--ignore-working-copy']
        args = ['jj', *snapshot_args, *args]
        logging.debug('Running jj command in repo %s: %r', repo, args)
        ps = subprocess.run(args, stdout=subprocess.PIPE, check=False, cwd=repo, stderr = stderr)
        stdout = ps.stdout.decode("utf-8").strip()
        if ps.returncode != 0:
            lines = [
                f'jj command failed in repo {repo}:',
                ' '.join(args),
            ]
            if stdout:
                lines.append('stdout:')
                lines.append(stdout)
            if ps.stderr:
                lines.append('stderr:')
                lines.append(ps.stderr.decode('utf-8').strip())
            raise gclient_utils.Error('\n'.join(lines))
        return stdout
    
    def git_to_jj(self, repo=None):
        repo = repo or self.workspace
        self.Print(f'Converting {repo} to a jj repo')
        # This prints some stuff to stderr that the user doesn't need to see.
        self.jj(['git', 'init', '--colocate'], repo=repo, snapshot = True, stderr=subprocess.PIPE)
    
    def add_workspace(self):
        self.Print(f'Initializing jj workspace {self.workspace}')
        # This prints some stuff to stderr that the user doesn't need to see.
        self.jj(['workspace', 'add', '--name', self.workspace_name, str(self.workspace)], repo = self.repo, snapshot=True, stderr=subprocess.PIPE)

    def commit_ids(self, revset: str, **kwargs) -> list[str]:
        return self.jj(
            ['log', '-r', revset, '-T', 'commit_id ++ "\n"', '--no-graph'],
            **kwargs,
        ).strip().splitlines()
    
    def commit_id(self, revset: str, **kwargs) -> str:
        commits = self.commit_ids(revset, **kwargs)
        if len(commits) != 1:
            raise gclient_utils.Error(f"Expected exactly one commit for revset {revset}, got: {commits}")
        return commits[0]

    def revinfo(self, _options, _args, _file_list):
        """Returns the commit we are synced to."""
        return self.commit_id(_SYNC_POINT)

    def update(self, options, args, file_list):
        # TODO: Currently, we don't handle the `-D` flag.
        # Handling the -D flag is nontrivial with workspaces.
        # I suspect we'll want to:
        # * If it's a workspace, delete it (and `jj workspace forget in the repo`)
        # * If it's a repo not referenced by any workspaces, delete it
        # * If it's a repo referenced by other workspaces, run `jj new root()`.
        _, revision = gclient_utils.SplitUrlRevision(self.url)
        if options.revision:
            # Override the revision number.
            revision = str(options.revision)
        if revision == 'unmanaged':
            return

        if not (self.workspace / '.jj/repo').exists():
            # Case 1: It's a git repo, just not a jj one.
            if (self.workspace / '.git').exists():
                self.git_to_jj()
            # Case 2: It's a workspace that doesn't exist.
            elif self.is_workspace:
                # This *shouldn't* exist.
                shutil.rmtree(self.workspace, ignore_errors=True)
                repo_is_jj = (self.repo / '.jj/repo').exists()
                repo_is_git = (self.repo / '.git').exists()
                if repo_is_git and not repo_is_jj:
                    self.git_to_jj(repo = self.repo)
                if repo_is_jj or repo_is_git:
                    # It should now be a jj repo
                    self.add_workspace()
                else:
                    # The underlying repo doesn't exist.
                    # We make sure this is a workspace pointing to the real repo.
                    super().update(options, args, file_list)
                    self.workspace.rename(self.repo)
                    self.add_workspace()
                    # However, the underlying repo needs this directory to be empty.
                    self.jj(['new', 'root()'], repo = self.repo)
            # Case 3: It's a real repo but isn't on disk yet. Run "git clone"
            else:
                super().update(options, args, file_list)
                self.git_to_jj()
        
        if not options.reset and revision == self.commit_id(_SYNC_POINT):
            # Already synced :)
            return

        if file_list is not None:
            initial_rev = self.commit_id('@', snapshot=True)
            snapshot = False # Already done it once
        else:
            initial_rev = None
            snapshot = True
        
        # If the target commit already exists, we can skip the fetch.
        if not self.commit_ids(f'present({revision})'):
            self._Fetch(options, refspec=revision)

        if options.reset:
            # Never actually perform resets. Resets are scary because they
            # lose data. New is more akin to a stash. Much safer :)
            self.jj(['new', revision], snapshot=snapshot)
        else:
            # Most of the time this is what you want. It just rebases any
            # local changes onto the latest revision.
            # If there is a merge conflict, the command will succeed but
            # leave merge conflicts behind.
            stdout = self.jj(['rebase', '-b', 'mutable()::@', '-d', revision], snapshot=snapshot, stderr = subprocess.STDOUT)
            # Stderr prints out the revisions that were rebased.
            # When we're rebasing potentially hundreds of times, we only
            # want to see the results for merge conflicts.
            if 'conflict' in stdout:
                self.Print(f'While rebasing {self.workspace}, found a merge conflict:\n{stdout}', )

        if file_list is not None:
            # List all the files that were have been chnaged by the rebase/new.
            files = self.jj('diff', '--name-only', '--from', initial_rev, '--to', '@').strip().splitlines()
            file_list.extend([os.path.join(self.checkout_path, f) for f in files])
        return

    def _GetSubmodulePaths(self):
        for line in open(os.path.join(self.checkout_path,".gitmodules")):
            line = line.strip()
            if not line.startswith("path = "):
                continue
            path = line[len("path = ")]
            # Not every submodule will exist, because many are conditional.
            if os.path.isdir(path):
                yield path

    def GetSubmoduleDiff(self):
        # Since jj ignores submodules, we just pretend that we've always
        # committed submodule changes. That means the "git index" always
        # has the current HEAD in the submodule and we never have a diff.
        return {}

    def GetSubmoduleStateFromIndex(self):
        # Since jj ignores submodules, we just pretend that we've always
        # committed submodule changes. That means the "git index" always
        # has the current HEAD in the submodule and we never have a diff.
        state = {}
        for submodule_path in self._GetSubmodulePaths():
            repo = pathlib.Path(self.checkout_path, submodule_path)
            workspace = repo
            if self.repo is not None:
                # This is a jj workspace, not a jj repo
                workspace = os.path.join(self.repo, self.checkout_path)
                # It can't be a jj repo or git repo.
                assert not os.path.isdir(os.path.join(workspace, '.jj/repo')) and not os.path.exists(os.path.join(workspace, '.git'))

                if not os.path.isfile(os.path.join(workspace, '.jj/repo')):
                    # This directory shouldn't exist, but just to be safe.
                    shutil.rmtree(workspace, ignore_errors=True)
                    if repo.is_dir():
                        self.Print(f'Converting submodule {submodule_path} to a jj workspace')
                        self.jj(['workspace', 'add', workspace], repo=repo)
            if (workspace / '.jj').exists():
                state[submodule_path] = self.commit_id(_SYNC_POINT, repo=workspace)

        return state