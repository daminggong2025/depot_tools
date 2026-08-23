#!/usr/bin/env python3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Code formatting subsystem for git cl format."""

import collections
import concurrent.futures
import fnmatch
import io
import json
import multiprocessing.pool
import optparse
import os
import re
import shutil
import sys
import threading
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Tuple,
)

import clang_format
import gclient_paths
import gclient_utils
import google_java_format
import ktfmt
import metrics
import metrics_xml_format
import rustfmt
import subprocess2
import swift_format
import utils

# Subcommand decorator support
import subcommand


def _ComputeFormatDiffLineRanges(files, diffs, expand=0):
    """Gets the changed line ranges for each file.

    Parses a git diff on provided files and returns a dict that maps a file name
    to an ordered list of range tuples in the form (start_line, count).
    Ranges are in the same format as a git diff.

    Args:
        files: List of files to parse the diff of and return the ranges for.
        diffs: a dict of diffs, where key is the file without the prefix,
          and the value is the diff generated for the corresponding file.
          Note that the hunks in the diffs contain prefxies, such as a/.., b/..
        expand: Expand diff ranges by this many lines before & after.

    Returns:
        A dict of path->[(start_line, end_line), ...]
    """
    # If files is empty then diff_output will be a full diff.
    if len(files) == 0:
        return {}

    # Matches the added/modified line range in a diff hunk header.
    # 1 capture group: 'diff_start,diff_count' or 'diff_start'
    # Matches:
    #   @@ -12,2 +14,3 @@ -> '14,3'
    #   @@ -12,2 +17 @@   -> '17'
    pattern = r"^@@ -\d+(?:,\d+)? \+([0-9,]+) @@"

    line_diffs = collections.defaultdict(list)
    for file in files:
        diff = diffs.get(file, "")
        if not diff:
            continue

        prev_end = 0
        for match in re.findall(pattern, diff, flags=re.MULTILINE):
            if not match:
                continue

            # Matches +14,3
            if "," in match:
                diff_start, diff_count = match.split(",")
            else:
                # Matches +12
                diff_start, diff_count = match, 1

            # if the original lines were removed without replacements,
            # the diff count is 0. Then, no formatting is necessary.
            if diff_count == 0:
                continue

            # diff_count contains the diff_start line, and the line numbers
            # given to formatter args are inclusive. For example, in
            # google-java-format "--lines 5:10" includes 5th-10th lines.
            diff_start = int(diff_start)
            diff_count = int(diff_count)
            diff_end = diff_start + diff_count - 1 + expand
            diff_start = max(prev_end + 1, diff_start - expand)
            if diff_start <= diff_end:
                prev_end = diff_end
                line_diffs[file].append((diff_start, diff_end))

    return line_diffs


# Constants
DEPOT_TOOLS = os.path.dirname(os.path.abspath(__file__))
YAPF_CONFIG_FILENAME = ".style.yapf"


# Lazy helpers delegating to git_cl to eliminate circular module initialization
def Changelist(*args, **kwargs):
    import git_cl

    return git_cl.Changelist(*args, **kwargs)


def DieWithError(*args, **kwargs):
    import git_cl

    return git_cl.DieWithError(*args, **kwargs)


def RunCommand(*args, **kwargs):
    import git_cl

    return git_cl.RunCommand(*args, **kwargs)


def RunGit(*args, **kwargs):
    import git_cl

    return git_cl.RunGit(*args, **kwargs)


def RunGitDiffCmd(*args, **kwargs):
    import git_cl

    return git_cl.RunGitDiffCmd(*args, **kwargs)


def _SplitArgsByCmdLineLimit(*args, **kwargs):
    import git_cl

    return git_cl._SplitArgsByCmdLineLimit(*args, **kwargs)


def _FindYapfConfigFile(fpath, yapf_config_cache, top_dir=None):
    """Checks if a yapf file is in any parent directory of fpath until top_dir.

    Recursively checks parent directories to find yapf file and if no yapf file
    is found returns None. Uses yapf_config_cache as a cache for previously found
    configs.
    """
    fpath = os.path.abspath(fpath)
    # Return result if we've already computed it.
    if fpath in yapf_config_cache:
        return yapf_config_cache[fpath]

    parent_dir = os.path.dirname(fpath)
    if os.path.isfile(fpath):
        ret = _FindYapfConfigFile(parent_dir, yapf_config_cache, top_dir)
    else:
        # Otherwise fpath is a directory
        yapf_file = os.path.join(fpath, YAPF_CONFIG_FILENAME)
        if os.path.isfile(yapf_file):
            ret = yapf_file
        elif fpath in (top_dir, parent_dir):
            # If we're at the top level directory, or if we're at root
            # there is no provided style.
            ret = None
        else:
            # Otherwise recurse on the current directory.
            ret = _FindYapfConfigFile(parent_dir, yapf_config_cache, top_dir)
    yapf_config_cache[fpath] = ret
    return ret


def _FindMarkdownConfigFile(
    fpath: str,
    markdown_config_cache: Dict[str, Optional[str]],
    top_dir: Optional[str] = None,
) -> Optional[str]:
    """Checks if a .style.mdformat file is in any parent directory of fpath
    until top_dir.

    Recursively checks parent directories to find the config file and if none
    is found returns None. Uses markdown_config_cache as a cache.
    """
    if fpath not in markdown_config_cache:
        markdown_config_cache[fpath] = utils.find_config_file(
            fpath, ".style.mdformat", top_dir
        )
    return markdown_config_cache[fpath]


