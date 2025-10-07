import gclient_scm
import gclient_utils
import subprocess
import os

def Capture(*args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8").strip()

class JjWrapper(gclient_scm.SCMWrapper):
    def update(self, options, args, file_list):
        # TODO? Seems like no-opt is okay here since it's only for Chromium
        # raise gclient_utils.Error("Unsupported")
        return

    def revinfo(self, _options, _args, _file_list):
        return Capture("jj","log","-r","@","-T", "commit_id", "--no-graph")

    def _GetSubmodulePaths(self):
        paths = []
        for line in open(os.path.join(self.checkout_path,".gitmodules")):
            line = line.strip()
            if not line.startswith("path = "):
                continue
            path = line[len("path = ")]
            if not os.path.exists(path):
                continue
            paths.append(path)
        return paths            

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
        cwd = os.getcwd()
        for submodule_path in self._GetSubmodulePaths():
            os.chdir(submodule_path)
            head = Capture("git", "rev-parse", "HEAD")
            os.chdir(cwd)
            state[submodule_path] = head

        return state
