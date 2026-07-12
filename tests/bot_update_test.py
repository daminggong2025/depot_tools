#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for the pure helper functions in bot_update.py."""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'recipes', 'recipe_modules', 'bot_update', 'resources'))
import bot_update


class RefToRemoteRefTest(unittest.TestCase):

    def testRefsHeads(self):
        self.assertEqual(bot_update.ref_to_remote_ref('refs/heads/main'),
                         'refs/remotes/origin/main')
        # Branch names containing slashes keep their full name.
        self.assertEqual(bot_update.ref_to_remote_ref('refs/heads/foo/bar'),
                         'refs/remotes/origin/foo/bar')

    def testRefsBranchHeads(self):
        self.assertEqual(bot_update.ref_to_remote_ref('refs/branch-heads/4472'),
                         'refs/remotes/branch-heads/4472')

    def testOriginPrefix(self):
        self.assertEqual(bot_update.ref_to_remote_ref('origin/main'),
                         'refs/remotes/origin/main')

    def testOtherRefsUnchanged(self):
        for ref in ('main', 'refs/tags/v1.0', 'refs/remotes/origin/main',
                    'deadbeef' * 5, ''):
            self.assertEqual(bot_update.ref_to_remote_ref(ref), ref)


class CommitMessageFootersTest(unittest.TestCase):

    def testSimpleFooters(self):
        message = ('Fix a thing\n'
                   '\n'
                   'Some description.\n'
                   '\n'
                   'Bug: 123\n'
                   'Cr-Commit-Position: refs/heads/main@{#100}\n')
        self.assertEqual(bot_update.get_commit_message_footer_map(message), {
            'Bug': '123',
            'Cr-Commit-Position': 'refs/heads/main@{#100}',
        })

    def testFooterLookup(self):
        message = 'Subject\n\nBug: 123\n'
        self.assertEqual(bot_update.get_commit_message_footer(message, 'Bug'),
                         '123')
        self.assertIsNone(
            bot_update.get_commit_message_footer(message, 'Change-Id'))

    def testOnlyLastParagraphIsFooterBlock(self):
        message = ('Subject\n'
                   '\n'
                   'Bug: 999\n'
                   '\n'
                   'Change-Id: Iabcdef\n')
        self.assertEqual(bot_update.get_commit_message_footer_map(message),
                         {'Change-Id': 'Iabcdef'})

    def testDuplicateKeyLastWins(self):
        message = 'Subject\n\nBug: 1\nBug: 2\n'
        self.assertEqual(bot_update.get_commit_message_footer_map(message),
                         {'Bug': '2'})

    def testMalformedLinesSkipped(self):
        # Lines without a colon are skipped, but surrounding valid footers
        # are still parsed (matching Gerrit/JGit leniency).
        message = ('Subject\n'
                   '\n'
                   'Bug: 1\n'
                   'not a footer line\n'
                   ': no key\n'
                   'Change-Id: I0\n')
        self.assertEqual(bot_update.get_commit_message_footer_map(message), {
            'Bug': '1',
            'Change-Id': 'I0',
        })

    def testValueHandling(self):
        # Values are stripped, may be empty, and may contain colons.
        message = 'Subject\n\nA:    padded  \nB:\nC: x:y\n'
        self.assertEqual(bot_update.get_commit_message_footer_map(message), {
            'A': 'padded',
            'B': '',
            'C': 'x:y',
        })

    def testContinuationLinesNotSupported(self):
        # An indented continuation line has no colon and is dropped rather
        # than appended to the previous footer's value.
        message = 'Subject\n\nKey: first\n  continuation\n'
        self.assertEqual(bot_update.get_commit_message_footer_map(message),
                         {'Key': 'first'})

    def testKeyIsNotStripped(self):
        # Suspicious: a space before the colon becomes part of the key, so
        # a lookup for 'Key' misses this footer.
        message = 'Subject\n\nKey : value\n'
        self.assertEqual(bot_update.get_commit_message_footer_map(message),
                         {'Key ': 'value'})
        self.assertIsNone(bot_update.get_commit_message_footer(message, 'Key'))

    def testSubjectOnlyMessageParsedAsFooter(self):
        # Suspicious: with no separate footer paragraph, a subject line
        # containing a colon is parsed as a footer.
        self.assertEqual(
            bot_update.get_commit_message_footer_map('Revert: broken change'),
            {'Revert': 'broken change'})

    def testUrlInFooterParagraphBecomesFooter(self):
        # Suspicious: a URL line in the last paragraph matches the footer
        # regex on the scheme's colon.
        message = 'Subject\n\nSee https://crbug.com/123\nBug: 123\n'
        self.assertEqual(bot_update.get_commit_message_footer_map(message), {
            'See https': '//crbug.com/123',
            'Bug': '123',
        })

    def testCrlfMessage(self):
        message = 'Subject\r\n\r\nBug: 123\r\n'
        self.assertEqual(bot_update.get_commit_message_footer_map(message),
                         {'Bug': '123'})

    def testEmptyMessage(self):
        self.assertEqual(bot_update.get_commit_message_footer_map(''), {})


