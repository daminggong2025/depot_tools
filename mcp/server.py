#!/bin/env vpython3
# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""The MCP server that provides tools"""
from collections.abc import Sequence
import pathlib
import os
import sys

sys.path.insert(
    0,
    os.path.abspath(
        pathlib.Path(__file__).resolve().parent.parent.joinpath(
            pathlib.Path('infra_lib'))))
import telemetry

import buildbucket
import codesearch
import gclient
import git_cl
import rdb
import utr

from absl import app

from mcp.server import fastmcp

mcp = fastmcp.FastMCP('chrome-infra-mcp')


@mcp.tool(name='ping_tool')
def ping_tool() -> None:
    return


@mcp.tool(name='log_tool')
async def log_tool(ctx: fastmcp.Context, ) -> None:
    await ctx.info('hello')
    return


@mcp.prompt(
    name='fix_chrome_build_request',
    description='Generates a request to fix a specific build from its url')
def fix_chrome_build_request(url: str) -> str:
    """Generates a prompt to attempt to fix a broken chrome build"""
    return f'''
AI Prompt: Chrome Build Failure Resolution Agent


Role: You are a senior software engineer on the Google Chrome team. Your primary responsibility is to diagnose and fix build
failures in the Chromium project. You are an expert in C++, the GN build system, and the Chromium development workflow,
including interacting with the Gerrit code review system.


Goal: Autonomously analyze the failed build {url}, develop a correct code fix, and upload that fix to Gerrit as a
new Change List (CL) for a dry run (CQ+1).


Core Context:
* The Chromium source code is located in the /usr/local/google/home/sshrimp/chromium-checkouts/chromium/src directory.
* Build recipes and related infrastructure code are in /usr/local/google/home/sshrimp/infra_checkouts/build
* You must use the provided tools to interact with the build system, filesystem, and version control.

---


Mandatory Workflow

You must follow this sequence of steps precisely.


Step 1: Analyze the Build Failure
1. You will be given a URL to a failed build (e.g., https://ci.chromium.org/ui/p/chromium/builders/...).
2. Use the get_build tool to fetch the build details.
3. Carefully examine the summaryMarkdown and the steps of the failing steps to determine the root cause.
4. Concisely state the root cause of the failure before proceeding.


Step 2: Investigate and Formulate a Plan
1. Based on the error, determine which files need to be modified. Use glob and search_file_content to locate the relevant
source files (.cc, .h) or build files (BUILD.gn).
2. Read the contents of these files using read_file to understand the context of the required change.
3. Formulate a clear, step-by-step plan to fix the issue. You must state this plan before implementing it. For example: "The build failed due to a missing dependency. I will add //chrome/browser/foo:bar to the deps list in the //chrome/browser/baz/BUILD.gn file."


Step 3: Implement the Fix
1. Create a new git branch for your fix. The branch name should be descriptive of the fix.
    * Command: run_shell_command with git checkout -b <descriptive-branch-name>
2. Modify the necessary files using the replace or write_file tool. Ensure your changes are minimal, correct, and adhere to all surrounding code styles and project conventions.
3. Run `git cl format` to ensure the change is well formatted.


Step 4: Create and Upload the Change List (CL)
1. Stage your changes using git add.
2. Commit your changes with a well-formatted, multi-line commit message.
    * Template:


1         [Component]: Brief, imperative summary of the fix
2 
3         Detailed explanation of the problem and the fix. Describe why the
4         change is necessary and how it resolves the build failure.
5 
6         Fixed: <URL of the failed build>

    * Example Commit Command:
        git commit -m "actor: Add impl dependency to test_support\n\nThis change resolves a GN dependency error by adding the ':impl' target to the test_support' source set. This allows actor_test_util.cc to include headers from the :impl target, fixing the 'Include not allowed' error.\n\nFixed: https://ci.chromium.org/ui/p/chromium/builders/try/linux-rel/2283517/infra"
3. Upload the change to Gerrit to create a new CL. The command must set a dry run (CQ+1) and add sshrimp@google.com as the reviewer.
    * Command: run_shell_command with `git cl upload --reviewers sshrimp@google.com --cq-dry-run`


Step 5: Final Output
* Provide the URL of the newly created Gerrit CL as your final response. Your task is complete once you output the URL.
'''


def main(argv: Sequence[str]) -> None:
    if len(argv) > 1:
        raise app.UsageError('Too many command-line arguments.')

    # Only initialize telemetry if the user is opted in. The MCP does not
    # currently have the ability to show the banner so we need to rely on other
    # tools to get consent
    if telemetry.opted_in():
        telemetry.initialize('chromium.mcp')

    mcp.add_tool(buildbucket.get_build)
    mcp.add_tool(buildbucket.get_build_from_build_number)
    mcp.add_tool(buildbucket.get_build_from_id)
    mcp.add_tool(buildbucket.get_build_status)
    mcp.add_tool(buildbucket.search_builds)
    mcp.add_tool(codesearch.code_search)
    mcp.add_tool(git_cl.checkout_change_list)
    mcp.add_tool(git_cl.format)
    mcp.add_tool(git_cl.get_current_changes)
    mcp.add_tool(git_cl.upload_change_list)
    mcp.add_tool(git_cl.try_builder_results)
    mcp.add_tool(gclient.sync_checkout)
    mcp.add_tool(rdb.get_test_failures)
    mcp.add_tool(utr.test_builder_compile)
    mcp.add_tool(utr.test_builder_test)
    mcp.run()


if __name__ == '__main__':
    app.run(main)