def _FindLitTemplateFormatterConfigFile(
    fpath: str,
    config_cache: Dict[str, Optional[str]],
    top_dir: Optional[str] = None,
) -> Optional[str]:
    """Checks if a .style.lit_template_formatter file is in any parent directory
    of fpath until top_dir.

    Recursively checks parent directories to find the config file and if none
    is found returns None. Uses config_cache as a cache.
    """
    if fpath not in config_cache:
        config_cache[fpath] = utils.find_config_file(
            fpath, ".style.lit_template_formatter", top_dir
        )
    return config_cache[fpath]


def _GetYapfIgnorePatterns(top_dir):
    """Returns all patterns in the .yapfignore file.

    yapf is supposed to handle the ignoring of files listed in .yapfignore itself,
    but this functionality appears to break when explicitly passing files to
    yapf for formatting. According to
    https://github.com/google/yapf/blob/HEAD/README.rst#excluding-files-from-formatting-yapfignore,
    the .yapfignore file should be in the directory that yapf is invoked from,
    which we assume to be the top level directory in this case.

    Args:
        top_dir: The top level directory for the repository being formatted.

    Returns:
        A set of all fnmatch patterns to be ignored.
    """
    yapfignore_file = os.path.join(top_dir, ".yapfignore")
    ignore_patterns = set()
    if not os.path.exists(yapfignore_file):
        return ignore_patterns

    for line in gclient_utils.FileRead(yapfignore_file).split("\n"):
        stripped_line = line.strip()
        # Comments and blank lines should be ignored.
        if stripped_line.startswith("#") or stripped_line == "":
            continue
        ignore_patterns.add(stripped_line)
    return ignore_patterns


def _FilterYapfIgnoredFiles(filepaths, patterns):
    """Filters out any filepaths that match any of the given patterns.

    Args:
        filepaths: An iterable of strings containing filepaths to filter.
        patterns: An iterable of strings containing fnmatch patterns to filter on.

    Returns:
        A list of strings containing all the elements of |filepaths| that did not
        match any of the patterns in |patterns|.
    """
    # Not inlined so that tests can use the same implementation.
    return [
        f for f in filepaths if not any(fnmatch.fnmatch(f, p) for p in patterns)
    ]


def _RunFormatterSubprocess(
    cmd: List[str],
    cwd: Optional[str] = None,
    shell: bool = False,
    stdin: Optional[bytes] = None,
) -> int:
    """Runs a formatter subprocess, capturing stdout/stderr and routing them to sys.stdout/sys.stderr."""
    (stdout, stderr), code = subprocess2.communicate(
        cmd,
        cwd=cwd,
        shell=shell,
        stdin=stdin,
        stdout=subprocess2.PIPE,
        stderr=subprocess2.PIPE,
    )
    if stdout:
        sys.stdout.flush()
        if hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write(stdout)
        else:
            sys.stdout.write(stdout.decode("utf-8", "replace"))
        sys.stdout.flush()
    if stderr:
        sys.stderr.flush()
        if hasattr(sys.stderr, "buffer"):
            sys.stderr.buffer.write(stderr)
        else:
            sys.stderr.write(stderr.decode("utf-8", "replace"))
        sys.stderr.flush()
    return code


def _RunClangFormatDiff(opts, paths, top_dir, diffs):
    """Runs clang-format-diff and sets a return value if necessary."""
    # Set to 2 to signal to CheckPatchFormatted() that this patch isn't
    # formatted. This is used to block during the presubmit.
    return_value = 0

    # Locate the clang-format binary in the checkout
    try:
        clang_format_tool = clang_format.FindClangFormatToolInChromiumTree()
    except clang_format.NotFoundError as e:
        DieWithError(e)

    # Full formatting
    if not diffs:
        cmd = [clang_format_tool]
        if not opts.dry_run and not opts.diff:
            cmd.append("-i")
        if opts.dry_run:
            for p in paths:
                with open(p, "r") as myfile:
                    code = myfile.read().replace("\r\n", "\n")
                    stdout = RunCommand(cmd + [p], cwd=top_dir)
                    stdout = stdout.replace("\r\n", "\n")
                    if opts.diff:
                        sys.stdout.write(stdout)
                    if code != stdout:
                        return_value = 2
        else:
            stdout = RunCommand(cmd + paths, cwd=top_dir)
            if opts.diff:
                sys.stdout.write(stdout)

        return return_value

    # Partial formatting
    try:
        script = clang_format.FindClangFormatScriptInChromiumTree(
            "clang-format-diff.py"
        )
    except clang_format.NotFoundError as e:
        DieWithError(e)

    cmd = [sys.executable, script, "-p1"]
    if not opts.dry_run and not opts.diff:
        cmd.append("-i")

    env = os.environ.copy()
    env["PATH"] = (
        str(os.path.dirname(clang_format_tool)) + os.pathsep + env["PATH"]
    )
    # If `clang-format-diff.py` is run without `-i` and the diff is
    # non-empty, it returns an error code of 1. This will cause `RunCommand`
    # to die with an error if `error_ok` is not set.
    input_diff = "\n".join(diffs.get(p, "") for p in paths)
    stdout = RunCommand(
        cmd,
        error_ok=True,
        stdin=input_diff.encode(),
        cwd=top_dir,
        env=env,
        shell=sys.platform.startswith("win32"),
    )

    if stdout:
        # Filter out full-deletion diffs produced by clang-format-diff.py for
        # files that are ignored by clang-format.
        filtered_diffs = []
        for file_diff in re.split(r"^(?=--- )", stdout, flags=re.MULTILINE):
            if not file_diff.strip():
                continue
            if re.search(
                r"^@@ -1(?:,\d+)? \+0,0 @@", file_diff, flags=re.MULTILINE
            ):
                continue
            filtered_diffs.append(file_diff)
        stdout = "".join(filtered_diffs)

    if opts.diff:
        sys.stdout.write(stdout)
    if opts.dry_run and len(stdout) > 0:
        return_value = 2

    return return_value


