#!/usr/bin/env vpython3
# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import contextlib
import io
import optparse
import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import subcommand


class SubcommandTest(unittest.TestCase):
    def setUp(self):
        super(SubcommandTest, self).setUp()
        self.module = mock.Mock(__doc__='Module documentation')
        self.parser = mock.Mock()
        self.sc = subcommand.CommandDispatcher(__name__)
        self.sc.module = self.module

    def testEnumerateCommands(self):
        self.module.CMDfoo_bar = object()
        self.module.CMDbaz = object()
        self.module.CMDaBcDeF_0123 = object()

        expected = {
            'foo-bar': self.module.CMDfoo_bar,
            'baz': self.module.CMDbaz,
            'aBcDeF-0123': self.module.CMDaBcDeF_0123,
            'help': subcommand.CMDhelp,
        }
        self.assertEqual(expected, self.sc.enumerate_commands())

    def testEnumerateCommands_CustomHelp(self):
        self.module.CMDhelp = object()
        self.assertEqual({'help': self.module.CMDhelp},
                         self.sc.enumerate_commands())

    def testFindNearestCommand_ExactMatch(self):
        self.module.CMDfoo = object()
        self.assertEqual(self.module.CMDfoo,
                         self.sc.find_nearest_command('foo'))

    def testFindNearestCommand_UniquePrefix(self):
        self.module.CMDfoo = object()
        self.module.CMDbar = object()
        self.module.CMDunique_prefix = object()
        self.assertEqual(self.module.CMDunique_prefix,
                         self.sc.find_nearest_command('unique-pre'))

    def testFindNearestCommand_NonUniquePrefix(self):
        self.module.CMDprefix1 = object()
        self.module.CMDprefix2 = object()
        self.assertIsNone(self.sc.find_nearest_command('prefix'))

    def testFindNearestCommand_CloseEnough(self):
        self.module.CMDfoo = object()
        self.module.CMDbar = object()
        self.module.CMDclose_enough = object()
        self.assertEqual(self.module.CMDclose_enough,
                         self.sc.find_nearest_command('clos-enough'))

    def testFindNearestCommand_TooManyCloseEnough(self):
        self.module.CMDcase_enough = object()
        self.module.CMDclose_enough = object()
        self.assertIsNone(self.sc.find_nearest_command('clase-enough'))

    def testFindNearestCommand_ClosestIsNotCloseEnough(self):
        self.module.CMDfoo = object()
        self.module.CMDbar = object()
        self.module.CMDnot_close_enough = object()
        self.assertIsNone(self.sc.find_nearest_command('clos-enof'))

    def _setUpTestCommands(self):
        self.module.CMDhelp = mock.Mock(__name__='CMDhelp',
                                        __doc__='help documentation',
                                        usage_more=None,
                                        return_value=0)
        self.module.CMDfoo = mock.Mock(__name__='CMDfoo',
                                       __doc__='foo documentation',
                                       usage_more='usage more',
                                       epilog='epilog',
                                       return_value=123)
        self.module.CMDbar_baz = mock.Mock(__name__='CMDbar_baz',
                                           __doc__='bar-baz documentation',
                                           usage_more=None,
                                           epilog=None,
                                           return_value=0)

    def testExecute(self):
        self._setUpTestCommands()

        self.assertEqual(
            123, self.sc.execute(self.parser, ['foo', '--bar', '--baz']))
        self.module.CMDfoo.assert_called_once_with(self.parser,
                                                   ['--bar', '--baz'])
        self.assertEqual('foo documentation\n\n', self.parser.description)
        self.assertEqual('\nepilog\n', self.parser.epilog)
        self.parser.set_usage.assert_called_once_with(
            'usage: %prog foo [options] usage more')

    def testExecute_Help(self):
        self._setUpTestCommands()

        self.assertEqual(0, self.sc.execute(self.parser, ['--help']))
        self.module.CMDhelp.assert_called_once_with(self.parser, [])
        self.assertEqual(
            'Module documentation\n\n'
            'Commands are:\n'
            '  bar-baz bar-baz documentation\n'
            '  foo     foo documentation\n'
            '  help    help documentation\n', self.parser.description)
        self.parser.set_usage.assert_called_once_with(
            'usage: %prog <command> [options]')

    def testExecute_CommandHelp(self):
        self._setUpTestCommands()

        self.assertEqual(0, self.sc.execute(self.parser, ['help', 'bar-baz']))
        self.module.CMDbar_baz.assert_called_once_with(self.parser, ['--help'])
        self.assertEqual('bar-baz documentation\n\n', self.parser.description)
        self.parser.set_usage.assert_called_once_with(
            'usage: %prog bar-baz [options]')

    def testExecute_CommandNotFound(self):
        self._setUpTestCommands()

        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                self.sc.execute(self.parser, ['not-found'])
        self.assertEqual(2, cm.exception.code)
        self.module.CMDhelp.assert_called_once_with(self.parser, [])
        self.assertEqual(
            'Module documentation\n\n'
            'Commands are:\n'
            '  bar-baz bar-baz documentation\n'
            '  foo     foo documentation\n'
            '  help    help documentation\n', self.parser.description)
        self.parser.set_usage.assert_called_once_with(
            'usage: %prog <command> [options]')

    def testExecute_CommandNotFoundAndHelpDisabled(self):
        self._setUpTestCommands()
        self.module.CMDhelp = None

        self.assertEqual(2, self.sc.execute(self.parser, ['not-found']))

    def _setUpRealCommands(self):
        """Sets up real commands so the default CMDhelp is used unmocked."""

        def CMDfoo(parser, args):
            """foo documentation."""
            return 0

        def CMDbar(parser, args):
            """bar documentation."""
            return 0

        self.module.CMDfoo = CMDfoo
        self.module.CMDbar = CMDbar

    def _executeReal(self, args):
        """Runs execute() with a real parser, capturing output and exit."""
        parser = optparse.OptionParser()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            try:
                code = self.sc.execute(parser, args)
            except SystemExit as e:
                code = e.code
        return code, stdout.getvalue(), stderr.getvalue()

    def testExecute_UnknownCommandExitsWithUsageError(self):
        self._setUpRealCommands()

        for args in (['thisisnotacommand'], ['--badflag']):
            with self.subTest(args=args):
                code, stdout, stderr = self._executeReal(args)
                self.assertEqual(2, code)
                self.assertIn("unknown command '%s'" % args[0], stderr)
                self.assertIn('Commands are:', stdout)

    def testExecute_ExplicitHelpExitsZero(self):
        self._setUpRealCommands()

        for args in (['help'], ['--help'], ['-h']):
            with self.subTest(args=args):
                code, stdout, stderr = self._executeReal(args)
                self.assertFalse(code)
                self.assertEqual('', stderr)
                self.assertIn('Commands are:', stdout)


if __name__ == '__main__':
    unittest.main()
