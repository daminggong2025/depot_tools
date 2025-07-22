# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tools for interacting with UTR"""
import subprocess

from mcp.server import fastmcp
import telemetry

tracer = telemetry.get_tracer(__name__)


def run_utr(
    command: str,
    checkout: str,
    builder: str,
    bucket: str,
    test_suite: str,
) -> (str, str):
    result = subprocess.run(
        [
            "vpython3",
            "tools",
            "utr",
            "run.py",
            "-B",
            bucket,
            "-b",
            builder,
            "-t",
            test_suite,
            command,
        ],
        capture_output=True,
        check=False,
        text=True,
        cwd=checkout,
    )
    print(type(result.stdout))
    return result.stdout, result.stderr


async def test_builder_compile(
    ctx: fastmcp.Context,
    checkout: str,
    builder: str,
    bucket: str,
    test_suite: str,
):
    """Recreates the build/compile step of the provided builder and targets

  Args:
    checkout: Location of the current checkout.
    builder: Name of the builder to test
    bucket: Bucket the builder belongs to (e.g. "try", or "ci")
    test_suite: Test suite to build, or "all" to build everything.

  Returns:
    A list of builds run on the current checkout.
  """
    with tracer.start_as_current_span('chromium.mcp.test_builder_compile'):
        stdout, stderr = run_utr('compile', checkout, builder, bucket,
                                 test_suite)
        await ctx.info(f'stdout {stdout}')
        await ctx.info(f'stderr {stderr}')
        return stdout


async def test_builder_test(
    ctx: fastmcp.Context,
    checkout: str,
    builder: str,
    bucket: str,
    test_suite: str = 'all',
):
    """Recreates the test step of the provided builder and targets

  Args:
    checkout: Location of the current checkout.
    builder: Name of the builder to test
    bucket: Bucket the builder belongs to (e.g. "try", or "ci")
    test_suite: Test suite to build, or "all" to build everything.

  Returns:
    A list of builds run on the current checkout.
  """
    with tracer.start_as_current_span('chromium.mcp.test_builder_test'):
        stdout, stderr = run_utr('test', checkout, builder, bucket, test_suite)
        await ctx.info(f'stdout {stdout}')
        await ctx.info(f'stderr {stderr}')
        return stdout
