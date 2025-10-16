#!/usr/bin/env python3
# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import difflib
import logging
import os
import re
import sys
import setup_color
from third_party import colorama
from third_party.colorama import Fore, Style


def _PrintDiff(original_content, formatted_content, rel_path):
    """Prints a unified diff using Python's difflib."""
    # Use forward slashes for consistent diff headers across platforms.
    rel_path = rel_path.replace(os.sep, '/')

    # Split content into lines, keeping line endings (True) for accurate diffing.
    original_lines = original_content.splitlines(True)
    formatted_lines = formatted_content.splitlines(True)

    # Generate the unified diff.
    diff = difflib.unified_diff(
        original_lines,
        formatted_lines,
        # These arguments guarantee the headers are exactly what we want.
        fromfile='a/' + rel_path,
        tofile='b/' + rel_path,
        # n=3 is the default context lines.
    )

    # Apply basic coloring if TTY is enabled, using existing color setup.
    use_color = setup_color.IS_TTY
    if use_color:
        # Define colors directly from colorama
        color_header = Fore.CYAN
        color_add = Fore.GREEN
        color_del = Fore.RED
        color_reset = Style.RESET_ALL

    for line in diff:
        # Ensure line is a string (difflib should return strings here in Py3)
        if not isinstance(line, str):
            # Handle potential bytes if encodings were mismatched (safety fallback)
            line = line.decode('utf-8', 'replace')

        if use_color:
            # Determine the color based on the line prefix
            color = None
            if line.startswith(('---', '+++', '@@')):
                color = color_header
            elif line.startswith('+'):
                color = color_add
            elif line.startswith('-'):
                color = color_del

            if color:
                # We must ensure the color reset happens BEFORE the newline.
                # 1. Strip trailing newlines (handles \n, \r\n).
                content = line.rstrip('\r\n')
                # 2. Capture the stripped whitespace.
                trailing_whitespace = line[len(content):]
                # 3. Construct the new line: Color + Content + Reset + Whitespace
                line = color + content + color_reset + trailing_whitespace

        sys.stdout.write(line)


