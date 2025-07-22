# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tools for interacting with resultdb"""
import json
import subprocess

from mcp.server import fastmcp
import telemetry

tracer = telemetry.get_tracer(__name__)


async def get_test_failures(
    ctx: fastmcp.Context,
    build_id: str,
) -> str:
    """Get the test failures for the given invocation.

  Only the failed tests are returned if any exist. Limits the max

  Args:
    build_id: The build that ran tests

  Returns:
    None
  """
    with tracer.start_as_current_span('chromium.mcp.get_test_failures'):
        request = {
            'invocations': [f'invocations/build-{build_id}'],
            'predicate': {
                'expectancy': 'VARIANTS_WITH_ONLY_UNEXPECTED_RESULTS'
            }
        }
        command = [
            'prpc',
            'call',
            'results.api.cr',
            'luci.resultdb.v1.ResultDB.QueryTestResults',
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            input=json.dumps(request),
            check=True,
            text=True,
        )
        await ctx.info(f'stdout {result.stdout}')
        await ctx.info(f'stderr {result.stderr}')
        return result.stdout
