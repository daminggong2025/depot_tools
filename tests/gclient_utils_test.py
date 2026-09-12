#!/usr/bin/env vpython3
# coding=utf-8
# Copyright 2012 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gclient_utils
import subprocess2
from testing_support import trial_dir


class CheckCallAndFilterTestCase(unittest.TestCase):
    class ProcessIdMock(object):
        def __init__(self, test_string, return_code=0):
            self.stdout = test_string.encode("utf-8")
            self.pid = 9284
            self.return_code = return_code

        def wait(self):
            return self.return_code

    def PopenMock(self, *args, **kwargs):
        kid = self.kids.pop(0)
        stdout = kwargs.get("stdout")
        os.write(stdout, kid.stdout)
        return kid

    def setUp(self):
        super(CheckCallAndFilterTestCase, self).setUp()
        self.printfn = io.StringIO()
        self.stdout = io.BytesIO()
        self.kids = []
        mock.patch("sys.stdout", mock.Mock()).start()
        mock.patch("sys.stdout.buffer", self.stdout).start()
        mock.patch("sys.stdout.isatty", return_value=False).start()
        mock.patch("builtins.print", self.printfn.write).start()
        mock.patch("sys.stdout.flush", lambda: None).start()
        self.addCleanup(mock.patch.stopall)

    @mock.patch("subprocess2.Popen")
    def testCheckCallAndFilter(self, mockPopen):
        cwd = "bleh"
        args = ["boo", "foo", "bar"]
        test_string = "ahah\naccb\nallo\naddb\n✔"

        self.kids = [self.ProcessIdMock(test_string)]
        mockPopen.side_effect = self.PopenMock

        line_list = []
        result = gclient_utils.CheckCallAndFilter(
            args,
            cwd=cwd,
            show_header=True,
            always_show_header=True,
            filter_fn=line_list.append,
        )

        self.assertEqual(result, test_string.encode("utf-8"))
        self.assertEqual(
            line_list,
            [
                "________ running 'boo foo bar' in 'bleh'\n",
                "ahah",
                "accb",
                "allo",
                "addb",
                "✔",
            ],
        )
        self.assertEqual(self.stdout.getvalue(), b"")

        kall = mockPopen.call_args
        self.assertEqual(kall.args, (args,))
        self.assertEqual(kall.kwargs["cwd"], cwd)
        self.assertEqual(kall.kwargs["stdout"], mock.ANY)
        self.assertEqual(kall.kwargs["stderr"], subprocess2.STDOUT)
        self.assertEqual(kall.kwargs["bufsize"], 0)
        self.assertIn("env", kall.kwargs)
        self.assertIn("COLUMNS", kall.kwargs["env"])
        self.assertIn("LINES", kall.kwargs["env"])

    @mock.patch("time.sleep")
    @mock.patch("subprocess2.Popen")
    def testCheckCallAndFilter_RetryOnce(self, mockPopen, mockTime):
        cwd = "bleh"
        args = ["boo", "foo", "bar"]
        test_string = "ahah\naccb\nallo\naddb\n✔"

        self.kids = [
            self.ProcessIdMock(test_string, 1),
            self.ProcessIdMock(test_string, 0),
        ]
        mockPopen.side_effect = self.PopenMock

        line_list = []
        result = gclient_utils.CheckCallAndFilter(
            args,
            cwd=cwd,
            show_header=True,
            always_show_header=True,
            filter_fn=line_list.append,
            retry=True,
        )

        self.assertEqual(result, test_string.encode("utf-8"))

        self.assertEqual(
            line_list,
            [
                "________ running 'boo foo bar' in 'bleh'\n",
                "ahah",
                "accb",
                "allo",
                "addb",
                "✔",
                "________ running 'boo foo bar' in 'bleh' attempt 2 / 2\n",
                "ahah",
                "accb",
                "allo",
                "addb",
                "✔",
            ],
        )

        mockTime.assert_called_with(gclient_utils.RETRY_INITIAL_SLEEP)

        for i in range(2):
            kall = mockPopen.mock_calls[i]
            self.assertEqual(kall.args, (args,))
            self.assertEqual(kall.kwargs["cwd"], cwd)
            self.assertEqual(kall.kwargs["stdout"], mock.ANY)
            self.assertEqual(kall.kwargs["stderr"], subprocess2.STDOUT)
            self.assertEqual(kall.kwargs["bufsize"], 0)
            self.assertIn("env", kall.kwargs)
            self.assertIn("COLUMNS", kall.kwargs["env"])
            self.assertIn("LINES", kall.kwargs["env"])

        self.assertEqual(self.stdout.getvalue(), b"")
        self.assertEqual(
            self.printfn.getvalue(),
            'WARNING: subprocess \'"boo" "foo" "bar"\' in bleh failed; will retry '
            "after a short nap...",
        )

    @mock.patch("subprocess2.Popen")
    def testCheckCallAndFilter_PrintStdout(self, mockPopen):
        cwd = "bleh"
        args = ["boo", "foo", "bar"]
        test_string = "ahah\naccb\nallo\naddb\n✔"

        self.kids = [self.ProcessIdMock(test_string)]
        mockPopen.side_effect = self.PopenMock

        result = gclient_utils.CheckCallAndFilter(
            args,
            cwd=cwd,
            show_header=True,
            always_show_header=True,
            print_stdout=True,
        )

        self.assertEqual(result, test_string.encode("utf-8"))
        self.assertEqual(
            self.stdout.getvalue().splitlines(),
            [
                b"________ running 'boo foo bar' in 'bleh'",
                b"ahah",
                b"accb",
                b"allo",
                b"addb",
                b"\xe2\x9c\x94",
            ],
        )


