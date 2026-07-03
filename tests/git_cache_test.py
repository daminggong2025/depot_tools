#!/usr/bin/env vpython3
# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for git_cache.py"""

from io import StringIO
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

DEPOT_TOOLS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DEPOT_TOOLS_ROOT)

from testing_support import coverage_utils
import gclient_utils
import git_cache


class GitCacheTest(unittest.TestCase):
    def setUp(self):
        self.cache_dir = tempfile.mkdtemp(prefix='git_cache_test_')
        self.addCleanup(shutil.rmtree, self.cache_dir, ignore_errors=True)
        self.origin_dir = tempfile.mkdtemp(suffix='origin.git')
        self.addCleanup(shutil.rmtree, self.origin_dir, ignore_errors=True)
        git_cache.Mirror.SetCachePath(self.cache_dir)

        # Ensure git_cache works with safe.bareRepository.
        mock.patch.dict(
            'os.environ', {
                'GIT_CONFIG_GLOBAL': os.path.join(self.cache_dir, '.gitconfig'),
            }).start()
        self.addCleanup(mock.patch.stopall)
        self.git([
            'config', '--file',
            os.path.join(self.cache_dir, '.gitconfig'), '--add',
            'safe.bareRepository', 'explicit'
        ])

    def git(self, cmd, cwd=None):
        cwd = cwd or self.origin_dir
        git = 'git.bat' if sys.platform == 'win32' else 'git'
        subprocess.check_call([git] + cmd, cwd=cwd)

    def testParseFetchSpec(self):
        testData = [([], []),
                    (['main'], [('+refs/heads/main:refs/heads/main',
                                 r'\+refs/heads/main:.*')]),
                    (['main/'], [('+refs/heads/main:refs/heads/main',
                                  r'\+refs/heads/main:.*')]),
                    (['+main'], [('+refs/heads/main:refs/heads/main',
                                  r'\+refs/heads/main:.*')]),
                    (['master'], [('+refs/heads/master:refs/heads/master',
                                   r'\+refs/heads/master:.*')]),
                    (['master/'], [('+refs/heads/master:refs/heads/master',
                                    r'\+refs/heads/master:.*')]),
                    (['+master'], [('+refs/heads/master:refs/heads/master',
                                    r'\+refs/heads/master:.*')]),
                    (['refs/heads/*'], [('+refs/heads/*:refs/heads/*',
                                         r'\+refs/heads/\*:.*')]),
                    (['foo/bar/*',
                      'baz'], [('+refs/heads/foo/bar/*:refs/heads/foo/bar/*',
                                r'\+refs/heads/foo/bar/\*:.*'),
                               ('+refs/heads/baz:refs/heads/baz',
                                r'\+refs/heads/baz:.*')]),
                    (['refs/foo/*:refs/bar/*'], [('+refs/foo/*:refs/bar/*',
                                                  r'\+refs/foo/\*:.*')])]

        mirror = git_cache.Mirror('test://phony.example.biz')
        for fetch_specs, expected in testData:
            mirror = git_cache.Mirror('test://phony.example.biz',
                                      refs=fetch_specs)
            self.assertEqual(mirror.fetch_specs, set(expected))

    def testPopulate(self):
        self.git(['init', '-q'])
        with open(os.path.join(self.origin_dir, 'foo'), 'w') as f:
            f.write('touched\n')
        self.git(['add', 'foo'])
        self.git([
            '-c', 'user.name=Test user', '-c', 'user.email=joj@test.com',
            'commit', '-m', 'foo'
        ])

        mirror = git_cache.Mirror(self.origin_dir)
        mirror.populate()

    def testPopulateResetFetchConfig(self):
        self.git(['init', '-q'])
        with open(os.path.join(self.origin_dir, 'foo'), 'w') as f:
            f.write('touched\n')
        self.git(['add', 'foo'])
        self.git([
            '-c', 'user.name=Test user', '-c', 'user.email=joj@test.com',
            'commit', '-m', 'foo'
        ])

        mirror = git_cache.Mirror(self.origin_dir)
        mirror.populate()

        # Add a bad refspec to the cache's fetch config.
        cache_dir = os.path.join(self.cache_dir,
                                 mirror.UrlToCacheDir(self.origin_dir))
        self.git([
            '--git-dir', cache_dir, 'config', '--add', 'remote.origin.fetch',
            '+refs/heads/foo:refs/heads/foo'
        ],
                 cwd=cache_dir)

        mirror.populate(reset_fetch_config=True)

    def testPopulateTwice(self):
        self.git(['init', '-q'])
        with open(os.path.join(self.origin_dir, 'foo'), 'w') as f:
            f.write('touched\n')
        self.git(['add', 'foo'])
        self.git([
            '-c', 'user.name=Test user', '-c', 'user.email=joj@test.com',
            'commit', '-m', 'foo'
        ])

        mirror = git_cache.Mirror(self.origin_dir)
        mirror.populate()

        mirror.populate()

    def testPopulateDeletesTmpPackFiles(self):
        self.git(['init', '-q'])
        with open(os.path.join(self.origin_dir, 'foo'), 'w') as f:
            f.write('touched\n')
        self.git(['add', 'foo'])
        self.git([
            '-c', 'user.name=Test user', '-c', 'user.email=joj@test.com',
            'commit', '-m', 'foo'
        ])

        mirror = git_cache.Mirror(self.origin_dir)
        mirror.populate()

        # Create tmp pack files.
        pack_dir = os.path.join(mirror.mirror_path, 'objects', 'pack')
        os.makedirs(pack_dir, exist_ok=True)
        tmp_pack_path = os.path.join(pack_dir, 'tmp_pack_abc')
        tmp_idx_path = os.path.join(pack_dir, '.tmp-1234-pack-def.idx')
        with open(tmp_pack_path, 'w') as f:
            f.write('content')
        with open(tmp_idx_path, 'w') as f:
            f.write('idx content')

        self.assertTrue(os.path.exists(tmp_pack_path))
        self.assertTrue(os.path.exists(tmp_idx_path))

        mirror.populate()

        # The temporary files should be deleted.
        self.assertFalse(os.path.exists(tmp_pack_path))
        self.assertFalse(os.path.exists(tmp_idx_path))


    @mock.patch('sys.stdout', StringIO())
    def testPruneRequired(self):
        self.git(['init', '-q'])
        with open(os.path.join(self.origin_dir, 'foo'), 'w') as f:
            f.write('touched\n')
        self.git(['checkout', '-b', 'foo'])
        self.git(['add', 'foo'])
        self.git([
            '-c', 'user.name=Test user', '-c', 'user.email=joj@test.com',
            'commit', '-m', 'foo'
        ])
        mirror = git_cache.Mirror(self.origin_dir)
        mirror.populate()
        self.git(['checkout', '-b', 'foo_tmp', 'foo'])
        self.git(['branch', '-D', 'foo'])
        self.git(['checkout', '-b', 'foo/bar', 'foo_tmp'])
        mirror.populate()
        self.assertNotIn(git_cache.GIT_CACHE_CORRUPT_MESSAGE,
                         sys.stdout.getvalue())

    @mock.patch('sys.stdout', StringIO())
    def testBadInit(self):
        self.git(['init', '-q'])
        with open(os.path.join(self.origin_dir, 'foo'), 'w') as f:
            f.write('touched\n')
        self.git(['add', 'foo'])
        self.git([
            '-c', 'user.name=Test user', '-c', 'user.email=joj@test.com',
            'commit', '-m', 'foo'
        ])

        mirror = git_cache.Mirror(self.origin_dir)

        # Simulate init being interrupted during fetch phase.
        with mock.patch.object(mirror, '_fetch'):
            mirror.populate()

        # Corrupt message is not expected at this point since it was
        # "interrupted".
        self.assertNotIn(git_cache.GIT_CACHE_CORRUPT_MESSAGE,
                         sys.stdout.getvalue())

        # We call mirror.populate() without _fetch patched. This time, a
        # sentient file should prompt cache deletion.
        mirror.populate()
        self.assertIn(git_cache.GIT_CACHE_CORRUPT_MESSAGE,
                      sys.stdout.getvalue())

    def _makeGitRepoWithTag(self):
        self.git(['init', '-q'])
        with open(os.path.join(self.origin_dir, 'foo'), 'w') as f:
            f.write('touched\n')
        self.git(['add', 'foo'])
        self.git([
            '-c', 'user.name=Test user', '-c', 'user.email=joj@test.com',
            'commit', '-m', 'foo'
        ])
        self.git(['tag', 'TAG'])
        self.git(['pack-refs'])

    def testPopulateFetchTagsByDefault(self):
        self._makeGitRepoWithTag()

        # Default behaviour includes tags.
        mirror = git_cache.Mirror(self.origin_dir)
        mirror.populate()

        cache_dir = os.path.join(self.cache_dir,
                                 mirror.UrlToCacheDir(self.origin_dir))
        self.assertTrue(os.path.exists(cache_dir + '/refs/tags/TAG'))

    def testPopulateFetchWithoutTags(self):
        self._makeGitRepoWithTag()

        # Ask to not include tags.
        mirror = git_cache.Mirror(self.origin_dir)
        mirror.populate(no_fetch_tags=True)

        cache_dir = os.path.join(self.cache_dir,
                                 mirror.UrlToCacheDir(self.origin_dir))
        self.assertFalse(os.path.exists(cache_dir + '/refs/tags/TAG'))

    def testPopulateResetFetchConfigEmptyFetchConfig(self):
        self.git(['init', '-q'])
        with open(os.path.join(self.origin_dir, 'foo'), 'w') as f:
            f.write('touched\n')
        self.git(['add', 'foo'])
        self.git([
            '-c', 'user.name=Test user', '-c', 'user.email=joj@test.com',
            'commit', '-m', 'foo'
        ])

        mirror = git_cache.Mirror(self.origin_dir)
        mirror.populate(reset_fetch_config=True)

    @mock.patch('gclient_utils.exponential_backoff_retry')
    def testSetSymbolicRefUnknownNoLocalBranches(self, mock_retry):
        mock_retry.return_value = 'HEAD branch: (unknown)'
        mirror = git_cache.Mirror(self.origin_dir)
        with mock.patch.object(mirror, 'RunGit') as mock_rungit:
            mock_rungit.return_value = b''
            mirror._set_symbolic_ref()
            for call in mock_rungit.call_args_list:
                self.assertNotIn('symbolic-ref', call.args[0])

    @mock.patch('gclient_utils.exponential_backoff_retry')
    def testSetSymbolicRefUnknownFallsBackToKnownBranchName(self, mock_retry):
        mock_retry.return_value = 'HEAD branch: (unknown)'
        mirror = git_cache.Mirror(self.origin_dir)
        with mock.patch.object(mirror, 'RunGit') as mock_rungit:
            mock_rungit.return_value = b'feature-x\nupstream/main\n'
            mirror._set_symbolic_ref()
            mock_rungit.assert_called_with(
                ['symbolic-ref', 'HEAD', 'refs/heads/upstream/main'])

    @mock.patch('gclient_utils.exponential_backoff_retry')
    def testSetSymbolicRefUnknownFallsBackToNewestBranch(self, mock_retry):
        mock_retry.return_value = 'HEAD branch: (unknown)'
        mirror = git_cache.Mirror(self.origin_dir)
        with mock.patch.object(mirror, 'RunGit') as mock_rungit:
            mock_rungit.return_value = b'feature-x\nfeature-y\n'
            mirror._set_symbolic_ref()
            mock_rungit.assert_called_with(
                ['symbolic-ref', 'HEAD', 'refs/heads/feature-x'])

    @mock.patch('gclient_utils.exponential_backoff_retry')
    def testSetSymbolicRefKnown(self, mock_retry):
        mock_retry.return_value = 'HEAD branch: main'
        mirror = git_cache.Mirror(self.origin_dir)
        with mock.patch.object(mirror, 'RunGit') as mock_rungit:
            mirror._set_symbolic_ref()
            mock_rungit.assert_called_once_with(
                ['symbolic-ref', 'HEAD', 'refs/heads/main'])

    def _mirror_head(self, mirror):
        cache_dir = os.path.join(self.cache_dir,
                                 mirror.UrlToCacheDir(self.origin_dir))
        return subprocess.check_output(
            ['git', '--git-dir', cache_dir, 'symbolic-ref',
             'HEAD']).decode('utf-8').strip()

    def testPopulateHealsDanglingHead(self):
        # The origin renamed its default branch and its own HEAD dangles, so
        # it advertises no default branch (as happens to GitHub mirrors on
        # *.googlesource.com). The mirror must still end up with HEAD on a
        # live branch.
        self.git(['init', '-q'])
        with open(os.path.join(self.origin_dir, 'foo'), 'w') as f:
            f.write('touched\n')
        self.git(['add', 'foo'])
        self.git([
            '-c', 'user.name=Test user', '-c', 'user.email=joj@test.com',
            'commit', '-m', 'foo'
        ])
        self.git(['branch', '-m', 'upstream/main'])
        self.git(['symbolic-ref', 'HEAD', 'refs/heads/gone'])

        mirror = git_cache.Mirror(self.origin_dir)
        mirror.populate()

        self.assertEqual(self._mirror_head(mirror), 'refs/heads/upstream/main')

    def testPopulateHealsStaleHead(self):
        # The origin renamed its default branch from master to main but kept
        # the frozen master around. A mirror created before the rename has
        # HEAD on master, which still resolves, so only the staleness check
        # can notice that HEAD must be re-verified against the origin.
        self.git(['init', '-q'])
        with open(os.path.join(self.origin_dir, 'foo'), 'w') as f:
            f.write('touched\n')
        self.git(['add', 'foo'])
        old_date = '2020-01-01T00:00:00 +0000'
        env = os.environ.copy()
        env['GIT_AUTHOR_DATE'] = old_date
        env['GIT_COMMITTER_DATE'] = old_date
        subprocess.check_call([
            'git', '-c', 'user.name=Test user', '-c', 'user.email=joj@test.com',
            'commit', '-m', 'old'
        ],
                              cwd=self.origin_dir,
                              env=env)
        self.git(['branch', '-m', 'master'])
        self.git(['checkout', '-q', '-b', 'main'])
        with open(os.path.join(self.origin_dir, 'bar'), 'w') as f:
            f.write('touched\n')
        self.git(['add', 'bar'])
        self.git([
            '-c', 'user.name=Test user', '-c', 'user.email=joj@test.com',
            'commit', '-m', 'new'
        ])

        mirror = git_cache.Mirror(self.origin_dir)
        mirror.populate()
        # Simulate a mirror created before the rename.
        cache_dir = os.path.join(self.cache_dir,
                                 mirror.UrlToCacheDir(self.origin_dir))
        subprocess.check_call([
            'git', '--git-dir', cache_dir, 'symbolic-ref', 'HEAD',
            'refs/heads/master'
        ])

        mirror.populate()

        self.assertEqual(self._mirror_head(mirror), 'refs/heads/main')

    def testHeadStaleCheckIgnoresFutureDatedBranches(self):
        # A busy default branch must never look stale, even when some side
        # branch carries a bogus future committer date. Otherwise a repo
        # like chromium/src would pay the remote default-branch lookup on
        # every populate.
        self.git(['init', '-q'])
        with open(os.path.join(self.origin_dir, 'foo'), 'w') as f:
            f.write('touched\n')
        self.git(['add', 'foo'])
        self.git([
            '-c', 'user.name=Test user', '-c', 'user.email=joj@test.com',
            'commit', '-m', 'current'
        ])
        self.git(['branch', '-m', 'main'])
        self.git(['checkout', '-q', '-b', 'weird'])
        with open(os.path.join(self.origin_dir, 'bar'), 'w') as f:
            f.write('touched\n')
        self.git(['add', 'bar'])
        future_date = '2090-01-01T00:00:00 +0000'
        env = os.environ.copy()
        env['GIT_AUTHOR_DATE'] = future_date
        env['GIT_COMMITTER_DATE'] = future_date
        subprocess.check_call([
            'git', '-c', 'user.name=Test user', '-c', 'user.email=joj@test.com',
            'commit', '-m', 'future'
        ],
                              cwd=self.origin_dir,
                              env=env)
        self.git(['checkout', '-q', 'main'])

        mirror = git_cache.Mirror(self.origin_dir)
        mirror.populate()

        self.assertFalse(mirror._head_looks_stale())
        self.assertEqual(self._mirror_head(mirror), 'refs/heads/main')

    def testUpdateBootstrapFailsWhenGenNumberFails(self):
        self.git(['init', '-q'])
        with open(os.path.join(self.origin_dir, 'foo'), 'w') as f:
            f.write('touched\n')
        self.git(['add', 'foo'])
        self.git([
            '-c', 'user.name=Test user', '-c', 'user.email=joj@test.com',
            'commit', '-m', 'foo'
        ])
        mirror = git_cache.Mirror(self.origin_dir)
        mirror.populate()

        with mock.patch('subprocess.check_output',
                        side_effect=subprocess.CalledProcessError(
                            1, 'git number')):
            with mock.patch.object(mirror, '_set_symbolic_ref') as heal:
                self.assertFalse(mirror.update_bootstrap())
                heal.assert_called_once()

    def testUpdateBootstrapHealsBrokenHead(self):
        self.git(['init', '-q'])
        with open(os.path.join(self.origin_dir, 'foo'), 'w') as f:
            f.write('touched\n')
        self.git(['add', 'foo'])
        self.git([
            '-c', 'user.name=Test user', '-c', 'user.email=joj@test.com',
            'commit', '-m', 'foo'
        ])
        mirror = git_cache.Mirror(self.origin_dir)
        mirror.populate()
        cache_dir = os.path.join(self.cache_dir,
                                 mirror.UrlToCacheDir(self.origin_dir))
        # Break the mirror's HEAD the way a default-branch rename does.
        subprocess.check_call([
            'git', '--git-dir', cache_dir, 'symbolic-ref', 'HEAD',
            'refs/heads/gone'
        ])

        # Repairs HEAD like _set_symbolic_ref (tested on its own) would,
        # minus the remote lookup.
        def heal():
            branch = subprocess.check_output([
                'git', '--git-dir', cache_dir, 'for-each-ref', '--count=1',
                '--format=%(refname)', 'refs/heads'
            ]).decode().strip()
            subprocess.check_call(
                ['git', '--git-dir', cache_dir, 'symbolic-ref', 'HEAD', branch])

        real_check_output = subprocess.check_output
        gen_calls = []

        # git number fails on the broken HEAD, then succeeds after the heal.
        def fake_check_output(cmd, *args, **kwargs):
            if 'number' in cmd:
                gen_calls.append(cmd)
                if len(gen_calls) == 1:
                    raise subprocess.CalledProcessError(1, cmd)
                return b'42\n'
            return real_check_output(cmd, *args, **kwargs)

        with mock.patch('git_cache.Gsutil') as gsutil_cls:
            gsutil_cls.return_value.check_call_with_retries.return_value = (0,
                                                                            '',
                                                                            '')
            with mock.patch('subprocess.check_output',
                            side_effect=fake_check_output):
                with mock.patch.object(mirror,
                                       '_set_symbolic_ref',
                                       side_effect=heal) as heal_mock:
                    self.assertTrue(mirror.update_bootstrap())
                    heal_mock.assert_called_once()
        self.assertEqual(len(gen_calls), 2)