def _RunGoogleJavaFormat(opts, paths, top_dir, diffs):
    """Runs google-java-format and sets a return value if necessary."""

    tool = google_java_format.FindGoogleJavaFormat()
    if tool is None:
        # Fail silently. It could be we are on an old chromium revision, or that
        # it is a non-chromium project. https://crbug.com/1491627
        print("google-java-format not found, skipping java formatting.")
        return 0

    base_cmd = [tool]
    # The script now adds --aosp, but this shim is needed for the window where
    # devs are using depot_tools that is newer than their chromium checkout.
    # This can be remove after Dec 2025.
    if os.path.exists(
        os.path.join(os.path.dirname(tool), "chromium-overrides.jar")
    ):
        base_cmd += ["--aosp"]

    if not opts.diff:
        if opts.dry_run:
            base_cmd += ["--dry-run"]
        else:
            base_cmd += ["--replace"]

    changed_lines_only = bool(diffs)
    if changed_lines_only:
        # Format two lines around each changed line so that the correct amount
        # of blank lines will be added between symbols.
        line_diffs = _ComputeFormatDiffLineRanges(paths, diffs, expand=2)

    def RunFormat(cmd, path, range_args, **kwds):
        stdout = RunCommand(cmd + range_args + [path], **kwds)

        if changed_lines_only:
            # google-java-format will not remove unused imports because they
            # do not fall within the changed lines. Run the command again to
            # remove them.
            if opts.diff:
                stdout = RunCommand(
                    cmd + ["--fix-imports-only", "-"],
                    stdin=stdout.encode(),
                    **kwds,
                )
            else:
                stdout += RunCommand(cmd + ["--fix-imports-only", path], **kwds)

        # If --diff is passed, google-java-format will output formatted content.
        # Diff it with the existing file in the checkout and output the result.
        if opts.diff:
            stdout = RunGitDiffCmd(
                ["-U3"],
                "--no-index",
                [path, "-"],
                stdin=stdout.encode(),
                **kwds,
            )
        return stdout

    parent_out_buf = getattr(sys.stdout, "get_buffer", lambda: None)()
    parent_err_buf = getattr(sys.stderr, "get_buffer", lambda: None)()

    def _RunFormatWorker(cmd, path, range_args, out_buf, err_buf, **kwds):
        if out_buf and hasattr(sys.stdout, "set_buffer"):
            sys.stdout.set_buffer(out_buf)
        if err_buf and hasattr(sys.stderr, "set_buffer"):
            sys.stderr.set_buffer(err_buf)
        return RunFormat(cmd, path, range_args, **kwds)

    results = []
    kwds = {"error_ok": True, "cwd": top_dir}
    with multiprocessing.pool.ThreadPool() as pool:
        for path in paths:
            cmd = base_cmd.copy()
            range_args = []
            if changed_lines_only:
                ranges = line_diffs.get(path)
                if not ranges:
                    # E.g. There were only deleted lines.
                    continue
                range_args = ["--lines={}:{}".format(a, b) for a, b in ranges]

            results.append(
                pool.apply_async(
                    _RunFormatWorker,
                    args=[
                        cmd,
                        path,
                        range_args,
                        parent_out_buf,
                        parent_err_buf,
                    ],
                    kwds=kwds,
                )
            )

        return_value = 0
        for result in results:
            stdout = result.get()
            if stdout:
                if opts.diff:
                    sys.stdout.write("Requires formatting: " + stdout)
                if opts.dry_run:
                    return_value = 2

        return return_value


def _RunKtfmt(opts, paths, top_dir, diffs):
    """Runs ktfmt and sets a return value if necessary."""

    tool = ktfmt.FindKtfmt()
    if tool is None:
        print("ktfmt not found, skipping kotlin formatting.")
        return 0

    if opts.diff:
        return_value = 0
        kwds = {"error_ok": True, "cwd": top_dir}
        for path in paths:
            file_path = os.path.join(top_dir, path) if top_dir else path
            with open(file_path, "rb") as f:
                content = f.read()
            stdout = RunCommand([tool, "-"], stdin=content, **kwds)
            diff_output = RunGitDiffCmd(
                ["-U3"],
                "--no-index",
                [path, "-"],
                stdin=stdout.encode(),
                **kwds,
            )
            if diff_output:
                sys.stdout.write("Requires formatting: " + diff_output)
                if opts.dry_run:
                    return_value = 2
        return return_value

    cmd = [tool]
    if opts.dry_run:
        cmd += ["--set-exit-if-changed", "--dry-run"]

    for paths_batch in _SplitArgsByCmdLineLimit(paths):
        ktfmt_exitcode = _RunFormatterSubprocess(cmd + paths_batch, shell=False)
        if opts.dry_run and ktfmt_exitcode != 0:
            return 2

    return 0


def _RunRustFmt(opts, paths, top_dir, diffs):
    """Runs rustfmt.  Just like _RunClangFormatDiff returns 2 to indicate that
    presubmit checks have failed (and returns 0 otherwise)."""

    # Locate the rustfmt binary.
    try:
        rustfmt_tool = rustfmt.FindRustfmtToolInChromiumTree()
    except rustfmt.NotFoundError as e:
        DieWithError(e)

    chromium_src_path = gclient_paths.GetPrimarySolutionPath()
    rustfmt_toml_path = os.path.join(chromium_src_path, ".rustfmt.toml")

    # TODO(crbug.com/1440869): Support formatting only the changed lines
    # if `opts.full or git_cl.settings.GetFormatFullByDefault()` is False.
    cmd = [rustfmt_tool, f"--config-path={rustfmt_toml_path}"]
    if opts.dry_run:
        cmd.append("--check")

    for paths_batch in _SplitArgsByCmdLineLimit(paths):
        rustfmt_exitcode = _RunFormatterSubprocess(
            cmd + paths_batch, shell=False
        )
        if opts.dry_run and rustfmt_exitcode != 0:
            return 2

    return 0


def _RunSwiftFormat(opts, paths, top_dir, diffs):
    """Runs swift-format.  Just like _RunClangFormatDiff returns 2 to indicate
    that presubmit checks have failed (and returns 0 otherwise)."""
    if sys.platform != "darwin":
        DieWithError("swift-format is only supported on macOS.")

    # Locate the swift-format binary.
    try:
        swift_format_tool = swift_format.FindSwiftFormatToolInChromiumTree()
    except swift_format.NotFoundError as e:
        DieWithError(e)

    cmd = [swift_format_tool]
    if opts.dry_run:
        cmd += ["lint", "-s"]
    else:
        cmd += ["format", "-i"]
    cmd += paths
    swift_format_exitcode = _RunFormatterSubprocess(cmd)

    if opts.dry_run and swift_format_exitcode != 0:
        return 2

    return 0


_ruff_batch_supported_cache: bool | None = None


def _GetRuffChromiumPath() -> str:
    """Returns the absolute path to the ruff_chromium wrapper script."""
    depot_tools_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(depot_tools_path, "ruff_chromium")


def _IsRuffBatchSupported(ruff_chromium_path: str) -> bool:
    """Checks if the ruff_chromium wrapper supports batch formatting.

    It does this by checking if the wrapper defines 'def run_batch('.
    """
    global _ruff_batch_supported_cache
    if _ruff_batch_supported_cache is not None:
        return _ruff_batch_supported_cache

    try:
        with open(ruff_chromium_path, "r", encoding="utf-8") as f:
            _ruff_batch_supported_cache = "def run_batch(" in f.read()
    except (OSError, UnicodeDecodeError):
        _ruff_batch_supported_cache = False

    return _ruff_batch_supported_cache


def _RunPythonFormat(
    opts: optparse.Values,
    paths: List[str],
    top_dir: str,
    diffs: Mapping[str, str] | None,
) -> int:
    """Formats python files using ruff_chromium in batch mode if supported.

    If ruff_chromium is not present or batch mode is not supported, it falls
    back to YAPF formatting.
    """
    ruff_chromium = _GetRuffChromiumPath()

    use_ruff_batch = False
    if os.path.exists(ruff_chromium):
        use_ruff_batch = _IsRuffBatchSupported(ruff_chromium)

    if not use_ruff_batch:
        return _RunYapf(opts, paths, top_dir, diffs)

    config = {
        "root": top_dir,
        "diff": bool(opts.diff),
        "dry_run": bool(opts.dry_run),
        "full": bool(opts.full),
        "files": [],
    }

    line_diffs = {}
    if paths and diffs:
        line_diffs = _ComputeFormatDiffLineRanges(paths, diffs)

    for path in paths:
        file_entry: dict[str, Any] = {"path": path}
        if not opts.full:
            ranges = line_diffs.get(path)
            if not ranges:
                continue
            file_entry["ranges"] = [[start, end + 1] for start, end in ranges]
        config["files"].append(file_entry)

    if not config["files"]:
        return 0

    cmd = ["vpython3", ruff_chromium, "--batch"]
    stdin_data = json.dumps(config).encode("utf-8")

    try:
        (stdout, stderr), code = subprocess2.communicate(
            cmd,
            stdin=stdin_data,
            stdout=subprocess2.PIPE,
            stderr=subprocess2.PIPE,
            cwd=top_dir,
            shell=sys.platform == "win32",
        )

        def _WriteBytes(stream, data):
            if not data:
                return
            if hasattr(stream, "buffer"):
                stream.buffer.write(data)
            else:
                stream.write(
                    data.decode("utf-8", errors="replace")
                    if isinstance(data, bytes)
                    else data
                )

        if code == 1:
            _WriteBytes(sys.stderr, stderr)
            return 1
        if code == 2:
            if opts.diff and stdout:
                _WriteBytes(sys.stdout, stdout)
            return 2
        if code == 0:
            if opts.diff and stdout:
                _WriteBytes(sys.stdout, stdout)
            return 0
        _WriteBytes(sys.stderr, stderr)
        return 1
    except Exception as e:
        sys.stderr.write(f"Failed to run ruff_chromium --batch: {e}\n")
        return 1


