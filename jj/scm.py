# Copyright (c) 2026 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
import pathlib
import subprocess

import gclient_scm


class JjWrapper(gclient_scm.SCMWrapper):
    """JjWrapper handles standalone JJ repos, i.e. those without a .git dir."""
    name = 'jj'

    def _GetSubmodulePaths(self):
        with pathlib.Path(self.checkout_path, '.gitmodules').open('r') as f:
            for line in f:
                line = line.strip()
                if not line.startswith('path = '):
                    continue
                path = pathlib.Path(self.checkout_path, line[len('path = '):])
                # Not every submodule will exist, because many are conditional.
                if (path / '.git').exists() or (path / '.jj').exists():
                    yield path

    def GetSubmoduleStateFromIndex(self):
        # Jj doesn't have an index as such, it just has a working copy.
        # Since jj doesn't yet have full submodule support, we just
        # read the submodules from the .gitmodules file.
        state = {}
        for submodule_path in self._GetSubmodulePaths():
            if (submodule_path / '.git').exists():
                state[str(submodule_path)] = subprocess.run(
                    ['git', 'rev-parse', 'HEAD'],
                    check=True,
                    cwd=submodule_path,
                    stdout=subprocess.PIPE,
                ).stdout.decode('utf-8').strip()
            else:
                assert (submodule_path / '.jj').exists()
                state[str(submodule_path)] = subprocess.run(
                    ['jj', 'log', '-r', '@', '-G', '-T', 'commit_id',
                     '--color=never', '--no-pager'],
                    check=True,
                    cwd=submodule_path,
                    stdout=subprocess.PIPE,
                ).stdout.decode('utf-8').strip()
        return state

    def GetSubmoduleDiff(self):
        # Git has an index and working copy, and calculates the submodule state
        # at the index and a diff since the index.
        # Jj has no index so this is always empty.
        return {}

    # All the rest are no-ops for now.
    def GetCacheMirror(self):
        return None

    def GetActualRemoteURL(self, options):
        return None

    def DoesRemoteURLMatch(self, options):
        del options
        return True

    def revert(self, options, args, file_list):
        pass

    def diff(self, options, args, file_list):
        pass

    def pack(self, options, args, file_list):
        pass

    def revinfo(self, options, args, file_list):
        pass

    def status(self, options, args, file_list):
        pass

    def update(self, options, args, file_list):
        pass
