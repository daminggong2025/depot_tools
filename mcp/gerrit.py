# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tools for interacting with gerrit"""
import asyncio
import base64
import json
import os
import pathlib
import re
import sys
from typing import Dict

import requests
from mcp.server import fastmcp

sys.path.insert(
    0,
    os.path.abspath(
        pathlib.Path(__file__).resolve().parent.parent.joinpath(
            pathlib.Path('infra_lib'))))
import telemetry

tracer = telemetry.get_tracer(__name__)


async def get_gerrit_host(ctx: fastmcp.Context, checkout: str) -> str:
    """Gets the gerrit host from the git remotes

    Args:
      checkout: Location of the current checkout.

    Returns:
      The gerrit host
    """
    with tracer.start_as_current_span('chromium.mcp.get_gerrit_host'):
        command = [
            "git",
            "remote",
            "-v",
        ]
        result = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=checkout,
        )
        stdout, stderr = await result.communicate()
        await ctx.info(f'stdout {stdout.decode()}')
        await ctx.info(f'stderr {stderr.decode()}')
        remotes = stdout.decode().split('\n')
        for remote in remotes:
            match = re.search(r'https://(.*?)-review.googlesource.com', remote)
            if match:
                return match.group(1)
        return ""


async def get_change_files(
    ctx: fastmcp.Context,
    change_number: int,
    checkout: str,
) -> Dict[str, str]:
    """Gets the files for a given change number

    Args:
      change_number: The change number to get the files for
      checkout: Location of the current checkout.

    Returns:
      A dictionary of file paths to file contents
    """
    with tracer.start_as_current_span('chromium.mcp.get_change_files'):
        gerrit_host = await get_gerrit_host(ctx, checkout)
        if not gerrit_host:
            return {"error": "Could not find gerrit host"}

        url = f"https://{gerrit_host}-review.googlesource.com/changes/{change_number}?o=CURRENT_REVISION"
        # Use git http.cookiefile to get the cookies for authentication
        command = [
            "git",
            "config",
            "--get",
            "http.cookiefile",
        ]
        result = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=checkout,
        )
        stdout, stderr = await result.communicate()
        cookie_file = stdout.decode().strip()

        # Use requests to make the http request
        with open(cookie_file, 'r') as f:
            cookies = f.read()
        headers = {'Cookie': cookies}
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return {"error": f"Failed to get change details: {response.text}"}

        # The response is a json object with a list of files
        # The real response starts with )]}'"
        change_details = json.loads(response.text[4:])
        current_revision = change_details['current_revision']
        files_url = f"https://{gerrit_host}-review.googlesource.com/changes/{change_number}/revisions/{current_revision}/files"
        response = requests.get(files_url, headers=headers)
        if response.status_code != 200:
            return {"error": f"Failed to get files: {response.text}"}

        files = json.loads(response.text[4:])
        file_contents = {}
        for file_path in files:
            if file_path == '/COMMIT_MSG':
                continue
            content_url = f"https://{gerrit_host}-review.googlesource.com/changes/{change_number}/revisions/{current_revision}/files/{file_path}/content"
            response = requests.get(content_url, headers=headers)
            if response.status_code != 200:
                file_contents[
                    file_path] = f"Failed to get file content: {response.text}"
            else:
                file_contents[file_path] = base64.b64decode(
                    response.text).decode()

        return file_contents