class ParseRevisionsTest(unittest.TestCase):

    def testEmptyRevisionsDefaultsRootToHead(self):
        self.assertEqual(bot_update.parse_revisions([], 'src'), {'src': 'HEAD'})

    def testPlainRevisionSetsRoot(self):
        self.assertEqual(bot_update.parse_revisions(['deadbeef'], 'src'),
                         {'src': 'deadbeef'})

    def testMultiplePlainRevisionsLastWins(self):
        self.assertEqual(
            bot_update.parse_revisions(['aaa111', 'bbb222'], 'src'),
            {'src': 'bbb222'})

    def testCommaSeparatedRevisions(self):
        self.assertEqual(
            bot_update.parse_revisions(['src/v8@aaa111,bbb222'], 'src'), {
                'src': 'bbb222',
                'src/v8': 'aaa111',
            })

    def testAltRootRevision(self):
        self.assertEqual(bot_update.parse_revisions(['src/v8@deadbeef'], 'src'),
                         {
                             'src': 'HEAD',
                             'src/v8': 'deadbeef',
                         })

    def testUrlRootGetsGitSuffix(self):
        self.assertEqual(
            bot_update.parse_revisions(
                ['https://foo.googlesource.com/bar@12345'], 'src'), {
                    'src': 'HEAD',
                    'https://foo.googlesource.com/bar.git': '12345',
                })
        # A url already ending in .git is kept as is.
        self.assertEqual(
            bot_update.parse_revisions(
                ['https://foo.googlesource.com/bar.git@12345'], 'src'), {
                    'src': 'HEAD',
                    'https://foo.googlesource.com/bar.git': '12345',
                })

    def testHttpUrlRewrittenToHttps(self):
        # Suspicious: an http:// root is silently rewritten to https://.
        self.assertEqual(
            bot_update.parse_revisions(['http://foo.example.com/bar@1'], 'src'),
            {
                'src': 'HEAD',
                'https://foo.example.com/bar.git': '1',
            })

    def testUnrecognizedSchemeIgnored(self):
        self.assertEqual(
            bot_update.parse_revisions(['ftp://foo.example.com/bar@1'], 'src'),
            {'src': 'HEAD'})

    def testWindowsPathRootIsLocalPath(self):
        # A drive letter is not a URL scheme; the path is kept as a root.
        self.assertEqual(
            bot_update.parse_revisions(['C:\\build\\src@deadbeef'], 'src'), {
                'src': 'HEAD',
                'C:\\build\\src': 'deadbeef',
            })
        # Drive letter with forward slashes.
        self.assertEqual(
            bot_update.parse_revisions(['c:/build/src@deadbeef'], 'src'), {
                'src': 'HEAD',
                'c:/build/src': 'deadbeef',
            })

    def testRevisionMayContainAtSign(self):
        self.assertEqual(
            bot_update.parse_revisions(
                ['bar@refs/experimental/test@example.com/test'], 'src'), {
                    'src': 'HEAD',
                    'bar': 'refs/experimental/test@example.com/test',
                })

    def testLocalPathRootStripsSlashes(self):
        # Local paths lose leading and trailing slashes, including the
        # leading slash of an absolute path.
        self.assertEqual(
            bot_update.parse_revisions(['/b/build/src@abc123'], 'src'), {
                'src': 'HEAD',
                'b/build/src': 'abc123',
            })

    def testEmptyRevisionAfterAtSign(self):
        # Suspicious: 'src@' pins an empty-string revision instead of HEAD.
        self.assertEqual(bot_update.parse_revisions(['src@'], 'src'),
                         {'src': ''})

    def testRootWithTrailingSlashIsStripped(self):
        # A plain revision is stored under the same stripped key as the
        # default entry, so there is only one entry for the root.
        self.assertEqual(bot_update.parse_revisions(['deadbeef'], 'src/'),
                         {'src': 'deadbeef'})

    def testAltRootWithTrailingSlashIsStripped(self):
        self.assertEqual(bot_update.parse_revisions(['src/v8/@abc123'], 'src'),
                         {
                             'src': 'HEAD',
                             'src/v8': 'abc123',
                         })