class AnnotatedTestCase(unittest.TestCase):
    def setUp(self):
        self.out = gclient_utils.MakeFileAnnotated(io.BytesIO())
        self.annotated = gclient_utils.MakeFileAnnotated(
            io.BytesIO(), include_zero=True
        )

    def testWrite(self):
        test_cases = [
            ("test string\n", b"test string\n"),
            (b"test string\n", b"test string\n"),
            ("✔\n", b"\xe2\x9c\x94\n"),
            (b"\xe2\x9c\x94\n", b"\xe2\x9c\x94\n"),
            ("first line\nsecondline\n", b"first line\nsecondline\n"),
            (b"first line\nsecondline\n", b"first line\nsecondline\n"),
        ]

        for test_input, expected_output in test_cases:
            out = gclient_utils.MakeFileAnnotated(io.BytesIO())
            out.write(test_input)
            self.assertEqual(out.getvalue(), expected_output)

    def testWrite_Annotated(self):
        test_cases = [
            ("test string\n", b"0>test string\n"),
            (b"test string\n", b"0>test string\n"),
            ("✔\n", b"0>\xe2\x9c\x94\n"),
            (b"\xe2\x9c\x94\n", b"0>\xe2\x9c\x94\n"),
            ("first line\nsecondline\n", b"0>first line\n0>secondline\n"),
            (b"first line\nsecondline\n", b"0>first line\n0>secondline\n"),
        ]

        for test_input, expected_output in test_cases:
            out = gclient_utils.MakeFileAnnotated(
                io.BytesIO(), include_zero=True
            )
            out.write(test_input)
            self.assertEqual(out.getvalue(), expected_output)

    def testByteByByteInput(self):
        self.out.write(b"\xe2")
        self.out.write(b"\x9c")
        self.out.write(b"\x94")
        self.out.write(b"\n")
        self.out.write(b"\xe2")
        self.out.write(b"\n")
        self.assertEqual(self.out.getvalue(), b"\xe2\x9c\x94\n\xe2\n")

    def testByteByByteInput_Annotated(self):
        self.annotated.write(b"\xe2")
        self.annotated.write(b"\x9c")
        self.annotated.write(b"\x94")
        self.annotated.write(b"\n")
        self.annotated.write(b"\xe2")
        self.annotated.write(b"\n")
        self.assertEqual(self.annotated.getvalue(), b"0>\xe2\x9c\x94\n0>\xe2\n")

    def testFlush_Annotated(self):
        self.annotated.write(b"first line\nsecond line")
        self.assertEqual(self.annotated.getvalue(), b"0>first line\n")
        self.annotated.flush()
        self.assertEqual(
            self.annotated.getvalue(), b"0>first line\n0>second line\n"
        )