class GitCacheDirTest(unittest.TestCase):
    def setUp(self):
        try:
            delattr(git_cache.Mirror, 'cachepath')
        except AttributeError:
            pass
        super(GitCacheDirTest, self).setUp()

    def tearDown(self):
        try:
            delattr(git_cache.Mirror, 'cachepath')
        except AttributeError:
            pass
        super(GitCacheDirTest, self).tearDown()

    def test_git_config_read(self):
        (fd, tmpFile) = tempfile.mkstemp()
        old = git_cache.Mirror._GIT_CONFIG_LOCATION
        try:
            try:
                os.write(fd, b'[cache]\n  cachepath="hello world"\n')
            finally:
                os.close(fd)

            git_cache.Mirror._GIT_CONFIG_LOCATION = ['-f', tmpFile]

            self.assertEqual(git_cache.Mirror.GetCachePath(), 'hello world')
        finally:
            git_cache.Mirror._GIT_CONFIG_LOCATION = old
            os.remove(tmpFile)

    def test_environ_read(self):
        path = os.environ.get('GIT_CACHE_PATH')
        config = os.environ.get('GIT_CONFIG')
        try:
            os.environ['GIT_CACHE_PATH'] = 'hello world'
            os.environ['GIT_CONFIG'] = 'disabled'

            self.assertEqual(git_cache.Mirror.GetCachePath(), 'hello world')
        finally:
            for name, val in zip(('GIT_CACHE_PATH', 'GIT_CONFIG'),
                                 (path, config)):
                if val is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = val

    def test_manual_set(self):
        git_cache.Mirror.SetCachePath('hello world')
        self.assertEqual(git_cache.Mirror.GetCachePath(), 'hello world')

    def test_unconfigured(self):
        path = os.environ.get('GIT_CACHE_PATH')
        config = os.environ.get('GIT_CONFIG')
        try:
            os.environ.pop('GIT_CACHE_PATH', None)
            os.environ['GIT_CONFIG'] = 'disabled'

            with self.assertRaisesRegex(RuntimeError, 'cache\.cachepath'):
                git_cache.Mirror.GetCachePath()

            # negatively cached value still raises
            with self.assertRaisesRegex(RuntimeError, 'cache\.cachepath'):
                git_cache.Mirror.GetCachePath()
        finally:
            for name, val in zip(('GIT_CACHE_PATH', 'GIT_CONFIG'),
                                 (path, config)):
                if val is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = val


class BootstrapConcurrencyTest(unittest.TestCase):
    def test_default_tracks_sync_parallelism(self):
        with mock.patch.dict('os.environ', clear=False):
            os.environ.pop('GIT_CACHE_BOOTSTRAP_CONCURRENCY', None)
            self.assertEqual(git_cache._bootstrap_concurrency(),
                             max(8, gclient_utils.NumLocalCpus()))

    def test_override(self):
        with mock.patch.dict('os.environ',
                             {'GIT_CACHE_BOOTSTRAP_CONCURRENCY': '32'}):
            self.assertEqual(git_cache._bootstrap_concurrency(), 32)

    def test_clamped_to_at_least_one(self):
        with mock.patch.dict('os.environ',
                             {'GIT_CACHE_BOOTSTRAP_CONCURRENCY': '0'}):
            self.assertEqual(git_cache._bootstrap_concurrency(), 1)

    def test_garbage_falls_back_to_default(self):
        with mock.patch.dict('os.environ',
                             {'GIT_CACHE_BOOTSTRAP_CONCURRENCY': 'lots'}):
            self.assertEqual(git_cache._bootstrap_concurrency(),
                             max(8, gclient_utils.NumLocalCpus()))


class GetBootstrapDefaultBranchTest(unittest.TestCase):
    def setUp(self):
        self.cache_dir = tempfile.mkdtemp(prefix='gc_defbranch_')
        self.addCleanup(shutil.rmtree, self.cache_dir, ignore_errors=True)
        git_cache.Mirror.SetCachePath(self.cache_dir)

    def _patched(self, ls_lines, head='ref: refs/heads/main\n', ls_code=0):
        class _FakeGsutil:
            def __init__(self, *a, **k):
                pass

            def check_call(self, *args):
                if args[0] == 'ls':
                    return (ls_code, '\n'.join(ls_lines), '')
                if args[0] == 'cat':
                    return (0, head, '')
                return (1, '', '')

        return mock.patch.object(git_cache, 'Gsutil', _FakeGsutil)

    def test_reads_default_branch_from_snapshot_head(self):
        m = git_cache.Mirror('https://chromium.googlesource.com/foo/bar')
        gp = m._gs_path
        with self._patched(['%s/42/' % gp, '%s/42.ready' % gp]):
            self.assertEqual(m.get_bootstrap_default_branch(), 'main')

    def test_respects_non_main_default(self):
        m = git_cache.Mirror('https://chromium.googlesource.com/foo/bar')
        gp = m._gs_path
        with self._patched(['%s/7/' % gp, '%s/7.ready' % gp],
                           head='ref: refs/heads/master\n'):
            self.assertEqual(m.get_bootstrap_default_branch(), 'master')

    def test_bucket_without_snapshot_returns_none(self):
        # Supported host, but no snapshot uploaded for this repo yet.
        m = git_cache.Mirror('https://chromium.googlesource.com/foo/bar')
        with self._patched([]):
            self.assertIsNone(m.get_bootstrap_default_branch())

    def test_unsupported_host_returns_none(self):
        # Host has no bootstrap bucket at all.
        with mock.patch.dict('os.environ', clear=False):
            os.environ.pop('OVERRIDE_BOOTSTRAP_BUCKET', None)
            m = git_cache.Mirror('https://unknown.example.com/foo/bar')
            self.assertIsNone(m.get_bootstrap_default_branch())


