# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tools for interacting with buildbucket"""
import subprocess

from mcp.server import fastmcp
import telemetry

tracer = telemetry.get_tracer(__name__)


async def code_search(
    ctx: fastmcp.Context,
    expression: str,
    max_results: int = 10,
    context: int = 5,
) -> str:
    """Searches the code base for the given string

  This should always be used over searching local files when a subdirectory
  is not included. Note that results will only contain landed changes - any
  unsubmitted changes that you or the user have made in the current checkout
  will not be reflected.

  Args:
    expression: The regex to search for.
    - Search with case-sensitive regex.
    - Include file patterns with f: and exclude file patterns with -f:
      The file patterns should be regex, not glob.
    max_results: Set a limit on how many result files to return (default 10)
    context: Print <n> preceding and <n> succeeding lines
  Return:
    The status of the build as a string
  """
    with tracer.start_as_current_span('chromium.mcp.codesearch'):
        command = [
            'cs',
            f'--max_num_results={max_results}',
            f'--context={context}',
            '--',
            'git:chromium/chromium/src@main',
            'case:y',
            expression,
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=True,
                text=True,
            )
            await ctx.info(result.stdout)
            await ctx.info(result.stderr)
            return result.stdout
        except Exception as e:
            await ctx.info('Exception calling prpc')
            return f'Exception calling prpc return {e}'