def _ParseDepsLine(line, item_regex):
    """Parses a single line from a DEPS list, extracting items and comments.

    Returns:
        A tuple: (list_of_items, trailing_comment, should_bail).
        Returns (None, None, True) if a complex line is detected that cannot be formatted.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith('#'):
        return [], "", False  # Skip empty lines or handle as a comment in the caller

    matches_on_line = list(item_regex.finditer(line))

    if not matches_on_line:
        # Line is not empty, not a comment, and contains no recognized items.
        # Likely complex Python code (e.g., Var() or list comprehensions).
        return None, None, True  # Bail out

    # Check for complex syntax between or after items.
    for i, match_obj in enumerate(matches_on_line):
        if i > 0:
            intervening_content = line[matches_on_line[i - 1].end():match_obj.
                                       start()]
            if not re.fullmatch(r"\s*,\s*", intervening_content):
                return None, None, True  # Bail out: Implicit string concat or other operator

    last_match = matches_on_line[-1]
    remainder = line[last_match.end():]
    suffix_match = re.fullmatch(r"\s*,?\s*(#.*)?\s*", remainder)

    if not suffix_match:
        return None, None, True  # Bail out: Unexpected content after last item

    trailing_comment = suffix_match.group(1).strip() if suffix_match.group(
        1) else ""
    items = [match.group(1) for match in matches_on_line]
    return items, trailing_comment, False


def _ReconstructDepsList(indent, var_name, entries, trailing_comments):
    """Reconstructs the formatted DEPS list body."""
    if not entries and not trailing_comments:
        return f"{indent}{var_name} = []"

    item_indent = indent + "  "
    new_body_lines = []

    for entry in entries:
        for comment in entry["comments"]:
            new_body_lines.append(item_indent + comment)

        item_line = item_indent + entry["item"] + ","
        if entry["trailing_comment"]:
            item_line += "  " + entry["trailing_comment"]
        new_body_lines.append(item_line)

    for comment in trailing_comments:
        new_body_lines.append(item_indent + comment)

    return f"{indent}{var_name} = [\n" + "\n".join(
        new_body_lines) + f"\n{indent}]"


def _ProcessDepsListBody(body, item_regex):
    """Parses the body of a DEPS list, extracts entries, and sorts them.

    Returns:
        A tuple: (sorted_entries, trailing_comments, should_bail).
        Returns (None, None, True) if complex syntax is detected.
    """
    lines = body.splitlines()
    entries = []
    current_comments = []
    trailing_comments = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith('#'):
            current_comments.append(stripped)
            continue

        items, trailing_comment, should_bail = _ParseDepsLine(line, item_regex)
        if should_bail:
            return None, None, True

        for i, item in enumerate(items):
            comments_to_assign = current_comments if i == 0 else []
            current_comments = []  # Clear after assigning to the first item

            trailing_comment_to_assign = trailing_comment if i == len(
                items) - 1 else ""

            entries.append({
                "comments": comments_to_assign,
                "item": item,
                "trailing_comment": trailing_comment_to_assign,
                "sort_key": item[1:-1]  # Sort by content inside quotes
            })

    entries.sort(key=lambda e: e["sort_key"])
    trailing_comments = current_comments  # Any remaining comments are at the end

    return entries, trailing_comments, False


def _FormatDepsContent(content):
    """Formats DEPS file content by sorting specific lists alphabetically.

    Handles 'include_rules' and 'skip_child_includes'.
    Ensures consistent indentation (2 spaces) and line breaks (one item per line).
    Bails out of formatting a list if complex syntax is detected.
    """
    assignment_regex = re.compile(
        r"^(\s*)(include_rules|skip_child_includes)\s*=\s*\[([\s\S]*?)\]",
        re.MULTILINE)
    item_regex = re.compile(r"""(['"][-+!][^'"]*['"])""")

    def replace_func(match):
        original_match = match.group(0)
        indent = match.group(1)
        var_name = match.group(2)
        body = match.group(3)

        if not body.strip():
            return f"{indent}{var_name} = []"

        entries, trailing_comments, should_bail = _ProcessDepsListBody(
            body, item_regex)

        if should_bail:
            return original_match  # Return original if complex syntax found

        return _ReconstructDepsList(indent, var_name, entries,
                                    trailing_comments)

    return assignment_regex.sub(replace_func, content)


def main():
    parser = argparse.ArgumentParser(description='Format DEPS files.')
    parser.add_argument('paths',
                        metavar='PATH',
                        type=str,
                        nargs='+',
                        help='Paths to DEPS files to format')
    parser.add_argument('--dry-run',
                        action='store_true',
                        help='Don\'t modify any file on disk.')
    parser.add_argument(
        '--diff',
        action='store_true',
        help='Print diff to stdout rather than modifying files.')
    parser.add_argument('--top-dir',
                        type=str,
                        required=True,
                        help='Top directory of the checkout.')
    args = parser.parse_args()

    setup_color.init()
    return_value = 0
    for path in args.paths:
        abs_path = os.path.join(args.top_dir, path)
        try:
            with open(abs_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except (IOError, UnicodeDecodeError) as e:
            print(f"Error reading DEPS file {path}: {e}", file=sys.stderr)
            return_value = 1
            continue
        try:
            formatted_content = _FormatDepsContent(content)
        except Exception as e:
            logging.exception("Error formatting DEPS file %s", path)
            print(f"Error during formatting logic for DEPS file {path}: {e}",
                  file=sys.stderr)
            return_value = 1
            continue

        if content != formatted_content:
            if args.dry_run:
                if args.diff:
                    _PrintDiff(content, formatted_content, path)
                # Exit code 2 signals to git cl format that formatting is needed.
                sys.exit(2)
            elif args.diff:
                _PrintDiff(content, formatted_content, path)
            else:
                try:
                    with open(abs_path, 'w', encoding='utf-8') as f:
                        f.write(formatted_content)
                    print(f"Formatted DEPS file: {path}")
                except IOError as e:
                    print(f"Error writing DEPS file {path}: {e}",
                          file=sys.stderr)
                    return_value = 1
        elif args.diff:
            print(f"No formatting changes needed for DEPS file: {path}")

    sys.exit(return_value)


if __name__ == '__main__':
    main()