def _RunYapf(opts, paths, top_dir, diffs):
    depot_tools_path = os.path.dirname(os.path.abspath(__file__))
    yapf_tool = os.path.join(depot_tools_path, "yapf")

    # Used for caching.
    yapf_configs = {}
    for p in paths:
        # Find the yapf style config for the current file, defaults to depot
        # tools default.
        _FindYapfConfigFile(p, yapf_configs, top_dir)

    # Turn on python formatting by default if a yapf config is specified.
    # This breaks in the case of this repo though since the specified
    # style file is also the global default.
    if opts.python is None:
        paths = [
            p
            for p in paths
            if _FindYapfConfigFile(p, yapf_configs, top_dir) is not None
        ]

    # Note: yapf still seems to fix indentation of the entire file
    # even if line ranges are specified.
    # See https://github.com/google/yapf/issues/499
    if paths and diffs:
        line_diffs = _ComputeFormatDiffLineRanges(paths, diffs)

    yapfignore_patterns = _GetYapfIgnorePatterns(top_dir)
    paths = _FilterYapfIgnoredFiles(paths, yapfignore_patterns)

    return_value = 0
    for path in paths:
        yapf_style = _FindYapfConfigFile(path, yapf_configs, top_dir)
        # Default to pep8 if not .style.yapf is found.
        if not yapf_style:
            yapf_style = "pep8"

        cmd = ["vpython3", yapf_tool, "--style", yapf_style, path]

        if not opts.full:
            ranges = line_diffs.get(path)
            if not ranges:
                continue
            # Only run yapf over changed line ranges.
            for diff_start, diff_end in ranges:
                cmd += ["-l", "{}-{}".format(diff_start, diff_end)]

        if opts.diff or opts.dry_run:
            cmd += ["--diff"]
            # Will return non-zero exit code if non-empty diff.
            stdout = RunCommand(
                cmd,
                error_ok=True,
                stderr=subprocess2.PIPE,
                cwd=top_dir,
                shell=sys.platform.startswith("win32"),
            )
            if opts.diff:
                sys.stdout.write(stdout)
            if opts.dry_run and len(stdout) > 0:
                return_value = 2
        else:
            cmd += ["-i"]
            RunCommand(cmd, cwd=top_dir, shell=sys.platform.startswith("win32"))
    return return_value


def _RunMarkdownFormat(
    opts: optparse.Values,
    paths: List[str],
    top_dir: str,
    diffs: Mapping[str, str] | None,
) -> int:
    depot_tools_path = os.path.dirname(os.path.abspath(__file__))
    markdown_tool = os.path.join(depot_tools_path, "markdown_format.py")

    # Used for caching.
    markdown_configs: Dict[str, Optional[str]] = {}

    # Only format files that have a .style.mdformat file in an ancestor
    # directory.
    paths = [
        p
        for p in paths
        if _FindMarkdownConfigFile(p, markdown_configs, top_dir) is not None
    ]

    if not paths:
        return 0

    return_value = 0
    cmd = ["vpython3", markdown_tool]
    if opts.diff:
        cmd.append("--diff")
    elif opts.dry_run:
        cmd.append("--check")

    exit_code = _RunFormatterSubprocess(cmd + paths)
    if exit_code == 2:
        return_value = 2

    return return_value


def _RunLitTemplateFormatter(
    opts: optparse.Values,
    paths: List[str],
    top_dir: str,
    diffs: Mapping[str, str] | None,
) -> int:
    """Runs lit_template_formatter on .html.ts files."""
    config_cache: Dict[str, Optional[str]] = {}
    paths = [
        p
        for p in paths
        if _FindLitTemplateFormatterConfigFile(p, config_cache, top_dir)
        is not None
    ]
    if not paths:
        return 0

    primary_solution_path = gclient_paths.GetPrimarySolutionPath()
    if not primary_solution_path:
        print(
            "Could not find the primary solution path, skipping Lit "
            "template formatting."
        )
        return 0

    formatter_path = os.path.join(
        primary_solution_path,
        "ui",
        "webui",
        "resources",
        "tools",
        "lit_template_formatter",
        "main.js",
    )
    if not os.path.exists(formatter_path):
        print(
            f'lit_template_formatter not found at "{formatter_path}", '
            "skipping Lit template formatting."
        )
        return 0

    node_py_path = os.path.join(
        primary_solution_path, "third_party", "node", "node.py"
    )
    if not os.path.exists(node_py_path):
        print(
            f'node.py not found at "{node_py_path}", skipping Lit template '
            "formatting."
        )
        return 0

    cmd = ["vpython3", node_py_path, formatter_path]
    if opts.dry_run:
        cmd.append("--dry-run")
    if opts.diff:
        cmd.append("--diff")

    return_value = 0
    for paths_batch in _SplitArgsByCmdLineLimit(paths):
        exit_code = _RunFormatterSubprocess(cmd + paths_batch, cwd=top_dir)
        if exit_code != 0:
            if opts.dry_run:
                return_value = 2
            else:
                return_value = exit_code

    return return_value


def _RunGnFormat(opts, paths, top_dir, diffs):
    cmd = [sys.executable, os.path.join(DEPOT_TOOLS, "gn.py"), "format"]
    if opts.dry_run or opts.diff:
        cmd.append("--dry-run")
    return_value = 0
    for path in paths:
        gn_ret = _RunFormatterSubprocess(
            cmd + [path], shell=sys.platform.startswith("win"), cwd=top_dir
        )
        if opts.diff and gn_ret == 2:
            # TODO this should compute and print the actual diff.
            print("This change has GN build file diff for " + path)
        if opts.dry_run and gn_ret == 2:
            return_value = 2  # Not formatted.
        elif gn_ret != 0:
            # For non-dry run cases (and non-2 return values for dry-run), a
            # nonzero error code indicates a failure, probably because the
            # file doesn't parse.
            DieWithError(
                "gn format failed on "
                + path
                + "\nTry running `gn format` on this file manually."
            )
    return return_value


def _RunMojomFormat(opts, paths, top_dir, diffs):
    primary_solution_path = gclient_paths.GetPrimarySolutionPath()
    if not primary_solution_path:
        DieWithError(
            "Could not find the primary solution path (e.g. "
            "the chromium checkout)"
        )
    mojom_format_path = os.path.join(
        primary_solution_path,
        "mojo",
        "public",
        "tools",
        "mojom",
        "mojom_format.py",
    )
    if not os.path.exists(mojom_format_path):
        DieWithError(f'Could not find mojom formater at "{mojom_format_path}"')

    cmd = ["vpython3", mojom_format_path]
    if opts.dry_run:
        cmd.append("--dry-run")
    cmd.extend(paths)

    ret = _RunFormatterSubprocess(cmd)
    if opts.dry_run and ret != 0:
        return 2

    return ret