class GetTargetBranchAndRevisionTest(unittest.TestCase):
    URL = 'https://chromium.googlesource.com/chromium/src.git'

    def testUnconfiguredDefaultsToMainHead(self):
        self.assertEqual(
            bot_update.get_target_branch_and_revision('src', self.URL, {}),
            ('refs/remotes/origin/main', 'HEAD'))

    def testShaPinsRevisionOnMain(self):
        sha = '4a2c6cb3e37eeb6b7d05bd3d426ee5c25599054d'
        self.assertEqual(
            bot_update.get_target_branch_and_revision('src', self.URL,
                                                      {'src': sha}),
            ('refs/remotes/origin/main', sha))
        # Matching is case-insensitive.
        self.assertEqual(
            bot_update.get_target_branch_and_revision('src', self.URL,
                                                      {'src': sha.upper()}),
            ('refs/remotes/origin/main', sha.upper()))

    def testShortHexIsBranchNotRevision(self):
        # Five or more leading hex chars count as a revision; four do not.
        self.assertEqual(
            bot_update.get_target_branch_and_revision('src', self.URL,
                                                      {'src': 'abcde'}),
            ('refs/remotes/origin/main', 'abcde'))
        self.assertEqual(
            bot_update.get_target_branch_and_revision('src', self.URL,
                                                      {'src': 'abcd'}),
            ('refs/remotes/origin/abcd', 'HEAD'))

    def testHexPrefixedBranchIsBranch(self):
        # A branch name that merely starts with 5+ hex chars is a branch,
        # not a pinned revision.
        for branch in ('badc0de-branch', 'deadbeef/nested', 'cafe123.4'):
            self.assertEqual(
                bot_update.get_target_branch_and_revision(
                    'src', self.URL, {'src': branch}),
                ('refs/remotes/origin/' + branch, 'HEAD'))

    def testHexLengthBoundaries(self):
        # 5 to 40 all-hex chars classify as a revision; 41 do not.
        for n in (39, 40):
            self.assertEqual(
                bot_update.get_target_branch_and_revision(
                    'src', self.URL, {'src': 'a' * n}),
                ('refs/remotes/origin/main', 'a' * n))
        self.assertEqual(
            bot_update.get_target_branch_and_revision('src', self.URL,
                                                      {'src': 'a' * 41}),
            ('refs/remotes/origin/' + 'a' * 41, 'HEAD'))

    def testMixedCaseHexIsRevision(self):
        self.assertEqual(
            bot_update.get_target_branch_and_revision('src', self.URL,
                                                      {'src': 'DeadBeef00'}),
            ('refs/remotes/origin/main', 'DeadBeef00'))

    def testBranchName(self):
        self.assertEqual(
            bot_update.get_target_branch_and_revision('src', self.URL,
                                                      {'src': 'lkgr'}),
            ('refs/remotes/origin/lkgr', 'HEAD'))

    def testRefsHeadsBranch(self):
        self.assertEqual(
            bot_update.get_target_branch_and_revision(
                'src', self.URL, {'src': 'refs/heads/lkgr'}),
            ('refs/remotes/origin/lkgr', 'HEAD'))

    def testOriginBranch(self):
        self.assertEqual(
            bot_update.get_target_branch_and_revision('src', self.URL,
                                                      {'src': 'origin/lkgr'}),
            ('refs/remotes/origin/lkgr', 'HEAD'))

    def testBranchHeads(self):
        self.assertEqual(
            bot_update.get_target_branch_and_revision(
                'src', self.URL, {'src': 'refs/branch-heads/4472'}),
            ('refs/remotes/branch-heads/4472', 'HEAD'))

    def testRefsTagsKeptAsIs(self):
        self.assertEqual(
            bot_update.get_target_branch_and_revision(
                'src', self.URL, {'src': 'refs/tags/v1.0'}),
            ('refs/tags/v1.0', 'HEAD'))

    def testBranchColonRevision(self):
        sha = '4a2c6cb3e37eeb6b7d05bd3d426ee5c25599054d'
        self.assertEqual(
            bot_update.get_target_branch_and_revision(
                'src', self.URL, {'src': 'refs/heads/lkgr:%s' % sha}),
            ('refs/remotes/origin/lkgr', sha))
        self.assertEqual(
            bot_update.get_target_branch_and_revision('src', self.URL,
                                                      {'src': 'lkgr:%s' % sha}),
            ('refs/remotes/origin/lkgr', sha))

    def testLookupByUrlAndNamePrecedence(self):
        self.assertEqual(
            bot_update.get_target_branch_and_revision(
                'src', self.URL, {self.URL: 'refs/heads/lkgr'}),
            ('refs/remotes/origin/lkgr', 'HEAD'))
        # The solution name takes precedence over the url.
        self.assertEqual(
            bot_update.get_target_branch_and_revision(
                'src', self.URL, {
                    'src': 'refs/heads/by-name',
                    self.URL: 'refs/heads/by-url',
                }), ('refs/remotes/origin/by-name', 'HEAD'))

    def testSolutionNameSlashesStripped(self):
        self.assertEqual(
            bot_update.get_target_branch_and_revision(
                '/src/', self.URL, {'src': 'refs/heads/lkgr'}),
            ('refs/remotes/origin/lkgr', 'HEAD'))

    def testHeadBecomesOriginHead(self):
        # Suspicious: 'HEAD' (the parse_revisions default) is treated as a
        # branch name, yielding refs/remotes/origin/HEAD.
        self.assertEqual(
            bot_update.get_target_branch_and_revision('src', self.URL,
                                                      {'src': 'HEAD'}),
            ('refs/remotes/origin/HEAD', 'HEAD'))

    def testEmptyRevisionDefaultsToMainHead(self):
        # The empty string produced by parse_revisions for 'src@' is falsy
        # and falls back to the defaults.
        self.assertEqual(
            bot_update.get_target_branch_and_revision('src', self.URL,
                                                      {'src': ''}),
            ('refs/remotes/origin/main', 'HEAD'))


if __name__ == '__main__':
    unittest.main()
