#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Replays the CLI transcripts in tests/transcripts/.

Each transcript file records one or more command invocations together with
their expected merged stdout+stderr and exit code. The commands run with the
vpython3 and the scripts of this checkout, so the suite pins the command line
contract of the code under review, not of a PATH-installed depot_tools. See
tests/transcripts/README.md for the file format.

Set CLI_TRANSCRIPT_UPDATE=1 to rewrite the expectations of failing
transcripts in place. Transcripts marked '# known-bug:' are expected to fail
and are never rewritten.
"""

import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DEPOT_TOOLS_ROOT = os.path.dirname(TESTS_DIR)
TRANSCRIPTS_DIR = os.path.join(TESTS_DIR, 'transcripts')

VPYTHON = os.path.join(
    DEPOT_TOOLS_ROOT, 'vpython3.bat' if sys.platform == 'win32' else 'vpython3')

UPDATE_MODE = os.environ.get('CLI_TRANSCRIPT_UPDATE') == '1'

COMMAND_PREFIX = '$ '
KNOWN_BUG_PREFIX = '# known-bug:'
WILDCARD = '...'
PER_COMMAND_TIMEOUT_S = 60

_EXIT_RE = re.compile(r'^\[exit: (\d+)\]$')
_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')


class TranscriptError(Exception):
    """A transcript file is malformed."""


def parse_transcript(path):
    """Parses a transcript file.

    Returns (known_bug, header, commands):
      known_bug: the description after '# known-bug:', or None.
      header: the comment and blank lines before the first command.
      commands: a list of (cmdline, argv, expected_lines, expected_exit).
    """
    with open(path, 'r') as f:
        lines = f.read().split('\n')
    if lines and lines[-1] == '':
        lines.pop()

    known_bug = None
    if lines and lines[0].startswith(KNOWN_BUG_PREFIX):
        known_bug = lines[0][len(KNOWN_BUG_PREFIX):].strip()

    header = []
    i = 0
    while i < len(lines) and not lines[i].startswith(COMMAND_PREFIX):
        if lines[i] and not lines[i].startswith('#'):
            raise TranscriptError(
                '%s:%d: expected a comment or a "$ " command line, got %r' %
                (path, i + 1, lines[i]))
        header.append(lines[i])
        i += 1

    commands = []
    while i < len(lines):
        cmdline = lines[i][len(COMMAND_PREFIX):]
        argv = shlex.split(cmdline)
        if not argv:
            raise TranscriptError('%s:%d: empty command' % (path, i + 1))
        i += 1
        expected = []
        expected_exit = None
        while i < len(lines) and not lines[i].startswith(COMMAND_PREFIX):
            m = _EXIT_RE.match(lines[i])
            i += 1
            if m:
                expected_exit = int(m.group(1))
                break
            expected.append(lines[i - 1])
        if expected_exit is None:
            raise TranscriptError('%s: command %r has no [exit: N] line' %
                                  (path, cmdline))
        commands.append((cmdline, argv, expected, expected_exit))

    if not commands:
        raise TranscriptError('%s: no commands found' % path)
    return known_bug, header, commands


def normalize_output(raw):
    """Decodes and normalizes command output into a list of lines.

    Normalizes \\r\\n to \\n, strips ANSI escape sequences (some scripts emit
    color codes even when the output is not a tty), replaces the checkout
    path with <DEPOT_TOOLS>, strips trailing whitespace per line and drops
    trailing blank lines.
    """
    text = raw.decode('utf-8', 'replace')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = _ANSI_RE.sub('', text)
    # The checkout may also be reported through a resolved symlink or, on
    # Windows, with forward slashes.
    roots = {DEPOT_TOOLS_ROOT, os.path.realpath(DEPOT_TOOLS_ROOT)}
    if sys.platform == 'win32':
        roots.update(root.replace('\\', '/') for root in list(roots))
    for root in roots:
        text = text.replace(root, '<DEPOT_TOOLS>')
    lines = [line.rstrip() for line in text.split('\n')]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def run_command(argv, cwd):
    """Runs one transcript command; returns (normalized_lines, exit_code)."""
    script = os.path.join(DEPOT_TOOLS_ROOT, argv[0])
    if not os.path.exists(script):
        raise TranscriptError('no such script in this checkout: %s' % argv[0])
    env = os.environ.copy()
    env.update({
        # Make the output deterministic. ANSI codes that still slip through
        # are stripped in normalize_output().
        'NO_COLOR': '1',
        'TERM': 'dumb',
        # optparse and argparse wrap help text based on COLUMNS.
        'COLUMNS': '80',
        # Keep the merged stdout+stderr in write order.
        'PYTHONUNBUFFERED': '1',
        'DEPOT_TOOLS_COLLECT_METRICS': '0',
    })
    proc = subprocess.run([VPYTHON, script] + argv[1:],
                          cwd=cwd,
                          env=env,
                          stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT,
                          timeout=PER_COMMAND_TIMEOUT_S,
                          check=False)
    return normalize_output(proc.stdout), proc.returncode


def match_output(expected, actual):
    """Matches actual lines against expected lines with '...' wildcards.

    A line containing only '...' matches any number of lines, including
    zero. Returns None on a match, or a description of the first mismatch.
    """
    e = a = 0
    star_e = star_a = None
    while a < len(actual):
        if e < len(expected) and expected[e] == WILDCARD:
            star_e, star_a = e, a
            e += 1
        elif e < len(expected) and expected[e] == actual[a]:
            e += 1
            a += 1
        elif star_e is not None:
            # Let the last '...' swallow one more actual line and retry.
            star_a += 1
            a = star_a
            e = star_e + 1
        else:
            if e < len(expected):
                return ('output line %d:\n    expected: %s\n    actual:   %s' %
                        (a + 1, expected[e], actual[a]))
            return 'unexpected extra output at line %d: %s' % (a + 1, actual[a])
    while e < len(expected) and expected[e] == WILDCARD:
        e += 1
    if e < len(expected):
        return 'missing expected output line: %s' % expected[e]
    return None


def write_transcript(path, header, results):
    """Rewrites a transcript with the actual output as literal lines.

    '...' wildcards are not preserved; re-add them by hand where the output
    is variable.
    """
    out = list(header)
    for cmdline, actual, exit_code in results:
        out.append(COMMAND_PREFIX + cmdline)
        out.extend(actual)
        out.append('[exit: %d]' % exit_code)
    with open(path, 'w') as f:
        f.write('\n'.join(out) + '\n')


class CliTranscriptTest(unittest.TestCase):
    """One test method per transcript file, generated at import time."""

    maxDiff = None

    def _check_transcript(self, path):
        known_bug, header, commands = parse_transcript(path)
        failures = []
        results = []
        # Run in an empty temporary directory so that the commands cannot
        # depend on the state of the current checkout.
        with tempfile.TemporaryDirectory() as cwd:
            for cmdline, argv, expected, expected_exit in commands:
                actual, exit_code = run_command(argv, cwd)
                results.append((cmdline, actual, exit_code))
                mismatch = match_output(expected, actual)
                if mismatch:
                    failures.append('$ %s\n  %s' % (cmdline, mismatch))
                if exit_code != expected_exit:
                    failures.append('$ %s\n  expected [exit: %d], got '
                                    '[exit: %d]' %
                                    (cmdline, expected_exit, exit_code))
        name = os.path.basename(path)
        if known_bug:
            if not failures:
                self.fail(
                    '%s: known-bug transcript flipped to passing (%s). The '
                    'bug appears to be fixed: remove the "# known-bug:" '
                    'marker.' % (name, known_bug))
            return
        if failures:
            if UPDATE_MODE:
                write_transcript(path, header, results)
                print('%s: updated' % name, file=sys.stderr)
                return
            self.fail('%s does not match:\n%s\nRun with '
                      'CLI_TRANSCRIPT_UPDATE=1 to update the expectations.' %
                      (name, '\n'.join(failures)))


def _make_test(path):

    def test(self):
        self._check_transcript(path)

    return test


def _generate_tests():
    paths = sorted(
        os.path.join(TRANSCRIPTS_DIR, name)
        for name in os.listdir(TRANSCRIPTS_DIR) if name.endswith('.txt'))
    assert paths, 'no transcripts found in %s' % TRANSCRIPTS_DIR
    for path in paths:
        name = 'test_' + os.path.splitext(os.path.basename(path))[0]
        setattr(CliTranscriptTest, name, _make_test(path))


_generate_tests()

if __name__ == '__main__':
    unittest.main()