def _RunMetricsXMLFormat(opts, paths, top_dir, diffs):
    # Skip the metrics formatting from the global presubmit hook. These files
    # have a separate presubmit hook that issues an error if the files need
    # formatting, whereas the top-level presubmit script merely issues a
    # warning. Formatting these files is somewhat slow, so it's important not to
    # duplicate the work.
    if opts.presubmit:
        return 0

    return_value = 0

    for path in paths:
        pretty_print_tool = metrics_xml_format.FindMetricsXMLFormatterTool(path)
        if not pretty_print_tool:
            continue

        cmd = [shutil.which("vpython3"), pretty_print_tool, "--non-interactive"]
        # If the XML file is histograms.xml or enums.xml, add the xml path
        # to the command as histograms/pretty_print.py now needs a relative
        # path argument after splitting the histograms into multiple
        # directories. For example, in tools/metrics/ukm, pretty-print could
        # be run using: $ python pretty_print.py But in
        # tools/metrics/histogrmas, pretty-print should be run with an
        # additional relative path argument, like: $ python pretty_print.py
        # metadata/UMA/histograms.xml $ python pretty_print.py enums.xml
        metricsDir = metrics_xml_format.GetMetricsDir(top_dir, path)
        histogramsDir = os.path.join(top_dir, "tools", "metrics", "histograms")
        if metricsDir == histogramsDir:
            cmd.append(path)
        if opts.dry_run or opts.diff:
            cmd.append("--diff")

        stdout = RunCommand(cmd, cwd=top_dir)
        if opts.diff:
            sys.stdout.write(stdout)
        if opts.dry_run and stdout:
            return_value = 2  # Not formatted.
    return return_value


def _RunLUCICfgFormat(opts, paths, top_dir, diffs):
    depot_tools_path = os.path.dirname(os.path.abspath(__file__))
    lucicfg = os.path.join(depot_tools_path, "lucicfg")
    if sys.platform == "win32":
        lucicfg += ".bat"

    cmd = [lucicfg, "fmt"]
    if opts.dry_run:
        cmd.append("--dry-run")
    cmd.extend(paths)

    ret = _RunFormatterSubprocess(cmd)
    if opts.dry_run and ret != 0:
        return 2

    return ret


FormatterFunction = Callable[
    [optparse.Values, List[str], str, Optional[Mapping[str, str]]], int
]


class _ThreadLocalStream:
    """Thread-local stream proxy that routes text and binary writes to a per-thread buffer.

    When multiple formatters run concurrently (e.g. clang-format, ruff, gn format),
    each worker thread may write diffs, status messages, or warnings directly to
    sys.stdout or sys.stderr (via print(), sys.stdout.write(), or sys.stdout.buffer.write()).
    This proxy intercepts writes in each worker thread and redirects them into an
    isolated thread-local memory buffer (io.BytesIO).

    Any write from an unmanaged thread (or the main thread when no buffer is active)
    transparently falls back to the underlying default stream.
    """

    def __init__(self, fallback_stream):
        self._fallback = fallback_stream
        self._local = threading.local()

    def set_buffer(self, buf: Optional[io.BytesIO]):
        self._local.buf = buf

    def get_buffer(self) -> Optional[io.BytesIO]:
        return getattr(self._local, "buf", None)

    def write(self, s):
        buf = self.get_buffer()
        if buf is not None:
            if isinstance(s, str):
                buf.write(s.encode("utf-8", "replace"))
                return len(s)
            return buf.write(s)
        if isinstance(s, bytes):
            if hasattr(self._fallback, "buffer"):
                return self._fallback.buffer.write(s)
            return self._fallback.write(s.decode("utf-8", "replace"))
        return self._fallback.write(s)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        buf = self.get_buffer()
        if buf is None:
            self._fallback.flush()

    @property
    def buffer(self):
        return self

    def __getattr__(self, name):
        # Delegate unknown stream attributes (e.g. isatty(), encoding, fileno())
        # to the fallback stream so formatters and libraries inspect stream
        # capabilities transparently.
        return getattr(self._fallback, name)


_ThreadSafeStreamWrapper = _ThreadLocalStream


def _SplitDiffsByFile(diff_string: str) -> Dict[str, str]:
    """Split a given diff string into per-file patches.

    This function expects that the diff_string was generated with a prefix.
    In other words, if it was generated by git-diff, don't add `--no-prefix`.
    `diff` always generates one with prefixes.

    Deleted files will be skipped.

    Args:
        diff_string: Unified diff
    Returns:
        a dict of file_path -> patch
    """
    if not diff_string:
        return {}

    file_diffs = re.split(r"^(?=diff --git)", diff_string, flags=re.MULTILINE)
    file_diffs = [d for d in file_diffs if d.strip()]
    ret = {}

    for file_diff in file_diffs:
        # Look for the line with `+++ ${prefix}/path/file.cc`
        # For file deletion, the line would be omitted or with `+++ /dev/null`,
        # which wouldn't match the regex.
        match = re.search(
            r"^\+\+\+ [^\s/]+/(.+)", file_diff, flags=re.MULTILINE
        )
        if match:
            ret[match.group(1)] = file_diff

    return ret


