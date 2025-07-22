# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tools for building chromium targets locally"""
import json
import os
import pathlib
import subprocess

from mcp.server import fastmcp
import telemetry

tracer = telemetry.get_tracer(__name__)


# Unnecessary as an MCP tool, the output is already reasonable short.
async def gn_generate(
    ctx: fastmcp.Context,
    build_dir: str,
    gn_args: list[str] | None = None,
) -> str:
    """Generates ninja files which are then used to compile targets

    Args:
      build_dir: The output build directory (e.g. "out/Release")
      gn_args: The gn_args to use for the build. If none, the gn_args
        in the build_dir will be used.
    Return:
      The status of generating gn
    """
    if gn_args:
        # write the gn args to args.gn
        pass
    with tracer.start_as_current_span('chromium.mcp.gn_generate'):
        command = [
            'gn',
            'gen',
            build_dir,
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
            )
            await ctx.info(result.stdout)
            await ctx.info(result.stderr)
            return json.loads(result.stdout)['status']
        except Exception as e:
            await ctx.info('Exception calling prpc')
            return f'Exception calling prpc return {e}'


async def build(
    ctx: fastmcp.Context,
    build_dir: str,
    targets: list[str] | None = None,
) -> str:
    """Builds the provided targets using autoninja

    Before being called `gn gen <build_dir>` should be called. This tool more
    effectively invokes autoninja by not printing an excessive amount of output

    Args:
      build_dir: The output build directory (e.g. "out/Release")
      targets: The targets to build. If None, all targets are built
    Return:
      The status of the build
    """
    with tracer.start_as_current_span('chromium.mcp.build'):
        command = [
            'autoninja',
            '-C',
            build_dir,
        ]
        if targets:
            command.extend(targets)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
            )
            await ctx.info(result.stdout)
            await ctx.info(result.stderr)

            if result.returncode == 0:
                return 'Build suceeded.'

            siso_output = pathlib.Path(build_dir).joinpath('siso_output')
            if os.path.exists(siso_output):
                with open(siso_output, 'r') as f:
                    return f'Build failed: {f.read()}'
            return result.stderr
        except Exception as e:
            return f'Exception building: {e}'
