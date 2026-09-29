#!/usr/bin/env python3
# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import sys
import unittest
from unittest import mock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import presubmit_canned_checks
from testing_support.presubmit_canned_checks_test_mocks import (
    MockInputApi,
    MockOutputApi,
    MockFile,
)


class MockRelativeFile(MockFile):
    def __init__(self, rel_path, new_contents, action="A"):
        # We pass absolute path to super so AbsoluteLocalPath returns absolute path
        abs_path = os.path.join("/tmp/repo", rel_path)
        super(MockRelativeFile, self).__init__(
            abs_path, new_contents, action=action
        )
        self._rel_path = rel_path

    def LocalPath(self):
        return self._rel_path


class CheckForCommitObjectsTest(unittest.TestCase):
    def setUp(self):
        self.input_api = MockInputApi()
        self.output_api = MockOutputApi()
        self.input_api.change.RepositoryRoot = lambda: "/tmp/repo"
        self.input_api.PresubmitLocalPath = lambda: "/tmp/repo"
        self.input_api.change.scm = "git"

        # Patch ParseDeps to avoid reading DEPS file
        self.patcher = mock.patch("presubmit_canned_checks._ParseDeps")
        self.mock_parse_deps = self.patcher.start()
        self.mock_parse_deps.return_value = {"git_dependencies": "DEPS"}

    def tearDown(self):
        self.patcher.stop()

    def testBatchedExecutionSmallCL(self):
        # 2 files, should run batched git ls-tree
        self.input_api.files = [
            MockFile(os.path.join("/tmp/repo", "a.txt"), []),
            MockFile(os.path.join("/tmp/repo", "b.txt"), []),
        ]

        self.input_api.subprocess.check_output = mock.Mock(return_value=b"")

        presubmit_canned_checks.CheckForCommitObjects(
            self.input_api, self.output_api
        )

        # Call 0: git show HEAD:DEPS, Call 1: git ls-tree
        self.assertEqual(2, self.input_api.subprocess.check_output.call_count)
        args = self.input_api.subprocess.check_output.call_args[0][0]
        self.assertIn("ls-tree", args)
        self.assertIn("a.txt", args)
        self.assertIn("b.txt", args)
        self.assertIn("--full-tree", args)
        self.assertNotIn("-r", args)

    def testChunkedExecutionLargeCL(self):
        # 1001 files, should execute batched ls-tree across chunks without -r
        self.input_api.files = [
            MockFile(os.path.join("/tmp/repo", f"f{i}.txt"), [])
            for i in range(1001)
        ]

        self.input_api.subprocess.check_output = mock.Mock(return_value=b"")

        presubmit_canned_checks.CheckForCommitObjects(
            self.input_api, self.output_api
        )

        # Call 0: git show HEAD:DEPS, Calls 1..3: chunked git ls-tree
        calls = self.input_api.subprocess.check_output.call_args_list
        self.assertEqual(4, len(calls))
        all_checked = []
        for call in calls[1:]:
            args = call[0][0]
            self.assertIn("ls-tree", args)
            self.assertIn("--full-tree", args)
            self.assertNotIn("-r", args)
            all_checked.extend(args[args.index("--") + 1 :])

        self.assertEqual(1001, len(all_checked))
        self.assertIn("f0.txt", all_checked)
        self.assertIn("f1000.txt", all_checked)

    def testChunkedExecutionWhenDepsModified(self):
        # DEPS modified, should check affected files without full tree scan
        self.input_api.files = [
            MockRelativeFile("DEPS", []),
            MockRelativeFile("other.txt", []),
        ]

        self.input_api.subprocess.check_output = mock.Mock(return_value=b"")

        presubmit_canned_checks.CheckForCommitObjects(
            self.input_api, self.output_api
        )

        # Call 0: git show HEAD:DEPS, Call 1: git ls-tree
        self.assertEqual(2, self.input_api.subprocess.check_output.call_count)
        args = self.input_api.subprocess.check_output.call_args[0][0]
        self.assertIn("ls-tree", args)
        self.assertIn("--full-tree", args)
        self.assertNotIn("-r", args)
        self.assertIn("DEPS", args)
        self.assertIn("other.txt", args)

    def testBatchedFoundCommit(self):
        # 1 submodule, found a commit object (gitlink)
        self.input_api.files = []

        submodule_file = MockFile(os.path.join("/tmp/repo", "submodule"), [])
        self.input_api.change.AffectedSubmodules = mock.Mock(
            return_value=[submodule_file]
        )

        self.input_api.subprocess.check_output = mock.Mock(
            return_value=b"160000 commit 1234567890abcdef\tsubmodule\0"
        )

        results = presubmit_canned_checks.CheckForCommitObjects(
            self.input_api, self.output_api
        )
        self.assertEqual(len(results), 1)
        self.assertIn("submodule", results[0].message)

    def testBatchedExecutionWithSubmodule(self):
        # 1 file + 1 submodule, should run batched git ls-tree with both
        self.input_api.files = [
            MockFile(os.path.join("/tmp/repo", "a.txt"), []),
        ]

        submodule_file = MockFile(
            os.path.join("/tmp/repo", "third_party/perl"), []
        )
        self.input_api.change.AffectedSubmodules = mock.Mock(
            return_value=[submodule_file]
        )

        self.input_api.subprocess.check_output = mock.Mock(return_value=b"")

        presubmit_canned_checks.CheckForCommitObjects(
            self.input_api, self.output_api
        )

        args = self.input_api.subprocess.check_output.call_args[0][0]
        self.assertIn("ls-tree", args)
        self.assertIn("a.txt", args)
        self.assertIn("third_party/perl", args)
        self.assertIn("--full-tree", args)
        self.assertNotIn("-r", args)

    def testAccidentalCommitObjectDetectedInDepsMode(self):
        # In DEPS mode, an accidental commit object at an undeclared path
        # must be detected and reported as an error.
        self.input_api.files = [
            MockFile(os.path.join("/tmp/repo", "accidental_gitlink"), []),
        ]
        self.input_api.subprocess.check_output = mock.Mock(
            return_value=b"160000 commit feedbeef\taccidental_gitlink\0"
        )

        results = presubmit_canned_checks.CheckForCommitObjects(
            self.input_api, self.output_api
        )
        self.assertEqual(len(results), 1)
        self.assertIn("Commit objects present within tree", results[0].message)
        self.assertIn("accidental_gitlink", results[0].items)

    @mock.patch("configparser.ConfigParser")
    def testAccidentalCommitObjectDetectedInSyncMode(self, mock_config_parser):
        # In SYNC mode, an accidental commit object committed in the CL
        # that is not in DEPS must be caught as a diverged entry.
        self.mock_parse_deps.return_value = {
            "git_dependencies": "SYNC",
            "deps": {
                "third_party/perl": "https://repo.git@1234",
            },
        }
        self.input_api.files = [
            MockFile(os.path.join("/tmp/repo", "accidental_gitlink"), []),
        ]
        mock_instance = mock_config_parser.return_value
        mock_instance.items.return_value = [
            ('submodule "third_party/perl"', {"path": "third_party/perl"}),
        ]

        self.input_api.subprocess.check_output = mock.Mock(
            return_value=(
                b"160000 commit 1234\tthird_party/perl\0"
                b"160000 commit feedbeef\taccidental_gitlink\0"
            )
        )

        results = presubmit_canned_checks.CheckForCommitObjects(
            self.input_api, self.output_api
        )
        self.assertEqual(len(results), 1)
        self.assertIn("accidental_gitlink", results[0].message)

    @mock.patch("configparser.ConfigParser")
    def testSyncModeLargeCLChecksSubmodulesAndFiles(self, mock_config_parser):
        # In SYNC mode on a 1229-file change, git ls-tree queries chunks
        # containing both affected files and declared submodules without -r.
        self.mock_parse_deps.return_value = {
            "git_dependencies": "SYNC",
            "deps": {
                "third_party/perl": "https://repo.git@1234",
            },
        }
        self.input_api.files = [
            MockFile(os.path.join("/tmp/repo", f"f{i}.txt"), [])
            for i in range(1229)
        ]
        mock_instance = mock_config_parser.return_value
        mock_instance.items.return_value = [
            ('submodule "third_party/perl"', {"path": "third_party/perl"}),
        ]

        # Call 0: git show HEAD:DEPS
        # Calls 1..3: 1229 files + 1 submodule = 1230 files (500, 500, 230)
        self.input_api.subprocess.check_output = mock.Mock(
            side_effect=[
                b"",
                b"",
                b"",
                b"160000 commit 1234\tthird_party/perl\0",
            ]
        )

        results = presubmit_canned_checks.CheckForCommitObjects(
            self.input_api, self.output_api
        )
        self.assertEqual(results, [])

        calls = self.input_api.subprocess.check_output.call_args_list
        self.assertEqual(4, len(calls))
        all_checked = []
        for call in calls[1:]:
            args = call[0][0]
            self.assertIn("ls-tree", args)
            self.assertNotIn("-r", args)
            all_checked.extend(args[args.index("--") + 1 :])

    @mock.patch("configparser.ConfigParser")
    def testSyncModeDepsModifiedRemovedSubmoduleDivergenceDetected(
        self, mock_config_parser
    ):
        # When a submodule is removed from DEPS and .gitmodules, but the
        # gitlink remains in the tree, presubmit must catch the orphaned
        # gitlink.
        self.mock_parse_deps.side_effect = [
            {"git_dependencies": "SYNC", "deps": {}},
            {
                "git_dependencies": "SYNC",
                "deps": {"third_party/perl": "https://repo.git@1234"},
            },
        ]
        self.input_api.files = [
            MockRelativeFile("DEPS", []),
            MockRelativeFile(".gitmodules", []),
        ]
        mock_instance = mock_config_parser.return_value
        mock_instance.items.return_value = []

        base_deps_code = (
            "deps = {'src/third_party/perl': 'https://repo.git@1234'}"
        )
        base_gitmodules_text = (
            b'[submodule "third_party/perl"]\n\tpath = third_party/perl'
        )

        def mock_check_output(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "ls-tree" in cmd_str:
                self.assertIn("third_party/perl", cmd)
                return b"160000 commit 1234\tthird_party/perl\0"
            if "git show" in cmd_str:
                if "HEAD:DEPS" in cmd_str:
                    return b""
                if "DEPS" in cmd_str:
                    return base_deps_code.encode("utf-8")
                if ".gitmodules" in cmd_str:
                    return base_gitmodules_text
            return b""

        self.input_api.subprocess.check_output = mock.Mock(
            side_effect=mock_check_output
        )

        results = presubmit_canned_checks.CheckForCommitObjects(
            self.input_api, self.output_api
        )
        self.assertEqual(len(results), 1)
        self.assertIn("third_party/perl", results[0].message)

    @mock.patch("configparser.ConfigParser")
    def testSyncModeDepsWithNoneAndCipdDependencies(self, mock_config_parser):
        # None and non-git (e.g. CIPD) deps must not raise TypeError.
        self.mock_parse_deps.return_value = {
            "git_dependencies": "SYNC",
            "deps": {
                "src/third_party/removed": None,
                "src/third_party/cipd_pkg": {
                    "packages": [{"package": "foo", "version": "1.0"}],
                    "dep_type": "cipd",
                },
                "src/third_party/plain_url": "https://repo.git@1234",
            },
        }
        self.input_api.files = [
            MockRelativeFile("DEPS", []),
        ]
        mock_instance = mock_config_parser.return_value
        mock_instance.items.return_value = [
            (
                'submodule "third_party/plain_url"',
                {"path": "third_party/plain_url"},
            ),
        ]

        def mock_check_output(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "git show" in cmd_str:
                return b""
            if "ls-tree" in cmd_str:
                return b"160000 commit 1234\tthird_party/plain_url\0"
            return b""

        self.input_api.subprocess.check_output = mock.Mock(
            side_effect=mock_check_output
        )

        results = presubmit_canned_checks.CheckForCommitObjects(
            self.input_api, self.output_api
        )
        self.assertEqual(results, [])

    @mock.patch("configparser.ConfigParser")
    def testSyncModeDepsModifiedSubmodulePathMatches(self, mock_config_parser):
        # Modifying a submodule hash in DEPS and updating gitlink in the CL
        # must succeed cleanly with chunked queries and zero -r.
        self.mock_parse_deps.return_value = {
            "git_dependencies": "SYNC",
            "deps": {
                "src/third_party/perl": "https://repo.git@5678",
            },
        }
        self.input_api.files = [
            MockRelativeFile("DEPS", []),
            MockFile(os.path.join("/tmp/repo", "third_party/perl"), []),
        ]
        mock_instance = mock_config_parser.return_value
        mock_instance.items.return_value = [
            ('submodule "third_party/perl"', {"path": "third_party/perl"}),
        ]

        def mock_check_output(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "git show" in cmd_str:
                return b""
            if "ls-tree" in cmd_str:
                self.assertNotIn("-r", cmd)
                return b"160000 commit 5678\tthird_party/perl\0"
            return b""

        self.input_api.subprocess.check_output = mock.Mock(
            side_effect=mock_check_output
        )

        results = presubmit_canned_checks.CheckForCommitObjects(
            self.input_api, self.output_api
        )
        self.assertEqual(results, [])

    def testBatchFilesHelper(self):
        # Test _BatchFiles with various inputs.
        self.assertEqual([], list(presubmit_canned_checks._BatchFiles([])))

        single = list(presubmit_canned_checks._BatchFiles(["a"]))
        self.assertEqual([["a"]], single)

        # Batch by max_files
        items = [f"f{i}" for i in range(12)]
        batches = list(presubmit_canned_checks._BatchFiles(items, max_files=5))
        self.assertEqual(3, len(batches))
        self.assertEqual(5, len(batches[0]))
        self.assertEqual(5, len(batches[1]))
        self.assertEqual(2, len(batches[2]))

        # Batch by max_chars
        batches = list(
            presubmit_canned_checks._BatchFiles(
                ["a" * 50, "b" * 50, "c" * 50], max_chars=105
            )
        )
        self.assertEqual(2, len(batches))
        self.assertEqual(2, len(batches[0]))
        self.assertEqual(1, len(batches[1]))

    def testExtractGitDepPathsNonSrcRepo(self):
        deps = {
            "third_party/zlib": "https://repo.git@1234",
            "v8/third_party/icu": "https://repo.git@5678",
            "src/build": "https://repo.git@9012",
        }
        paths = presubmit_canned_checks._ExtractGitDepPaths(deps, "v8")
        # "third_party/zlib" should be preserved, not truncated to "zlib"
        self.assertIn("third_party/zlib", paths)
        self.assertNotIn("zlib", paths)
        # "v8/third_party/icu" stripped repo_name prefix to "third_party/icu"
        self.assertIn("v8/third_party/icu", paths)
        self.assertIn("third_party/icu", paths)
        # "src/build" stripped "src/" prefix to "build"
        self.assertIn("src/build", paths)
        self.assertIn("build", paths)

    @mock.patch("configparser.ConfigParser")
    def testUpstreamBranchMergeBaseResolution(self, mock_config_parser):
        self.mock_parse_deps.return_value = {
            "git_dependencies": "SYNC",
            "deps": {},
        }
        self.input_api.files = [MockRelativeFile("DEPS", [])]
        self.input_api.change.UpstreamBranch = lambda: "origin/main"

        commands_run = []

        def mock_check_output(cmd, **kwargs):
            commands_run.append(list(cmd))
            cmd_str = " ".join(cmd)
            if "merge-base" in cmd_str:
                return b"abcdef123456\n"
            if "git show" in cmd_str and "abcdef123456:DEPS" in cmd_str:
                return b"deps = {}"
            if "ls-tree" in cmd_str:
                return b""
            return b""

        self.input_api.subprocess.check_output = mock.Mock(
            side_effect=mock_check_output
        )

        results = presubmit_canned_checks.CheckForCommitObjects(
            self.input_api, self.output_api
        )
        self.assertEqual(results, [])

        # Verify merge-base was called with HEAD and origin/main
        mb_calls = [c for c in commands_run if "merge-base" in c]
        self.assertEqual(1, len(mb_calls))
        self.assertIn("origin/main", mb_calls[0])

        # Verify base DEPS was fetched using the merge-base SHA
        show_calls = [
            c
            for c in commands_run
            if "show" in c and "abcdef123456:DEPS" in " ".join(c)
        ]
        self.assertEqual(1, len(show_calls))

    def testEmptyAndDotPathspecFiltering(self):
        # Empty paths or '.' should be filtered out from path extraction.
        deps = {
            "src/": "https://repo.git@1234",
            "repo/": "https://repo.git@5678",
            "": "https://repo.git@9012",
            ".": "https://repo.git@3456",
        }
        paths = presubmit_canned_checks._ExtractGitDepPaths(deps, "repo")
        self.assertNotIn("", paths)
        self.assertNotIn(".", paths)

        import configparser

        config = configparser.ConfigParser(strict=False)
        config.read_string(
            '[submodule "empty"]\n\tpath = \n'
            '[submodule "dot"]\n\tpath = .\n'
            '[submodule "slash"]\n\tpath = /\n'
        )
        sub_paths = presubmit_canned_checks._ExtractSubmodulePathsFromConfig(
            config
        )
        self.assertNotIn("", sub_paths)
        self.assertNotIn(".", sub_paths)

    @mock.patch("configparser.ConfigParser")
    def testDepsNoneDoesNotCrash(self, mock_config_parser):
        # When deps["deps"] is None, CheckForCommitObjects must not raise
        # AttributeError: 'NoneType' object has no attribute 'items'.
        self.mock_parse_deps.return_value = {
            "git_dependencies": "SYNC",
            "deps": None,
        }
        self.input_api.files = [MockRelativeFile("DEPS", [])]
        self.input_api.subprocess.check_output = mock.Mock(return_value=b"")

        results = presubmit_canned_checks.CheckForCommitObjects(
            self.input_api, self.output_api
        )
        self.assertEqual(results, [])

    @mock.patch("configparser.ConfigParser")
    def testUpstreamBranchAsStringAttribute(self, mock_config_parser):
        # UpstreamBranch set as a string attribute (not callable) should work.
        self.mock_parse_deps.return_value = {
            "git_dependencies": "SYNC",
            "deps": {},
        }
        self.input_api.files = [MockRelativeFile("DEPS", [])]
        self.input_api.change.UpstreamBranch = "origin/main"

        commands_run = []

        def mock_check_output(cmd, **kwargs):
            commands_run.append(list(cmd))
            cmd_str = " ".join(cmd)
            if "merge-base" in cmd_str:
                return b"abcdef123456\n"
            if "git show" in cmd_str:
                return b"deps = {}"
            return b""

        self.input_api.subprocess.check_output = mock.Mock(
            side_effect=mock_check_output
        )

        results = presubmit_canned_checks.CheckForCommitObjects(
            self.input_api, self.output_api
        )
        self.assertEqual(results, [])
        mb_calls = [c for c in commands_run if "merge-base" in c]
        self.assertEqual(1, len(mb_calls))
        self.assertIn("origin/main", mb_calls[0])

    @mock.patch("configparser.ConfigParser")
    def testEmptyGitmodulesDoesNotTriggerHeadFallback(self, mock_config_parser):
        # When .gitmodules exists on disk but has 0 submodules (e.g. after
        # removing the last submodule), config.read() succeeds and returns the file.
        # CheckForCommitObjects must NOT fall back to HEAD:.gitmodules.
        self.mock_parse_deps.return_value = {
            "git_dependencies": "SYNC",
            "deps": {},
        }
        self.input_api.files = [MockRelativeFile(".gitmodules", [])]
        mock_instance = mock_config_parser.return_value
        mock_instance.read.return_value = ["/tmp/repo/.gitmodules"]
        mock_instance.items.return_value = []

        commands_run = []

        def mock_check_output(cmd, **kwargs):
            commands_run.append(" ".join(cmd))
            return b""

        self.input_api.subprocess.check_output = mock.Mock(
            side_effect=mock_check_output
        )
        self.input_api.os_path.exists = mock.Mock(return_value=True)

        results = presubmit_canned_checks.CheckForCommitObjects(
            self.input_api, self.output_api
        )
        self.assertEqual(results, [])
        # Ensure HEAD:.gitmodules was never fetched.
        self.assertFalse(any("HEAD:.gitmodules" in c for c in commands_run))

    @mock.patch("configparser.ConfigParser")
    def testExplicitlyDeletedGitmodulesDoesNotTriggerHeadFallback(
        self, mock_config_parser
    ):
        # When .gitmodules is explicitly deleted in the CL, config.read() returns []
        # because the file is gone. CheckForCommitObjects must NOT fall back to HEAD:.gitmodules.
        self.mock_parse_deps.return_value = {
            "git_dependencies": "SYNC",
            "deps": {},
        }
        self.input_api.files = [MockRelativeFile(".gitmodules", [], action="D")]
        mock_instance = mock_config_parser.return_value
        mock_instance.read.return_value = []
        mock_instance.items.return_value = []

        commands_run = []

        def mock_check_output(cmd, **kwargs):
            commands_run.append(" ".join(cmd))
            return b""

        self.input_api.subprocess.check_output = mock.Mock(
            side_effect=mock_check_output
        )
        self.input_api.os_path.exists = mock.Mock(return_value=False)

        results = presubmit_canned_checks.CheckForCommitObjects(
            self.input_api, self.output_api
        )
        self.assertEqual(results, [])
        # Ensure HEAD:.gitmodules was never fetched.
        self.assertFalse(any("HEAD:.gitmodules" in c for c in commands_run))

    def testSurrogateEscapePathDecoding(self):
        # Non-UTF-8 bytes in tree entry names should decode using surrogateescape
        # rather than throwing UnicodeDecodeError.
        self.input_api.files = [MockRelativeFile("submodule", [])]
        # "\xff\xfe" is invalid UTF-8.
        non_utf8_entry = b"160000 commit 1234\tsub_\xff\xfe\0"
        self.input_api.subprocess.check_output = mock.Mock(
            side_effect=[
                b"",  # git show HEAD:DEPS
                non_utf8_entry,  # git ls-tree
            ]
        )

        results = presubmit_canned_checks.CheckForCommitObjects(
            self.input_api, self.output_api
        )
        self.assertEqual(len(results), 1)
        self.assertIn("sub_\udcff\udcfe", results[0].items)

    def testRepositoryRootEvaluatedOnce(self):
        # input_api.change.RepositoryRoot() should only be called once.
        call_count = 0

        def counting_repo_root():
            nonlocal call_count
            call_count += 1
            return "/tmp/repo"

        self.input_api.change.RepositoryRoot = counting_repo_root
        self.input_api.files = [MockRelativeFile("a.txt", [])]
        self.input_api.subprocess.check_output = mock.Mock(return_value=b"")

        presubmit_canned_checks.CheckForCommitObjects(
            self.input_api, self.output_api
        )
        self.assertEqual(1, call_count)


if __name__ == "__main__":
    unittest.main()