def _FindFilesToFormat(
    opts: optparse.Values,
    files: Optional[List[str]],
    upstream_commit: Optional[str],
) -> Tuple[List[str], Optional[Dict[str, str]]]:
    """Returns a list of files to format and the diffs.

    If opts.full, returns None for the diffs.
    Deleted files are always excluded in the return.
    """
    if opts.input_diff_file:
        with open(opts.input_diff_file, encoding="utf-8") as f:
            diffs = _SplitDiffsByFile(f.read())
            return diffs.keys(), diffs

    if opts.full:
        files = RunGitDiffCmd(
            ["--name-only", "--diff-filter=d"], upstream_commit, files
        ).splitlines()
        return files, None

    diffs = _SplitDiffsByFile(
        RunGitDiffCmd(["-U0"], upstream_commit, files, allow_prefix=True)
    )
    return diffs.keys(), diffs


@subcommand.usage("[files or directories to diff]")
@metrics.collector.collect_metrics("git cl format")
def CMDformat(parser: optparse.OptionParser, args: List[str]):
    """Runs auto-formatting tools (clang-format etc.) on the diff.

    Specific languages can be opted-in for automatic formatting by placing
    marker files in the directory hierarchy:
      - Python: .style.yapf
      - Markdown: .style.mdformat
      - Lit HTML templates (.html.ts): .style.lit_template_formatter
    """

    clang_exts = [".cc", ".cpp", ".h", ".m", ".mm", ".proto"]
    GN_EXTS = [".gn", ".gni", ".typemap"]
    parser.add_option(
        "--full",
        action="store_true",
        help="Reformat the full content of all touched files",
    )
    parser.add_option("--upstream", help="Branch to check against")
    parser.add_option(
        "--dry-run", action="store_true", help="Don't modify any file on disk."
    )
    parser.add_option(
        "--no-clang-format",
        dest="clang_format",
        action="store_false",
        default=True,
        help="Disables formatting of various file types using clang-format.",
    )
    parser.add_option(
        "--python",
        action="store_true",
        help="Enables python formatting on all python files.",
    )
    parser.add_option(
        "--no-python",
        action="store_false",
        dest="python",
        help="Disables python formatting on all python files. "
        "If neither --python or --no-python are set, python files that have a "
        ".style.yapf or ruff config file (ruff.toml or .ruff.toml) in an "
        "ancestor directory will be formatted. "
        "It is an error to set both.",
    )
    import git_cl

    default_js = git_cl.settings.GetFormatJs()
    parser.add_option(
        "--js",
        action="store_true",
        dest="js",
        default=default_js,
        help="Format javascript/typescript code with clang-format. "
        "Defaults to False unless FORMAT_JS is set to True in codereview.settings.",
    )
    parser.add_option(
        "--no-js",
        action="store_false",
        dest="js",
        help="Disable formatting of javascript/typescript code with clang-format.",
    )
    parser.add_option(
        "--diff",
        action="store_true",
        help="Print diff to stdout rather than modifying files.",
    )
    parser.add_option(
        "--presubmit",
        action="store_true",
        help="Used when running the script from a presubmit.",
    )

    parser.add_option(
        "--rust-fmt",
        dest="use_rust_fmt",
        action="store_true",
        default=rustfmt.IsRustfmtSupported(),
        help="Enables formatting of Rust file types using rustfmt.",
    )
    parser.add_option(
        "--no-rust-fmt",
        dest="use_rust_fmt",
        action="store_false",
        help="Disables formatting of Rust file types using rustfmt.",
    )

    parser.add_option(
        "--swift-format",
        dest="use_swift_format",
        action="store_true",
        default=swift_format.IsSwiftFormatSupported(),
        help="Enables formatting of Swift file types using swift-format "
        "(macOS host only).",
    )
    parser.add_option(
        "--no-swift-format",
        dest="use_swift_format",
        action="store_false",
        help="Disables formatting of Swift file types using swift-format.",
    )
    parser.add_option(
        "--input_diff_file",
        help="File to read input change diff from. "
        "If given, the added blocks of the files in the diff "
        "will be formatted.",
    )
    parser.add_option(
        "--mojom",
        action="store_true",
        help="Enables formatting of .mojom files.",
    )
    parser.add_option(
        "--no-java",
        action="store_true",
        help="Disable auto-formatting of .java",
    )
    parser.add_option(
        "--ktfmt",
        dest="ktfmt",
        action="store_true",
        default=False,
        help="Temporary until on by default. "
        "Enables formatting of .kt files using ktfmt.",
    )
    parser.add_option(
        "--lucicfg",
        action="store_true",
        help="Enables formatting of .star files.",
    )

    opts, files = parser.parse_args(args)
    upstream_commit: Optional[str] = None
    upstream_branch: Optional[str] = opts.upstream
    top_dir: Optional[str] = None

    if opts.input_diff_file:
        if opts.full:
            print(
                "--full and --input_diff_file cannot be used together.",
                file=sys.stderr,
            )
            return 1
        if files:
            print(
                "No file paths to format are allowed "
                "if --input_diff_file is given.",
                file=sys.stderr,
            )
            return 1
    elif gclient_utils.IsEnvCog():
        print(
            "Use --input_diff_file to run the format command in non-git "
            "environments. In CiderG, please use the "
            '"Format Modified Lines in All Files (git cl format)" '
            "functionality in the command palette instead.",
            file=sys.stderr,
        )
        return 1
    else:
        opts.full = opts.full or git_cl.settings.GetFormatFullByDefault()
        # git diff generates paths against the root of the repository.  Change
        # to that directory so clang-format can find files even within subdirs.
        rel_base_path = git_cl.settings.GetRelativeRoot()
        if rel_base_path:
            os.chdir(rel_base_path)

        # Grab the merge-base commit, i.e. the upstream commit of the current
        # branch when it was created or the last time it was rebased. This is
        # to cover the case where the user may have called "git fetch origin",
        # moving the origin branch to a newer commit, but hasn't rebased yet.
        if not upstream_branch:
            cl = Changelist()
            upstream_branch = cl.GetUpstreamBranch()
        if upstream_branch:
            upstream_commit = RunGit(
                ["merge-base", "HEAD", upstream_branch]
            ).strip()
        if not upstream_commit:
            DieWithError(
                "Could not find base commit for this branch. "
                "Are you in detached state?"
            )

    # Normalize files against the current path, so paths relative to the
    # current directory are still resolved as expected.
    top_dir = os.getcwd() if opts.presubmit else git_cl.settings.GetRoot()
    files = [os.path.join(os.getcwd(), file) for file in files]
    diff_files, diffs = _FindFilesToFormat(opts, files, upstream_commit)

    if opts.js:
        clang_exts.extend([".js", ".ts"])

    formatters: list[tuple[List[str], FormatterFunction, List[str]]] = [
        (GN_EXTS, _RunGnFormat, []),
        ([".xml"], _RunMetricsXMLFormat, []),
        ([".md"], _RunMarkdownFormat, []),
        ([".html.ts"], _RunLitTemplateFormatter, []),
    ]
    if not opts.no_java:
        formatters.append(([".java"], _RunGoogleJavaFormat, []))
    if opts.ktfmt:
        formatters.append(([".kt"], _RunKtfmt, []))
    if opts.clang_format:
        formatters.append((clang_exts, _RunClangFormatDiff, [".html.ts"]))
    if opts.use_rust_fmt:
        formatters.append(([".rs"], _RunRustFmt, []))
    if opts.use_swift_format:
        formatters.append(([".swift"], _RunSwiftFormat, []))
    if opts.python is not False:
        formatters.append(([".py"], _RunPythonFormat, []))
    if opts.mojom:
        formatters.append(([".mojom", ".test-mojom"], _RunMojomFormat, []))
    if opts.lucicfg:
        formatters.append(([".star"], _RunLUCICfgFormat, []))

    tasks: list[tuple[FormatterFunction, List[str]]] = []
    for item in formatters:
        file_types, format_func, exclude_types = item

        paths = [
            p
            for p in diff_files
            if p.lower().endswith(tuple(file_types))
            and not p.lower().endswith(tuple(exclude_types))
        ]
        if paths:
            tasks.append((format_func, paths))

    if not tasks:
        return 0

    # If only a single language formatter is needed, run it directly on the main
    # thread to avoid thread pool and buffer allocation overhead.
    if len(tasks) == 1:
        format_func, paths = tasks[0]
        return format_func(opts, paths, top_dir, diffs)

    # For multi-language changes, execute formatters concurrently across CPU
    # cores. To prevent concurrent threads from interleaving stdout/stderr
    # (e.g. diffs during git cl format --diff, or unformatted warnings during
    # git cl upload --dry-run), install thread-local proxies and buffer each
    # formatter's output independently.
    stdout_proxy = _ThreadLocalStream(sys.stdout)
    stderr_proxy = _ThreadLocalStream(sys.stderr)

    def _RunWorker(
        format_func: FormatterFunction, paths: List[str]
    ) -> tuple[int, bytes, bytes]:
        out_buf = io.BytesIO()
        err_buf = io.BytesIO()
        stdout_proxy.set_buffer(out_buf)
        stderr_proxy.set_buffer(err_buf)
        try:
            ret = format_func(opts, paths, top_dir, diffs)
            return ret, out_buf.getvalue(), err_buf.getvalue()
        except BaseException:
            out_bytes = out_buf.getvalue()
            if out_bytes:
                if hasattr(orig_stdout, "buffer"):
                    orig_stdout.buffer.write(out_bytes)
                else:
                    orig_stdout.write(out_bytes.decode("utf-8", "replace"))
                orig_stdout.flush()

            err_bytes = err_buf.getvalue()
            if err_bytes:
                if hasattr(orig_stderr, "buffer"):
                    orig_stderr.buffer.write(err_bytes)
                else:
                    orig_stderr.write(err_bytes.decode("utf-8", "replace"))
                orig_stderr.flush()
            raise
        finally:
            stdout_proxy.set_buffer(None)
            stderr_proxy.set_buffer(None)

    orig_stdout, orig_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = stdout_proxy, stderr_proxy
    try:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(tasks), 8)
        ) as executor:
            # Submit all language formatters concurrently.
            futures = [
                executor.submit(_RunWorker, func, paths)
                for func, paths in tasks
            ]
            return_value = 0
            # Iterate through futures in original task order so that outputs
            # (diffs, notices, errors) are emitted deterministically and sequentially.
            for future in futures:
                ret, out_bytes, err_bytes = future.result()
                if out_bytes:
                    if hasattr(orig_stdout, "buffer"):
                        orig_stdout.buffer.write(out_bytes)
                    else:
                        orig_stdout.write(out_bytes.decode("utf-8", "replace"))
                    orig_stdout.flush()
                if err_bytes:
                    if hasattr(orig_stderr, "buffer"):
                        orig_stderr.buffer.write(err_bytes)
                    else:
                        orig_stderr.write(err_bytes.decode("utf-8", "replace"))
                    orig_stderr.flush()
                # Deterministically preserve error codes (prioritizing code 2
                # from unformatted dry-run checks over other non-zero/negative codes).
                if ret != 0:
                    if return_value == 0 or ret == 2:
                        return_value = ret
            return return_value
    finally:
        sys.stdout, sys.stderr = orig_stdout, orig_stderr
