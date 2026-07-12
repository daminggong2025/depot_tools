#!/usr/bin/env vpython3
# Copyright (c) 2026 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import setup_color


@unittest.skipIf(sys.platform.startswith('win'),
                 'tests exercise the POSIX branch of setup_color.init()')
class SetupColorTest(unittest.TestCase):

    def _init(self, isatty, env):
        """Runs setup_color.init() with a fake stdout and environment.

        Returns (IS_TTY, OUT_TYPE, colorama_init_mock) as seen after init().
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
                mock.patch.object(setup_color.colorama,
                                  'init') as colorama_init:
            setup_color.init()
        return setup_color.IS_TTY, setup_color.OUT_TYPE, colorama_init

    def testDefaultTty(self):
        is_tty, out_type, colorama_init = self._init(isatty=True, env={})
        self.assertTrue(is_tty)
        self.assertEqual(out_type, 'console')
        colorama_init.assert_called_once_with(wrap=True)

    def testDefaultNonTty(self):
        is_tty, out_type, colorama_init = self._init(isatty=False, env={})
        self.assertFalse(is_tty)
        self.assertEqual(out_type, 'pipe or file')
        colorama_init.assert_called_once_with(wrap=False)

    def testNoColorNonTty(self):
        is_tty, _, colorama_init = self._init(isatty=False,
                                              env={'NO_COLOR': '1'})
        self.assertFalse(is_tty)
        colorama_init.assert_called_once_with(strip=True)

    def testNoColorTty(self):
        is_tty, _, colorama_init = self._init(isatty=True,
                                              env={'NO_COLOR': '1'})
        self.assertFalse(is_tty)
        colorama_init.assert_called_once_with(strip=True)

    def testNoColorEmptyIsIgnored(self):
        is_tty, _, _ = self._init(isatty=True, env={'NO_COLOR': ''})
        self.assertTrue(is_tty)

    def testClicolorForceNonTty(self):
        is_tty, _, colorama_init = self._init(isatty=False,
                                              env={'CLICOLOR_FORCE': '1'})
        self.assertTrue(is_tty)
        # Color codes must pass through to the pipe unmodified.
        colorama_init.assert_called_once_with(wrap=False)

    def testClicolorForceZeroDoesNotForce(self):
        is_tty, _, _ = self._init(isatty=False, env={'CLICOLOR_FORCE': '0'})
        self.assertFalse(is_tty)

    def testClicolorForceEmptyDoesNotForce(self):
        is_tty, _, _ = self._init(isatty=False, env={'CLICOLOR_FORCE': ''})
        self.assertFalse(is_tty)

    def testNoColorWinsOverClicolorForce(self):
        is_tty, _, _ = self._init(isatty=False,
                                  env={
                                      'NO_COLOR': '1',
                                      'CLICOLOR_FORCE': '1'
                                  })
        self.assertFalse(is_tty)


if __name__ == '__main__':
    unittest.main()
