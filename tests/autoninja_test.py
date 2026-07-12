#!/usr/bin/env vpython3
# Copyright (c) 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import contextlib
import io
import multiprocessing
import os
import os.path
import sys
import unittest
from unittest import mock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

import autoninja
from testing_support import trial_dir


def write(filename, content):
    """Writes the content of a file and create the directories as needed."""
    filename = os.path.abspath(filename)
    dirname = os.path.dirname(filename)
    if not os.path.isdir(dirname):
        os.makedirs(dirname)
    with open(filename, 'w') as f:
        f.write(content)


class AutoninjaTest(trial_dir.TestCase):
    def setUp(self):
        super(AutoninjaTest, self).setUp()
        self.previous_dir = os.getcwd()
        os.chdir(self.root_dir)

    def tearDown(self):
        os.chdir(self.previous_dir)
        super(AutoninjaTest, self).tearDown()

    @mock.patch('subprocess.Popen')
    def test_upload_ninjalog(self, mock_popen):
        with mock.patch('metrics_utils.get_edit_monitor_state',
                        return_value='control'):
            autoninja._upload_ninjalog(['autoninja.py', 'arg1', 'arg2'], 0, 10)
            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            self.assertIn('--cmdline', cmd)
            self.assertEqual(cmd[cmd.index('--edit_monitor_state'):], [
                '--edit_monitor_state', 'control', '--cmdline', 'arg1', 'arg2'
            ])

    @mock.patch('subprocess.Popen')
    def test_upload_ninjalog_with_edit_monitor(self, mock_popen):
        with mock.patch('metrics_utils.get_edit_monitor_state',
                        return_value='enabled'):
            autoninja._upload_ninjalog(['autoninja.py', 'arg1', 'arg2'], 0, 10)
            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            self.assertEqual(cmd[cmd.index('--edit_monitor_state'):], [
                '--edit_monitor_state', 'enabled', '--cmdline', 'arg1', 'arg2'
            ])

    def test_autoninja(self):
        """Test that by default (= no GN args) autoninja delegates to ninja."""
        with mock.patch('ninja.main', return_value=0) as ninja_main:
            out_dir = os.path.join('out', 'dir')
            write(os.path.join(out_dir, 'args.gn'), '')
            autoninja.main(['autoninja.py', '-C', out_dir])
            ninja_main.assert_called_once()
            args = ninja_main.call_args.args[0]
        self.assertIn('-C', args)
        self.assertEqual(args[args.index('-C') + 1], out_dir)

    @mock.patch('sys.platform', 'win32')
    def test_autoninja_splits_args_on_windows(self):
        """
        Test that autoninja correctly handles the special case of being
        passed its arguments as a quoted, whitespace-delimited string on
        Windows.
        """
        with mock.patch('ninja.main', return_value=0) as ninja_main:
            out_dir = os.path.join('out', 'dir')
            write(os.path.join(out_dir, 'args.gn'), '')
            autoninja.main([
                'autoninja.py',
                '-C {} base'.format(out_dir),
            ])
            ninja_main.assert_called_once()
            args = ninja_main.call_args.args[0]
        self.assertIn('-C', args)
        self.assertEqual(args[args.index('-C') + 1], out_dir)
        self.assertIn('base', args)

    def test_autoninja_reclient(self):
        """
        Test that when specifying use_remoteexec=true use_reclient=true,
        autoninja delegates to reclient_helper.
        """
        with mock.patch('reclient_helper.run_ninja',
                        return_value=0) as run_ninja:
            out_dir = os.path.join('out', 'dir')
            write(
                os.path.join(out_dir, 'args.gn'), """
                  use_remoteexec=true
                  use_reclient=true
                  """)
            write(os.path.join('buildtools', 'reclient_cfgs', 'reproxy.cfg'),
                  'RBE_v=2')
            write(os.path.join('buildtools', 'reclient', 'version.txt'), '0.0')
            write(
                os.path.join('buildtools', 'reclient_cfgs', 'reproxy.cfg'),
                'instance=projects/rbe-chromium-untrusted-test/'
                'instances/default_instance')
            autoninja.main(['autoninja.py', '-C', out_dir])
            run_ninja.assert_called_once()
            args = run_ninja.call_args.args[0]
        self.assertIn('-C', args)
        self.assertEqual(args[args.index('-C') + 1], out_dir)
        # Check that autoninja correctly calculated the number of jobs to use
        # as required for remote execution, instead of using the value for
        # local execution.
        self.assertIn('-j', args)
        parallel_j = int(args[args.index('-j') + 1])
        self.assertGreater(parallel_j, multiprocessing.cpu_count() * 2)

    def test_reclient_rbe_project_from_env(self):
        """
        Test that the RBE_instance environment variable overrides the
        project parsed from reproxy.cfg.
        """
        write(os.path.join('buildtools', 'reclient_cfgs', 'reproxy.cfg'),
              'instance=projects/cfg-project/instances/default_instance')
        with mock.patch.dict(
                os.environ,
            {'RBE_instance': 'projects/env-project/instances/default_instance'
             }):
            self.assertEqual(autoninja._reclient_rbe_project(), 'env-project')

    def test_reclient_rbe_project_from_env_unexpected_format(self):
        """
        Test that an RBE_instance value in an unexpected format does not
        crash and falls back to the project from reproxy.cfg.
        """
        write(os.path.join('buildtools', 'reclient_cfgs', 'reproxy.cfg'),
              'instance=projects/cfg-project/instances/default_instance')
        with mock.patch.dict(os.environ, {'RBE_instance': 'projects/'}):
            self.assertEqual(autoninja._reclient_rbe_project(), 'cfg-project')

    def test_autoninja_siso(self):
        """
        Test that when specifying use_siso=true, autoninja delegates to siso.
        """
        with mock.patch('siso.main', return_value=0) as siso_main:
            out_dir = os.path.join('out', 'dir')
            write(os.path.join(out_dir, 'args.gn'), 'use_siso=true')
            write(os.path.join('build', 'config', 'siso', '.sisoenv'),
                  'SISO_PROJECT=rbe-chromium-untrusted-test')
            autoninja.main(['autoninja.py', '-C', out_dir])
            siso_main.assert_called_once()
            args = siso_main.call_args.args[0]
        self.assertIn('-C', args)
        self.assertEqual(args[args.index('-C') + 1], out_dir)

    def test_autoninja_siso_reclient(self):
        """
        Test that when specifying use_siso=true and use_remoteexec=true,
        autoninja starts reproxy using reclient_helper and calls 'siso ninja'.
        """
        reclient_helper_calls = []

        @contextlib.contextmanager
        def reclient_helper_mock(argv, tool, _should_collect_logs):
            reclient_helper_calls.append([argv, tool])
            yield 0

        with mock.patch('reclient_helper.build_context', reclient_helper_mock):
            with mock.patch('siso.main', return_value=0) as siso_main:
                out_dir = os.path.join('out', 'dir')
                write(
                    os.path.join(out_dir, 'args.gn'), """
                      use_remoteexec=true
                      use_siso=true
                      use_reclient=true
                      """)
                write(
                    os.path.join('buildtools', 'reclient_cfgs', 'reproxy.cfg'),
                    'instance=projects/rbe-chromium-untrusted-test/'
                    'instances/default_instance\n'
                    'RBE_v=2')
                write(os.path.join('buildtools', 'reclient', 'version.txt'),
                      '0.0')
                autoninja.main(['autoninja.py', '-C', out_dir])
                siso_main.assert_called_once_with([
                    'siso', 'ninja', '-project=', '-reapi_instance=',
                    '-reapi_address=', '-C', out_dir
                ])
        self.assertEqual(len(reclient_helper_calls), 1)
        self.assertEqual(reclient_helper_calls[0][0], [
            'siso', 'ninja', '-project=', '-reapi_instance=', '-reapi_address=',
            '-C', out_dir
        ])
        self.assertEqual(reclient_helper_calls[0][1], 'autosiso')

    def test_collect_log_paths(self):
        """
        Test that _collect_log_paths returns only existing log files and
        resolves the newest reproxy log directory.
        """
        out_dir = os.path.join('out', 'dir')
        write(os.path.join(out_dir, '.ninja_log'), '')
        write(os.path.join(out_dir, 'siso.INFO'), '')
        write(os.path.join(out_dir, 'siso_output'), '')
        reproxy_logs = os.path.join(out_dir, '.reproxy_tmp', 'logs')
        old_run = os.path.join(reproxy_logs,
                               '20240101T000000.000000_build-id-1')
        new_run = os.path.join(reproxy_logs,
                               '20250101T000000.000000_build-id-2')
        write(os.path.join(old_run, 'reproxy.INFO'), '')
        write(os.path.join(new_run, 'reproxy.INFO'), '')
        log_paths = autoninja._collect_log_paths(out_dir)
        self.assertIn(os.path.join(out_dir, '.ninja_log'), log_paths)
        self.assertIn(os.path.join(out_dir, 'siso.INFO'), log_paths)
        self.assertIn(os.path.join(out_dir, 'siso_output'), log_paths)
        self.assertNotIn(os.path.join(out_dir, 'siso_localexec'), log_paths)
        self.assertNotIn(os.path.join(out_dir, 'siso_metrics.json'), log_paths)
        self.assertIn(new_run, log_paths)
        self.assertNotIn(old_run, log_paths)

    def test_collect_log_paths_no_logs(self):
        """Test that _collect_log_paths returns nothing without log files."""
        out_dir = os.path.join('out', 'dir')
        write(os.path.join(out_dir, 'args.gn'), '')
        self.assertEqual(autoninja._collect_log_paths(out_dir), [])

    def test_autoninja_show_logs(self):
        """Test that --show-logs prints the log files and does not build."""
        out_dir = os.path.join('out', 'dir')
        write(os.path.join(out_dir, 'args.gn'), '')
        write(os.path.join(out_dir, 'siso.INFO'), '')
        with mock.patch('ninja.main', return_value=0) as ninja_main, \
                mock.patch('siso.main', return_value=0) as siso_main, \
                contextlib.redirect_stdout(io.StringIO()) as f:
            exit_code = autoninja.main(
                ['autoninja.py', '-C', out_dir, '--show-logs'])
        self.assertEqual(exit_code, 0)
        ninja_main.assert_not_called()
        siso_main.assert_not_called()
        self.assertIn(os.path.join(out_dir, 'siso.INFO'), f.getvalue())

    def test_autoninja_prints_logs_on_failure(self):
        """Test that a failed build prints existing log paths to stderr."""
        out_dir = os.path.join('out', 'dir')
        write(os.path.join(out_dir, 'args.gn'), '')
        write(os.path.join(out_dir, '.ninja_log'), '')
        with mock.patch('ninja.main', return_value=1), \
                contextlib.redirect_stderr(io.StringIO()) as f:
            exit_code = autoninja.main(['autoninja.py', '-C', out_dir])
        self.assertEqual(exit_code, 1)
        self.assertIn('Build logs:', f.getvalue())
        self.assertIn(os.path.join(out_dir, '.ninja_log'), f.getvalue())

    def test_autoninja_no_logs_on_success(self):
        """Test that a successful build does not print log paths."""
        out_dir = os.path.join('out', 'dir')
        write(os.path.join(out_dir, 'args.gn'), '')
        write(os.path.join(out_dir, '.ninja_log'), '')
        with mock.patch('ninja.main', return_value=0), \
                contextlib.redirect_stderr(io.StringIO()) as f:
            exit_code = autoninja.main(['autoninja.py', '-C', out_dir])
        self.assertEqual(exit_code, 0)
        self.assertNotIn('Build logs:', f.getvalue())

    @mock.patch('sys.platform', 'win32')
    def test_print_cmd_windows(self):
        args = [
            'C:\\Program Files\\Python 3\\bin\\python3.exe', 'ninja.py', '-C',
            'out\\direc tory\\',
            '../../base/types/expected_macros_unittest.cc^', '-j', '140'
        ]
        with contextlib.redirect_stderr(io.StringIO()) as f:
            autoninja._print_cmd(args)
            self.assertEqual(
                f.getvalue(),
                '"C:\\Program Files\\Python 3\\bin\\python3.exe" ninja.py -C ' +
                '"out\\direc tory\\" ' +
                '../../base/types/expected_macros_unittest.cc^^ -j 140\n')

    @mock.patch('sys.platform', 'linux')
    def test_print_cmd_linux(self):
        args = [
            '/home/user name/bin/python3', 'ninja.py', '-C', 'out/direc tory/',
            '../../base/types/expected_macros_unittest.cc^', '-j', '140'
        ]
        with contextlib.redirect_stderr(io.StringIO()) as f:
            autoninja._print_cmd(args)
            self.assertEqual(
                f.getvalue(),
                "'/home/user name/bin/python3' ninja.py -C 'out/direc tory/' " +
                "'../../base/types/expected_macros_unittest.cc^' -j 140\n")


if __name__ == '__main__':
    unittest.main()