class SplitUrlRevisionTestCase(unittest.TestCase):
    def testSSHUrl(self):
        url = "ssh://test@example.com/test.git"
        rev = "ac345e52dc"
        out_url, out_rev = gclient_utils.SplitUrlRevision(url)
        self.assertEqual(out_rev, None)
        self.assertEqual(out_url, url)
        out_url, out_rev = gclient_utils.SplitUrlRevision("%s@%s" % (url, rev))
        self.assertEqual(out_rev, rev)
        self.assertEqual(out_url, url)
        url = "ssh://example.com/test.git"
        out_url, out_rev = gclient_utils.SplitUrlRevision(url)
        self.assertEqual(out_rev, None)
        self.assertEqual(out_url, url)
        out_url, out_rev = gclient_utils.SplitUrlRevision("%s@%s" % (url, rev))
        self.assertEqual(out_rev, rev)
        self.assertEqual(out_url, url)
        url = "ssh://example.com/git/test.git"
        out_url, out_rev = gclient_utils.SplitUrlRevision(url)
        self.assertEqual(out_rev, None)
        self.assertEqual(out_url, url)
        out_url, out_rev = gclient_utils.SplitUrlRevision("%s@%s" % (url, rev))
        self.assertEqual(out_rev, rev)
        self.assertEqual(out_url, url)
        rev = "test-stable"
        out_url, out_rev = gclient_utils.SplitUrlRevision("%s@%s" % (url, rev))
        self.assertEqual(out_rev, rev)
        self.assertEqual(out_url, url)
        url = "ssh://user-name@example.com/~/test.git"
        out_url, out_rev = gclient_utils.SplitUrlRevision(url)
        self.assertEqual(out_rev, None)
        self.assertEqual(out_url, url)
        out_url, out_rev = gclient_utils.SplitUrlRevision("%s@%s" % (url, rev))
        self.assertEqual(out_rev, rev)
        self.assertEqual(out_url, url)
        url = "ssh://user-name@example.com/~username/test.git"
        out_url, out_rev = gclient_utils.SplitUrlRevision(url)
        self.assertEqual(out_rev, None)
        self.assertEqual(out_url, url)
        out_url, out_rev = gclient_utils.SplitUrlRevision("%s@%s" % (url, rev))
        self.assertEqual(out_rev, rev)
        self.assertEqual(out_url, url)
        url = "git@github.com:dart-lang/spark.git"
        out_url, out_rev = gclient_utils.SplitUrlRevision(url)
        self.assertEqual(out_rev, None)
        self.assertEqual(out_url, url)
        out_url, out_rev = gclient_utils.SplitUrlRevision("%s@%s" % (url, rev))
        self.assertEqual(out_rev, rev)
        self.assertEqual(out_url, url)

    def testSVNUrl(self):
        url = "svn://example.com/test"
        rev = "ac345e52dc"
        out_url, out_rev = gclient_utils.SplitUrlRevision(url)
        self.assertEqual(out_rev, None)
        self.assertEqual(out_url, url)
        out_url, out_rev = gclient_utils.SplitUrlRevision("%s@%s" % (url, rev))
        self.assertEqual(out_rev, rev)
        self.assertEqual(out_url, url)

    def testInvalidRevisionStartingWithHyphen(self):
        with self.assertRaises(gclient_utils.Error):
            gclient_utils.SplitUrlRevision(
                "https://example.com/repo.git@--upload-pack=foo"
            )
        with self.assertRaises(gclient_utils.Error):
            gclient_utils.SplitUrlRevision("https://example.com/repo.git@-b")
        with self.assertRaises(gclient_utils.Error):
            gclient_utils.SplitUrlRevision(
                "ssh://user@example.com/repo.git@--upload-pack=foo"
            )

    def testInvalidUrlStartingWithHyphen(self):
        with self.assertRaises(gclient_utils.Error):
            gclient_utils.SplitUrlRevision("--upload-pack=foo@main")
        with self.assertRaises(gclient_utils.Error):
            gclient_utils.SplitUrlRevision("-u")


