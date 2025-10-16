#!/usr/bin/env python3
# coding=utf-8
# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for deps_format.py."""

import io
import os
import sys
import tempfile
import unittest
from unittest import mock
import shutil
import difflib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deps_format
import gclient_utils
import setup_color
from third_party import colorama


class DepsFormatTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self._top_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._top_dir)

        self.mock_is_tty = mock.patch('deps_format.setup_color.IS_TTY',
                                      return_value=False).start()

        mock.patch('third_party.colorama.Fore.CYAN', new='<header>').start()
        mock.patch('third_party.colorama.Fore.GREEN', new='<add>').start()
        mock.patch('third_party.colorama.Fore.RED', new='<del>').start()
        mock.patch('third_party.colorama.Style.RESET_ALL',
                   new='<reset>').start()

        self.addCleanup(mock.patch.stopall)

    def _make_temp_file(self, fname, contents):
        dir_path = os.path.dirname(fname)
        if dir_path:
            os.makedirs(os.path.join(self._top_dir, dir_path), exist_ok=True)
        abs_path = os.path.join(self._top_dir, fname)
        gclient_utils.FileWrite(abs_path, ('\n'.join(contents)))
        return abs_path

    def _run_format_content_test(self, initial_content_list,
                                 expected_content_list):
        print("\n--- Running Format Content Test ---")
        initial_content = '\n'.join(initial_content_list)
        expected_content = '\n'.join(expected_content_list)

        print("--- Initial Content ---")
        print(initial_content)

        print("--- Expected Content ---")
        print(expected_content)

        formatted_content = deps_format._FormatDepsContent(initial_content)

        print("--- Formatted Content ---")
        print(formatted_content)

        if formatted_content != expected_content:
            print("--- DIFF (Expected vs. Formatted) ---")
            diff = difflib.unified_diff(
                expected_content.splitlines(True),
                formatted_content.splitlines(True),
                fromfile="expected",
                tofile="formatted",
            )
            sys.stdout.writelines(diff)
        else:
            print("--- No difference between Expected and Formatted ---")

        self.assertEqual(formatted_content, expected_content)
        print("--- Test Assertion Passed ---")

    def testFormatDepsContent_SimpleSorting(self):
        print("\n=== testFormatDepsContent_SimpleSorting ===")
        initial = [
            "include_rules = [",
            "  '+b',",
            "  '+a',",
            "]",
            "skip_child_includes = [",
            "  '-z',",
            "  '-y',",
            "]",
        ]
        expected = [
            "include_rules = [",
            "  '+a',",
            "  '+b',",
            "]",
            "skip_child_includes = [",
            "  '-y',",
            "  '-z',",
            "]",
        ]
        self._run_format_content_test(initial, expected)

    def testFormatDepsContent_WithComments(self):
        print("\n=== testFormatDepsContent_WithComments ===")
        initial = [
            "include_rules = [",
            "  # Comment for b",
            "  '+b',  # Trailing for b",
            "  '+a',",
            "  # Final comment",
            "]",
        ]
        expected = [
            "include_rules = [",
            "  '+a',",
            "  # Comment for b",
            "  '+b',  # Trailing for b",
            "  # Final comment",
            "]",
        ]
        self._run_format_content_test(initial, expected)

    def testFormatDepsContent_MultiItemsPerLine(self):
        print("\n=== testFormatDepsContent_MultiItemsPerLine ===")
        initial = [
            "include_rules = ['+c', '+b', '+a']",
        ]
        expected = [
            "include_rules = [",
            "  '+a',",
            "  '+b',",
            "  '+c',",
            "]",
        ]
        self._run_format_content_test(initial, expected)

    def testFormatDepsContent_BailOutComplex(self):
        print("\n=== testFormatDepsContent_BailOutComplex ===")
        initial = [
            "include_rules = [",
            "  '+a',",
            "  Var('b'),",
            "]",
        ]
        self._run_format_content_test(initial, initial)

    def testFormatDepsContent_BailOutImplicitConcat(self):
        print("\n=== testFormatDepsContent_BailOutImplicitConcat ===")
        initial = [
            "include_rules = [",
            "  '+a'"
            "  '+b',",
            "]",
        ]
        self._run_format_content_test(initial, initial)

    def testFormatDepsContent_EmptyList(self):
        print("\n=== testFormatDepsContent_EmptyList ===")
        initial = ["include_rules = []"]
        self._run_format_content_test(initial, initial)

    def testFormatDepsContent_EmptyListWithWhitespace(self):
        print("\n=== testFormatDepsContent_EmptyListWithWhitespace ===")
        initial = ["include_rules = [  \n\n ]"]
        expected = ["include_rules = []"]
        self._run_format_content_test(initial, expected)

    def testFormatDepsContent_NoOp(self):
        print("\n=== testFormatDepsContent_NoOp ===")
        initial = [
            "include_rules = [",
            "  '+a',",
            "  '+b',",
            "]",
        ]
        self._run_format_content_test(initial, initial)

    @mock.patch('sys.stdout', new_callable=io.StringIO)
    def testPrintDiff_NoColor(self, mock_stdout):
        print("\n=== testPrintDiff_NoColor ===")
        self.mock_is_tty.return_value = False

        original = "a\nb\nc"
        formatted = "a\nX\nc"
        print("--- Testing No-Color Diff Output ---")

        with mock.patch('third_party.colorama.Fore.CYAN', new=''), \
             mock.patch('third_party.colorama.Fore.GREEN', new=''), \
             mock.patch('third_party.colorama.Fore.RED', new=''), \
             mock.patch('third_party.colorama.Style.RESET_ALL', new=''):
            deps_format._PrintDiff(original, formatted, "test.deps")

        output = mock_stdout.getvalue()
        print("--- Captured stdout ---")
        print(output)

        self.assertIn("--- a/test.deps", output)
        self.assertIn("+++ b/test.deps", output)
        self.assertIn("-b", output)
        self.assertIn("+X", output)

        self.assertNotIn("<header>", output)
        self.assertNotIn("<add>", output)
        self.assertNotIn("<del>", output)
        self.assertNotIn("<reset>", output)
        print("--- Assertions Passed ---")

    def _run_main_test(self, args, initial_content, expected_content,
                       expected_rc):
        rel_path = "DEPS"
        if 'NONEXISTENT_DEPS' in args:
            print(f"--- Running main test for non-existent file: {args} ---")
        else:
            print(f"--- Running main test for: {args} ---")
            self._make_temp_file(rel_path, initial_content)

        print(
            "WARNING: _run_main_test is not fully implemented in the provided snippet."
        )


if __name__ == '__main__':
    unittest.main()