class MirrorTest(unittest.TestCase):
    def test_same_cache_for_authenticated_and_unauthenticated_urls(self):
        # GoB can fetch a repo via two different URLs; if the url contains '/a/'
        # it forces authenticated access instead of allowing anonymous access,
        # even in the case where a repo is public. We want this in order to make
        # sure bots are authenticated and get the right quotas. However, we
        # only want to maintain a single cache for the repo.
        self.assertEqual(
            git_cache.Mirror.UrlToCacheDir(
                'https://chromium.googlesource.com/a/chromium/src.git'),
            'chromium.googlesource.com-chromium-src')

    def test_ssh_url_in_UrlToCacheDir_and_CacheDirToUrl(self):
        ssh_url = "git@github.com:chromium/chromium.git"
        self.assertEqual(git_cache.Mirror.UrlToCacheDir(ssh_url),
                         "git@github.com__chromium-chromium")
        self.assertEqual(
            git_cache.Mirror.CacheDirToUrl(
                git_cache.Mirror.UrlToCacheDir(ssh_url)), ssh_url[:-4])


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.DEBUG if '-v' in sys.argv else logging.ERROR)
    sys.exit(
        coverage_utils.covered_main(
            (os.path.join(DEPOT_TOOLS_ROOT, 'git_cache.py')),
            required_percentage=0))