class ExtracRefNameTest(unittest.TestCase):
    def testMatchFound(self):
        self.assertEqual(
            "main",
            gclient_utils.ExtractRefName("origin", "refs/remote/origin/main"),
        )
        self.assertEqual(
            "1234", gclient_utils.ExtractRefName("origin", "refs/tags/1234")
        )
        self.assertEqual(
            "chicken",
            gclient_utils.ExtractRefName("origin", "refs/heads/chicken"),
        )

    def testNoMatch(self):
        self.assertIsNone(gclient_utils.ExtractRefName("origin", "abcbbb1234"))


class GClientUtilsTest(trial_dir.TestCase):
    def testHardToDelete(self):
        # Use the fact that tearDown will delete the directory to make it hard
        # to do so.
        l1 = os.path.join(self.root_dir, "l1")
        l2 = os.path.join(l1, "l2")
        l3 = os.path.join(l2, "l3")
        f3 = os.path.join(l3, "f3")
        os.mkdir(l1)
        os.mkdir(l2)
        os.mkdir(l3)
        gclient_utils.FileWrite(f3, "foo")
        os.chmod(f3, 0)
        os.chmod(l3, 0)
        os.chmod(l2, 0)
        os.chmod(l1, 0)

    def testUpgradeToHttps(self):
        values = [
            ["", ""],
            [None, None],
            ["foo", "https://foo"],
            ["http://foo", "https://foo"],
            ["foo/", "https://foo/"],
            ["ssh-svn://foo", "ssh-svn://foo"],
            ["ssh-svn://foo/bar/", "ssh-svn://foo/bar/"],
            ["codereview.chromium.org", "https://codereview.chromium.org"],
            ["codereview.chromium.org/", "https://codereview.chromium.org/"],
            [
                "chromium-review.googlesource.com",
                "https://chromium-review.googlesource.com",
            ],
            [
                "chromium-review.googlesource.com/",
                "https://chromium-review.googlesource.com/",
            ],
            ["http://foo:10000", "http://foo:10000"],
            ["http://foo:10000/bar", "http://foo:10000/bar"],
            ["foo:10000", "http://foo:10000"],
            ["foo:", "https://foo:"],
        ]
        for content, expected in values:
            self.assertEqual(expected, gclient_utils.UpgradeToHttps(content))

    def testParseCodereviewSettingsContent(self):
        values = [
            ["# bleh\n", {}],
            ["\t# foo : bar\n", {}],
            ["Foo:bar", {"Foo": "bar"}],
            ["Foo:bar:baz\n", {"Foo": "bar:baz"}],
            [" Foo : bar ", {"Foo": "bar"}],
            [" Foo : bar \n", {"Foo": "bar"}],
            ["a:b\n\rc:d\re:f", {"a": "b", "c": "d", "e": "f"}],
            ["an_url:http://value/", {"an_url": "http://value/"}],
            [
                "CODE_REVIEW_SERVER : http://r/s",
                {"CODE_REVIEW_SERVER": "https://r/s"},
            ],
            ["VIEW_VC:http://r/s", {"VIEW_VC": "https://r/s"}],
        ]
        for content, expected in values:
            self.assertEqual(
                expected, gclient_utils.ParseCodereviewSettingsContent(content)
            )

    def testFileRead_Bytes(self):
        with gclient_utils.temporary_file() as tmp:
            gclient_utils.FileWrite(
                tmp, b"foo \xe2\x9c bar", mode="wb", encoding=None
            )
            self.assertEqual("foo \ufffd bar", gclient_utils.FileRead(tmp))

    def testFileRead_Unicode(self):
        with gclient_utils.temporary_file() as tmp:
            gclient_utils.FileWrite(tmp, "foo ✔ bar")
            self.assertEqual("foo ✔ bar", gclient_utils.FileRead(tmp))

    def testTemporaryFile(self):
        with gclient_utils.temporary_file() as tmp:
            gclient_utils.FileWrite(tmp, "test")
            self.assertEqual("test", gclient_utils.FileRead(tmp))
        self.assertFalse(os.path.exists(tmp))

    def testMergeConditions(self):
        self.assertEqual(None, gclient_utils.merge_conditions(None, None))

        self.assertEqual("foo", gclient_utils.merge_conditions("foo", None))

        self.assertEqual("foo", gclient_utils.merge_conditions(None, "foo"))

        self.assertEqual(
            "(foo) and (bar)", gclient_utils.merge_conditions("foo", "bar")
        )

        self.assertEqual(
            "(foo or bar) and (baz)",
            gclient_utils.merge_conditions("foo or bar", "baz"),
        )

    def testIsEnvAi(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(gclient_utils.IsEnvAi())
            self.assertEqual(gclient_utils.GetAiAgentEnvVars(), [])
        with mock.patch.dict(os.environ, {"GEMINI_CLI": "1"}, clear=True):
            self.assertTrue(gclient_utils.IsEnvAi())
            self.assertEqual(gclient_utils.GetAiAgentEnvVars(), ["GEMINI_CLI"])
        with mock.patch.dict(os.environ, {"AI_AGENT": "true"}, clear=True):
            self.assertTrue(gclient_utils.IsEnvAi())
            self.assertEqual(gclient_utils.GetAiAgentEnvVars(), ["AI_AGENT"])

        for falsey_val in ("", "0", "false", "FALSE"):
            with mock.patch.dict(
                os.environ, {"GEMINI_CLI": falsey_val}, clear=True
            ):
                self.assertFalse(gclient_utils.IsEnvAi())
                self.assertEqual(gclient_utils.GetAiAgentEnvVars(), [])

        self.assertFalse(gclient_utils.IsEnvAi({"GEMINI_CLI": None}))
        self.assertFalse(
            gclient_utils.IsEnvAi({"GEMINI_CLI": "0", "AI_AGENT": "false"})
        )
        self.assertTrue(
            gclient_utils.IsEnvAi({"GEMINI_CLI": "0", "AI_AGENT": "1"})
        )
        self.assertEqual(
            gclient_utils.GetAiAgentEnvVars(
                {"GEMINI_CLI": "0", "AI_AGENT": "1"}
            ),
            ["AI_AGENT"],
        )


class IsGitShaTest(unittest.TestCase):
    def testIsFullGitSha(self):
        # A full object name is 40 hex chars for SHA-1 or 64 for SHA-256.
        self.assertTrue(gclient_utils.IsFullGitSha("a" * 40))
        self.assertTrue(gclient_utils.IsFullGitSha("0" * 64))
        self.assertTrue(
            gclient_utils.IsFullGitSha(
                "0123456789abcdef0123456789abcdef01234567"
            )
        )

    def testIsFullGitShaRejectsInvalid(self):
        # Abbreviated, invalid-length (41-63), over-length, and non-hex values
        # are not full shas.
        self.assertFalse(gclient_utils.IsFullGitSha("a" * 39))
        self.assertFalse(gclient_utils.IsFullGitSha("a" * 41))
        self.assertFalse(gclient_utils.IsFullGitSha("a" * 63))
        self.assertFalse(gclient_utils.IsFullGitSha("a" * 65))
        self.assertFalse(gclient_utils.IsFullGitSha("z" * 40))

    def testIsGitSha(self):
        # Abbreviated (>=6 hex) through a full SHA-256 (64) are accepted.
        self.assertTrue(gclient_utils.IsGitSha("abcdef"))
        self.assertTrue(gclient_utils.IsGitSha("a" * 40))
        self.assertTrue(gclient_utils.IsGitSha("a" * 64))

    def testIsGitShaRejectsInvalid(self):
        self.assertFalse(gclient_utils.IsGitSha("abc"))
        self.assertFalse(gclient_utils.IsGitSha("a" * 65))
        self.assertFalse(gclient_utils.IsGitSha("z" * 40))


class SafeReplaceTest(unittest.TestCase):
    def setUp(self):
        self.mock_os = mock.patch("gclient_utils.os").start()
        self.addCleanup(mock.patch.stopall)

    def testReplace(self):
        gclient_utils.safe_replace("old", "new")
        self.mock_os.replace.assert_called_with("old", "new")

    def testReplaceRetry(self):
        self.mock_os.replace.side_effect = [OSError, OSError, None]
        with mock.patch("time.sleep") as mock_sleep:
            gclient_utils.safe_replace("old", "new")
            self.assertEqual(self.mock_os.replace.call_count, 3)
            self.assertEqual(mock_sleep.call_count, 2)

    def testReplaceFailure(self):
        self.mock_os.replace.side_effect = OSError
        with mock.patch("time.sleep"):
            with self.assertRaises(OSError):
                gclient_utils.safe_replace("old", "new")


class ReflinkOrCopyTest(unittest.TestCase):
    def setUp(self):
        super(ReflinkOrCopyTest, self).setUp()
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    @mock.patch("sys.platform", "linux")
    @mock.patch("subprocess.run")
    def test_reflink_linux_file(self, mock_run):
        mock_run.return_value.returncode = 0
        src = os.path.join(self.tmpdir, "src_file")
        dst = os.path.join(self.tmpdir, "dst_file")
        with open(src, "w") as f:
            f.write("test content")

        result = gclient_utils.reflink_or_copy(src, dst)
        self.assertTrue(result)
        mock_run.assert_called_once_with(
            ["cp", "-a", "--reflink=auto", src, dst],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @mock.patch("sys.platform", "linux")
    @mock.patch("subprocess.run")
    def test_reflink_linux_dir(self, mock_run):
        mock_run.return_value.returncode = 0
        src = os.path.join(self.tmpdir, "src_dir")
        dst = os.path.join(self.tmpdir, "dst_dir")
        os.makedirs(src)

        result = gclient_utils.reflink_or_copy(src, dst)
        self.assertTrue(result)
        mock_run.assert_called_once_with(
            ["cp", "-a", "--reflink=auto", os.path.join(src, "."), dst],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @mock.patch("sys.platform", "darwin")
    @mock.patch("subprocess.run")
    def test_reflink_darwin_file(self, mock_run):
        mock_run.return_value.returncode = 0
        src = os.path.join(self.tmpdir, "src_file")
        dst = os.path.join(self.tmpdir, "dst_file")
        with open(src, "w") as f:
            f.write("darwin content")

        result = gclient_utils.reflink_or_copy(src, dst)
        self.assertTrue(result)
        mock_run.assert_called_once_with(
            ["cp", "-p", "-c", src, dst],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @mock.patch("sys.platform", "darwin")
    @mock.patch("subprocess.run")
    def test_reflink_darwin_dir(self, mock_run):
        mock_run.return_value.returncode = 0
        src = os.path.join(self.tmpdir, "src_dir")
        dst = os.path.join(self.tmpdir, "dst_dir")
        os.makedirs(src)

        result = gclient_utils.reflink_or_copy(src, dst)
        self.assertTrue(result)
        mock_run.assert_called_once_with(
            ["cp", "-R", "-p", "-c", os.path.join(src, "."), dst],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @mock.patch("sys.platform", "win32")
    @mock.patch("gclient_utils._windows_block_clone_file", return_value=True)
    def test_reflink_windows_file(self, mock_clone):
        src = os.path.join(self.tmpdir, "src_file")
        dst = os.path.join(self.tmpdir, "dst_file")
        with open(src, "w") as f:
            f.write("win content")

        result = gclient_utils.reflink_or_copy(src, dst)
        self.assertTrue(result)
        mock_clone.assert_called_once_with(src, dst)

    @mock.patch("sys.platform", "win32")
    @mock.patch("gclient_utils._windows_block_clone_dir", return_value=True)
    def test_reflink_windows_dir(self, mock_clone):
        src = os.path.join(self.tmpdir, "src_dir")
        dst = os.path.join(self.tmpdir, "dst_dir")
        os.makedirs(src)

        result = gclient_utils.reflink_or_copy(src, dst)
        self.assertTrue(result)
        mock_clone.assert_called_once_with(src, dst)

    @mock.patch("sys.platform", "linux")
    @mock.patch("subprocess.run")
    @mock.patch("shutil.copy2")
    def test_fallback_on_nonzero_returncode(self, mock_copy2, mock_run):
        mock_run.return_value.returncode = 1
        src = os.path.join(self.tmpdir, "src_file")
        dst = os.path.join(self.tmpdir, "dst_file")
        with open(src, "w") as f:
            f.write("fallback content")

        result = gclient_utils.reflink_or_copy(src, dst)
        self.assertTrue(result)
        mock_copy2.assert_called_once_with(src, dst)

    @mock.patch("sys.platform", "linux")
    @mock.patch(
        "subprocess.run",
        side_effect=subprocess.SubprocessError("reflink failed"),
    )
    @mock.patch("shutil.copy2")
    def test_fallback_on_subprocess_error(self, mock_copy2, mock_run):
        src = os.path.join(self.tmpdir, "src_file")
        dst = os.path.join(self.tmpdir, "dst_file")
        with open(src, "w") as f:
            f.write("fallback content")

        result = gclient_utils.reflink_or_copy(src, dst)
        self.assertTrue(result)
        mock_copy2.assert_called_once_with(src, dst)

    @mock.patch("sys.platform", "darwin")
    @mock.patch("subprocess.run", side_effect=OSError("cp not found"))
    @mock.patch("shutil.copy2")
    def test_darwin_fallback_on_os_error(self, mock_copy2, mock_run):
        src = os.path.join(self.tmpdir, "src_file")
        dst = os.path.join(self.tmpdir, "dst_file")
        with open(src, "w") as f:
            f.write("fallback content")

        result = gclient_utils.reflink_or_copy(src, dst)
        self.assertTrue(result)
        mock_copy2.assert_called_once_with(src, dst)

    @mock.patch("sys.platform", "win32")
    @mock.patch(
        "gclient_utils._windows_block_clone_file",
        side_effect=OSError("not ReFS"),
    )
    @mock.patch("shutil.copy2")
    def test_windows_fallback_on_os_error(self, mock_copy2, mock_clone):
        src = os.path.join(self.tmpdir, "src_file")
        dst = os.path.join(self.tmpdir, "dst_file")
        with open(src, "w") as f:
            f.write("fallback content")

        result = gclient_utils.reflink_or_copy(src, dst)
        self.assertTrue(result)
        mock_copy2.assert_called_once_with(src, dst)

    @mock.patch("sys.platform", "linux")
    @mock.patch("subprocess.run", side_effect=OSError("fail"))
    @mock.patch("shutil.copy2", side_effect=OSError("copy failed"))
    def test_failure_both_reflink_and_copy(self, mock_copy2, mock_run):
        src = os.path.join(self.tmpdir, "src_file")
        dst = os.path.join(self.tmpdir, "dst_file")
        with open(src, "w") as f:
            f.write("content")

        result = gclient_utils.reflink_or_copy(src, dst)
        self.assertFalse(result)

    def test_real_filesystem_file_copy(self):
        src = os.path.join(self.tmpdir, "real_src.txt")
        dst = os.path.join(self.tmpdir, "sub", "real_dst.txt")
        content = "hello world from real file test\n"
        with open(src, "w") as f:
            f.write(content)

        result = gclient_utils.reflink_or_copy(src, dst)
        self.assertTrue(result)
        self.assertTrue(os.path.isfile(dst))
        with open(dst, "r") as f:
            self.assertEqual(f.read(), content)

    def test_real_filesystem_dir_copy(self):
        src_dir = os.path.join(self.tmpdir, "real_src_dir")
        dst_dir = os.path.join(self.tmpdir, "real_dst_dir")
        nested_dir = os.path.join(src_dir, "nested")
        os.makedirs(nested_dir)
        file1 = os.path.join(src_dir, "file1.txt")
        file2 = os.path.join(nested_dir, "file2.txt")
        with open(file1, "w") as f:
            f.write("data 1")
        with open(file2, "w") as f:
            f.write("data 2")

        result = gclient_utils.reflink_or_copy(src_dir, dst_dir)
        self.assertTrue(result)
        self.assertTrue(os.path.isdir(dst_dir))
        self.assertTrue(os.path.isfile(os.path.join(dst_dir, "file1.txt")))
        self.assertTrue(
            os.path.isfile(os.path.join(dst_dir, "nested", "file2.txt"))
        )
        with open(os.path.join(dst_dir, "file1.txt"), "r") as f:
            self.assertEqual(f.read(), "data 1")
        with open(os.path.join(dst_dir, "nested", "file2.txt"), "r") as f:
            self.assertEqual(f.read(), "data 2")

    def test_windows_block_clone_file_non_windows(self):
        src = os.path.join(self.tmpdir, "win_dummy_src")
        dst = os.path.join(self.tmpdir, "win_dummy_dst")
        with open(src, "w") as f:
            f.write("dummy")
        with mock.patch("ctypes.windll", None, create=True):
            self.assertFalse(gclient_utils._windows_block_clone_file(src, dst))

    def test_windows_block_clone_file_mock_kernel32(self):
        src = os.path.join(self.tmpdir, "src.bin")
        dst = os.path.join(self.tmpdir, "dst.bin")
        with open(src, "wb") as f:
            f.write(b"A" * 65536 + b"TAIL123456")

        mock_kernel32 = mock.MagicMock()
        mock_kernel32.CreateFileW.side_effect = [1001, 1002]

        def fake_get_file_size(handle, ref):
            ref._obj.QuadPart = 65536 + 10
            return True

        mock_kernel32.GetFileSizeEx.side_effect = fake_get_file_size
        mock_kernel32.SetFilePointerEx.return_value = True
        mock_kernel32.SetEndOfFile.return_value = True
        mock_kernel32.DeviceIoControl.return_value = True
        mock_kernel32.CloseHandle.return_value = True

        fake_windll = mock.MagicMock()
        fake_windll.kernel32 = mock_kernel32

        with mock.patch("ctypes.windll", fake_windll, create=True):
            result = gclient_utils._windows_block_clone_file(src, dst)

        self.assertTrue(result)
        self.assertEqual(mock_kernel32.CloseHandle.call_count, 2)
        self.assertTrue(os.path.exists(dst))
        with open(dst, "rb") as f:
            content = f.read()
        self.assertEqual(content[65536:], b"TAIL123456")

    @mock.patch("gclient_utils._windows_block_clone_file")
    def test_windows_block_clone_dir(self, mock_clone_file):
        src = os.path.join(self.tmpdir, "win_dir_src")
        dst = os.path.join(self.tmpdir, "win_dir_dst")
        sub = os.path.join(src, "sub")
        os.makedirs(sub)
        file1 = os.path.join(src, "f1.txt")
        file2 = os.path.join(sub, "f2.txt")
        with open(file1, "w") as f:
            f.write("f1")
        with open(file2, "w") as f:
            f.write("f2")

        result = gclient_utils._windows_block_clone_dir(src, dst)
        self.assertTrue(result)
        self.assertEqual(mock_clone_file.call_count, 2)


if __name__ == "__main__":
    unittest.main()

# vim: ts=2:sw=2:tw=80:et:
