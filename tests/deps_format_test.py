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

# Add the parent directory to the path so we can import the modules.
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

        # Mock TTY detection for consistent color output behavior in tests.
        # Default to False (no color).
        self.mock_is_tty = mock.patch('deps_format.setup_color.IS_TTY',
                                      return_value=False).start()

        # Mock colorama constants so we can assert their usage if color is enabled.
        mock.patch('third_party.colorama.Fore.CYAN', new='<header>').start()
        mock.patch('third_party.colorama.Fore.GREEN', new='<add>').start()
        mock.patch('third_party.colorama.Fore.RED', new='<del>').start()
        mock.patch('third_party.colorama.Style.RESET_ALL',
                   new='<reset>').start()

        self.addCleanup(mock.patch.stopall)

    def _make_temp_file(self, fname, contents):
        """Helper to create a temporary file in the test directory."""
        dir_path = os.path.dirname(fname)
        if dir_path:
            os.makedirs(os.path.join(self._top_dir, dir_path), exist_ok=True)
        abs_path = os.path.join(self._top_dir, fname)
        # Use gclient_utils.FileWrite for robust file writing.
        gclient_utils.FileWrite(abs_path, ('\n'.join(contents)))
        return abs_path

    # --------------------------------------------------------------------------
    # Helper for testing _FormatDepsContent (Core Logic)
    # --------------------------------------------------------------------------

    def _run_format_content_test(self, initial_content_list,
                                 expected_content_list):
        """Tests the core formatting logic on string content."""
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

        # If the test fails, print a diff for easier debugging.
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

    # --------------------------------------------------------------------------
    # Tests for _FormatDepsContent (Core Logic)
    # --------------------------------------------------------------------------

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
        # Should bail out and return the original content.
        self._run_format_content_test(initial, initial)

    def testFormatDepsContent_BailOutImplicitConcat(self):
        print("\n=== testFormatDepsContent_BailOutImplicitConcat ===")
        initial = [
            "include_rules = [",
            "  '+a'"
            "  '+b',",
            "]",
        ]
        # Should bail out and return the original content.
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

    # --------------------------------------------------------------------------
    # Tests for _PrintDiff
    # --------------------------------------------------------------------------

    @mock.patch('sys.stdout', new_callable=io.StringIO)
    def testPrintDiff_NoColor(self, mock_stdout):
        print("\n=== testPrintDiff_NoColor ===")
        self.mock_is_tty.return_value = False

        original = "a\nb\nc"
        formatted = "a\nX\nc"
        print("--- Testing No-Color Diff Output ---")

        # Ensure colorama mocks return empty strings when IS_TTY is False.
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

        # Verify no color codes (mocked as <tag>) are present.
        self.assertNotIn("<header>", output)
        self.assertNotIn("<add>", output)
        self.assertNotIn("<del>", output)
        self.assertNotIn("<reset>", output)
        print("--- Assertions Passed ---")

    @mock.patch('sys.stdout', new_callable=io.StringIO)
    def testPrintDiff_WithColor(self, mock_stdout):
        print("\n=== testPrintDiff_WithColor ===")
        # Enable color output for this test.
        self.mock_is_tty.return_value = True

        original = "a\nb\nc"
        formatted = "a\nX\nc"
        print("--- Testing Colored Diff Output ---")

        # This will use the mocked color constants defined in setUp.
        deps_format._PrintDiff(original, formatted, "test.deps")

        output = mock_stdout.getvalue()
        print("--- Captured stdout (with mocked color tags) ---")
        print(output)

        # Verify color codes (mocked as <tag>) are present in the output.
        self.assertIn("<header>--- a/test.deps<reset>", output)
        self.assertIn("<header>+++ b/test.deps<reset>", output)
        self.assertIn("<del>-b<reset>", output)
        self.assertIn("<add>+X<reset>", output)
        print("--- Assertions Passed ---")

    # --------------------------------------------------------------------------
    # Helper for testing main() (Orchestration Logic)
    # --------------------------------------------------------------------------

    def _run_main_test(self,
                       args,
                       initial_content_list,
                       expected_on_disk_list,
                       expected_formatted_list,
                       expected_return,
                       file_name="DEPS"):
        """
        Runs an end-to-end test of the deps_format.main() function.

        Args:
            args (list[str]): Command-line arguments (e.g., ['--dry-run']).
            initial_content_list (list[str] | None): Initial content of the file.
                                                     If None, the file is not created (for testing File Not Found).
            expected_on_disk_list (list[str] | None): Expected content on disk after execution.
            expected_formatted_list (list[str] | None): The ideal formatted content (used for comparison).
            expected_return (int): The expected exit code (e.g., 0 for success, 2 for dry-run changes).
            file_name (str): The name of the file to test against.
        """
        print(f"\n--- Running main test for: {args} on {file_name} ---")

        # 1. Setup: Create the file and prepare inputs
        abs_path = os.path.join(self._top_dir, file_name)
        if initial_content_list is not None:
            self._make_temp_file(file_name, initial_content_list)
            initial_content_str = '\n'.join(initial_content_list)
        else:
            initial_content_str = None

        expected_formatted_str = '\n'.join(
            expected_formatted_list) if expected_formatted_list else ""

        # Construct the full sys.argv list (script name + args + required flags + file path)
        # Use sys.argv[0] as a placeholder for the script name.
        argv = [sys.argv[0]] + args + ['--top-dir', self._top_dir, file_name]

        # 2. Execution: Run main() while capturing stdout/stderr/argv and exit code
        with mock.patch('sys.stdout', new_callable=io.StringIO) as mock_stdout, \
             mock.patch('sys.stderr', new_callable=io.StringIO) as mock_stderr, \
             mock.patch('sys.argv', argv), \
             mock.patch('deps_format.setup_color.init') as mock_color_init:

            # deps_format.main() calls sys.exit(), so we catch it to check the exit code.
            try:
                deps_format.main()
                actual_return = 0  # Should only happen if main completes without sys.exit(0)
            except SystemExit as e:
                actual_return = e.code

            # Ensure color initialization is called as expected by main().
            mock_color_init.assert_called_once()

            # 3. Verification: Check exit code
            self.assertEqual(actual_return,
                             expected_return,
                             msg="Exit code mismatch")

            # 4. Verification: Read back content from disk
            if os.path.exists(abs_path):
                with open(abs_path, 'r', encoding='utf-8') as f:
                    actual_content_on_disk = f.read()
            else:
                actual_content_on_disk = None

            stdout_val = mock_stdout.getvalue()
            stderr_val = mock_stderr.getvalue()

            # 5. Verification: Handle specific error cases (e.g., File Not Found)
            if initial_content_list is None:
                # If the file didn't exist initially, we expect an error message.
                self.assertIn(f"Error reading DEPS file {file_name}",
                              stderr_val)
                return stdout_val, stderr_val

            # 6. Verification: Check behavior based on flags and content changes
            is_dry_run = '--dry-run' in args
            is_diff = '--diff' in args
            has_changes = initial_content_str != expected_formatted_str

            # Check stdout for diff output
            if is_diff:
                if has_changes:
                    self.assertIn(f"--- a/{file_name}",
                                  stdout_val,
                                  msg="Should contain diff start")
                    self.assertIn(f"+++ b/{file_name}",
                                  stdout_val,
                                  msg="Should contain diff end")
                    self.assertNotIn(
                        f"No formatting changes needed for DEPS file: {file_name}",
                        stdout_val,
                        msg="Should NOT say no changes")
                else:  # No changes
                    self.assertIn(
                        f"No formatting changes needed for DEPS file: {file_name}",
                        stdout_val,
                        msg="Should say no changes")
                    self.assertNotIn(f"--- a/{file_name}",
                                     stdout_val,
                                     msg="Should NOT contain diff start")

            # Check disk content modifications
            if is_dry_run:
                self.assertEqual(
                    actual_content_on_disk,
                    initial_content_str,
                    msg="Disk content should not change in dry run")
            elif is_diff:
                self.assertEqual(
                    actual_content_on_disk,
                    initial_content_str,
                    msg="Disk content should not change in diff-only mode")
            else:
                # Write-back mode
                self.assertEqual(
                    actual_content_on_disk,
                    '\n'.join(expected_on_disk_list),
                    msg="Disk content mismatch (on-disk expectation)")
                self.assertEqual(
                    actual_content_on_disk,
                    expected_formatted_str,
                    msg="Disk content mismatch (formatter output expectation)")
                if has_changes:
                    # Should print a confirmation message in write-back mode.
                    self.assertIn(f"Formatted DEPS file: {file_name}",
                                  stdout_val)

        print("--- Standard Output ---")
        print(stdout_val)
        print("--- Standard Error ---")
        print(stderr_val)
        return stdout_val, stderr_val

    # --------------------------------------------------------------------------
    # Tests for main() (Orchestration Logic)
    # --------------------------------------------------------------------------

    def testMain_WriteBack_Changes(self):
        print("\n=== testMain_WriteBack_Changes ===")
        initial = ["include_rules = ['+b', '+a']"]
        expected = [
            "include_rules = [",
            "  '+a',",
            "  '+b',",
            "]",
        ]
        # No flags means write-back mode. Expect exit code 0.
        self._run_main_test([], initial, expected, expected, 0)

    def testMain_WriteBack_NoChanges(self):
        print("\n=== testMain_WriteBack_NoChanges ===")
        initial = [
            "include_rules = [",
            "  '+a',",
            "  '+b',",
            "]",
        ]
        # Write-back mode with no changes. Expect exit code 0.
        self._run_main_test([], initial, initial, initial, 0)

    def testMain_DryRun_Changes(self):
        print("\n=== testMain_DryRun_Changes ===")
        initial = ["include_rules = ['+b', '+a']"]
        expected_formatted = [
            "include_rules = [",
            "  '+a',",
            "  '+b',",
            "]",
        ]
        # Expect return code 2 when changes are needed in dry-run. File on disk remains unchanged.
        self._run_main_test(['--dry-run'], initial, initial, expected_formatted,
                            2)

    def testMain_DryRun_NoChanges(self):
        print("\n=== testMain_DryRun_NoChanges ===")
        initial = [
            "include_rules = [",
            "  '+a',",
            "  '+b',",
            "]",
        ]
        # Expect return code 0 when no changes are needed.
        self._run_main_test(['--dry-run'], initial, initial, initial, 0)

    def testMain_Diff_Changes(self):
        print("\n=== testMain_Diff_Changes ===")
        initial = ["include_rules = ['+b', '+a']"]
        expected_formatted = [
            "include_rules = [",
            "  '+a',",
            "  '+b',",
            "]",
        ]
        # Diff mode should return 0 and print the diff to stdout. File on disk remains unchanged.
        self._run_main_test(['--diff'], initial, initial, expected_formatted, 0)

    def testMain_Diff_NoChanges(self):
        print("\n=== testMain_Diff_NoChanges ===")
        initial = [
            "include_rules = [",
            "  '+a',",
            "  '+b',",
            "]",
        ]
        # Diff mode with no changes. Expect exit code 0.
        self._run_main_test(['--diff'], initial, initial, initial, 0)

    def testMain_DryRunDiff_Changes(self):
        print("\n=== testMain_DryRunDiff_Changes ===")
        initial = ["include_rules = ['+b', '+a']"]
        expected_formatted = [
            "include_rules = [",
            "  '+a',",
            "  '+b',",
            "]",
        ]
        # Dry-run takes precedence for the exit code (2), but diff output should still be shown.
        self._run_main_test(['--dry-run', '--diff'], initial, initial,
                            expected_formatted, 2)

    def testMain_FileNotFound(self):
        print("\n=== testMain_FileNotFound ===")
        # Pass None for initial content to simulate file not found. Expect exit code 1.
        self._run_main_test([],
                            None,
                            None,
                            None,
                            1,
                            file_name="NONEXISTENT_DEPS")

    @mock.patch('logging.exception')
    @mock.patch('deps_format._FormatDepsContent',
                side_effect=Exception("Test Error"))
    def testMain_FormattingError(self, mock_format, mock_logging):
        print("\n=== testMain_FormattingError ===")
        initial = ["include_rules = ['+a']"]

        # Test that exceptions during formatting are caught and handled (exit code 1).
        _, stderr = self._run_main_test([], initial, initial, initial, 1)
        self.assertIn(
            "Error during formatting logic for DEPS file DEPS: Test Error",
            stderr)
        mock_format.assert_called_once()
        # Ensure that the exception was logged.
        mock_logging.assert_called_with("Error formatting DEPS file %s", "DEPS")


if __name__ == '__main__':
    unittest.main()
