# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tools for interacting with gclient"""
import subprocess

from mcp.server import fastmcp
import telemetry

tracer = telemetry.get_tracer(__name__)


async def sync_checkout(
    ctx: fastmcp.Context,
    checkout: str,
    revision: str = 'HEAD',
) -> None:
    """Syncs the current checkout to the provided revision or HEAD

  Handles getting the current checkout to the provided revision or
  HEAD and then syncs all the submodules.

  Args:
    checkout: Location of the current checkout.
    revision: Revision to sync to or HEAD if not provided.

  Returns:
    None
  """
    with tracer.start_as_current_span('chromium.mcp.sync_checkout'):
        result = subprocess.run(
            [
                'git',
                'checkout',
                revision,
            ],
            capture_output=True,
            check=False,
            text=True,
            cwd=checkout,
        )
        await ctx.info(f'stdout {result.stdout}')
        await ctx.info(f'stderr {result.stderr}')

        result = subprocess.run(
            [
                'gclient',
                'sync',
                '-f',
            ],
            capture_output=True,
            check=False,
            text=True,
            cwd=checkout,
        )
        await ctx.info(f'stdout {result.stdout}')
        await ctx.info(f'stderr {result.stderr}')
