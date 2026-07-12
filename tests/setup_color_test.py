#!/usr/bin/env vpython3
# Copyright (c) 2026 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import io
import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import setup_color

# Sample output of a tool that emits ANSI color codes without checking
# IS_TTY first, and the same output with the codes stripped.
_COLORED = 'a\x1b[31mb\x1b[0mc'
_PLAIN = 'abc'


@unittest.skipIf(sys.platform.startswith('win'),
                 'tests exercise the POSIX branch of setup_color.init()')
class SetupColorTest(unittest.TestCase):

    def _init(self, isatty, env):
        """Runs setup_color.init() with a fake stdout and environment.

        Returns (IS_TTY, OUT_TYPE, written) as seen after init(), where
        written is what arrives at the underlying stdout after writing
        _COLORED to sys.stdout.
        """
        environ = {
            k: v
            for k, v in os.environ.items()
            if k not in ('NO_COLOR', 'CLICOLOR_FORCE')
        }
        environ.update(env)
        fake_stdout = mock.Mock()
        fake_stdout.isatty.return_value = isatty
        with mock.patch.dict(os.environ, environ, clear=True), \
                mock.patch('sys.stdout', fake_stdout), \
                mock.patch('sys.stderr', mock.Mock()):
            setup_color.init()
            sys.stdout.write(_COLORED)
        written = ''.join(c.args[0] for c in fake_stdout.write.call_args_list)
        return setup_color.IS_TTY, setup_color.OUT_TYPE, written

    def testDefaultTty(self):
        is_tty, out_type, written = self._init(isatty=True, env={})
        self.assertTrue(is_tty)
        self.assertEqual(out_type, 'console')
        self.assertEqual(written, _COLORED)

    def testDefaultNonTty(self):
        is_tty, out_type, written = self._init(isatty=False, env={})
        self.assertFalse(is_tty)
        self.assertEqual(out_type, 'pipe or file')
        # Color codes pass through to the pipe unmodified.
        self.assertEqual(written, _COLORED)

    def testNoColorNonTty(self):
        is_tty, _, written = self._init(isatty=False, env={'NO_COLOR': '1'})
        self.assertFalse(is_tty)
        self.assertEqual(written, _PLAIN)

    def testNoColorTty(self):
        is_tty, _, written = self._init(isatty=True, env={'NO_COLOR': '1'})
        self.assertFalse(is_tty)
        self.assertEqual(written, _PLAIN)

    def testNoColorEmptyIsIgnored(self):
        is_tty, _, written = self._init(isatty=True, env={'NO_COLOR': ''})
        self.assertTrue(is_tty)
        self.assertEqual(written, _COLORED)

    def testClicolorForceNonTty(self):
        is_tty, _, written = self._init(isatty=False,
                                        env={'CLICOLOR_FORCE': '1'})
        self.assertTrue(is_tty)
        # Color codes must pass through to the pipe unmodified.
        self.assertEqual(written, _COLORED)

    def testClicolorForceZeroDoesNotForce(self):
        is_tty, _, _ = self._init(isatty=False, env={'CLICOLOR_FORCE': '0'})
        self.assertFalse(is_tty)

    def testClicolorForceEmptyDoesNotForce(self):
        is_tty, _, _ = self._init(isatty=False, env={'CLICOLOR_FORCE': ''})
        self.assertFalse(is_tty)

    def testNoColorWinsOverClicolorForce(self):
        is_tty, _, written = self._init(isatty=False,
                                        env={
                                            'NO_COLOR': '1',
                                            'CLICOLOR_FORCE': '1'
                                        })
        self.assertFalse(is_tty)
        self.assertEqual(written, _PLAIN)


class AnsiCodesTest(unittest.TestCase):

    def testAnsiSequences(self):
        self.assertEqual(setup_color.Fore.RED, '\x1b[31m')
        self.assertEqual(setup_color.Fore.RESET, '\x1b[39m')
        self.assertEqual(setup_color.Back.BLUE, '\x1b[44m')
        self.assertEqual(setup_color.Style.BRIGHT, '\x1b[1m')
        self.assertEqual(setup_color.Style.RESET_ALL, '\x1b[0m')


class StripAnsiWriterTest(unittest.TestCase):

    def testStripsCsiAndOscSequences(self):
        stream = io.StringIO()
        writer = setup_color.StripAnsiWriter(stream)
        writer.write('\x1b]0;title\x07a\x1b[31;1mb\x1b[2Kc\x1b[0m\n')
        self.assertEqual(stream.getvalue(), 'abc\n')

    def testProxiesOtherAttributes(self):
        stream = io.StringIO()
        writer = setup_color.StripAnsiWriter(stream)
        self.assertFalse(writer.isatty())
        writer.flush()


if __name__ == '__main__':
    unittest.main()
