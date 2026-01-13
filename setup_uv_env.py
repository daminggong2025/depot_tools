#!/usr/bin/env python3
# Copyright (c) 2026 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Cross-platform setup script for the uv-based hermetic environment.

This script bridges the gap between the internal CIPD/vpython3 infrastructure
and the modern 'uv' package manager. It:
1. Detects the host platform (OS/Arch).
2. Parses .vpython3 and bootstrap/manifest.txt to find hermetic dependencies.
3. Downloads the exact Python interpreter and Wheels from CIPD.
4. Generates a pyproject.toml that points uv to these local files.
5. Creates a .venv using uv.

Usage:
  python3 setup_uv_env.py
"""

import glob
import os
import platform
import re
import shutil
import subprocess
import sys
from typing import Dict, List, Tuple

# Configuration
VPYTHON_FILE = '.vpython3'
BRIDGE_DIR = '.uv_bridge'
BOOTSTRAP_MANIFEST = os.path.join('bootstrap', 'manifest.txt')


def detect_platform(python_version: str) -> Tuple[str, str]:
    """
    Detects the current CIPD platform string and vpython ABI suffix.

    Args:
        python_version: The target python version (e.g. "3.11").

    Returns:
        A tuple containing:
        - cipd_platform: e.g. 'linux-amd64'
        - vpython_platform_suffix: e.g. 'linux-amd64_cp311_cp311'

    Raises:
        RuntimeError: If the OS or Architecture is not supported.
    """
    system = sys.platform
    machine = platform.machine().lower()

    # Normalize OS
    if system.startswith('linux'):
        os_name = 'linux'
    elif system == 'darwin':
        os_name = 'mac'
    elif system == 'win32':
        os_name = 'windows'
    else:
        raise RuntimeError(f"Unsupported OS: {system}")

    # Normalize Arch
    if machine in ('x86_64', 'amd64'):
        arch = 'amd64'
    elif machine in ('arm64', 'aarch64'):
        arch = 'arm64'
    elif machine == 'x86':
        arch = '386'
    else:
        raise RuntimeError(f"Unsupported Architecture: {machine}")

    cipd_platform = f"{os_name}-{arch}"

    # Convert "3.11" -> "311"
    version_clean = python_version.replace('.', '')
    abi = f"cp{version_clean}"
    suffix = f"{cipd_platform}_{abi}_{abi}"

    return cipd_platform, suffix


def get_vpython_version(filename: str) -> str:
    """
    Parses the target python version from the .vpython3 file.

    Args:
        filename: Path to the .vpython3 file.

    Returns:
        The version string (e.g. "3.11") or a default "3.11".
    """
    with open(filename, 'r') as f:
        for line in f:
            match = re.search(r'python_version:\s*"([^"]+)"', line)
            if match:
                return match.group(1)
    return "3.11"  # Fallback


def get_python_version_from_manifest() -> str:
    """
    Parses the exact cpython3 version tag from bootstrap/manifest.txt.

    Returns:
        The CIPD version tag (e.g. "version:2@3.11.8.chromium.35").

    Raises:
        FileNotFoundError: If the manifest is missing.
        RuntimeError: If the cpython3 entry is not found.
    """
    # Look for line: infra/3pp/tools/cpython3/${platform} version:2@3.11.8.chromium.35
    if not os.path.exists(BOOTSTRAP_MANIFEST):
        raise FileNotFoundError(f"Manifest not found at {BOOTSTRAP_MANIFEST}")

    with open(BOOTSTRAP_MANIFEST, 'r') as f:
        for line in f:
            if 'infra/3pp/tools/cpython3/' in line:
                # Extract the version tag (last part)
                parts = line.strip().split()
                if len(parts) >= 2:
                    return parts[1]

    raise RuntimeError("Could not find cpython3 version in manifest")


def get_cipd_client() -> str:
    """
    Finds the CIPD client executable in the path or current directory.

    Returns:
        Absolute path to the cipd binary.

    Raises:
        RuntimeError: If cipd cannot be found.
    """
    if sys.platform == 'win32':
        candidates = ['cipd.bat', 'cipd.exe']
    else:
        candidates = ['./cipd', 'cipd']

    for c in candidates:
        if shutil.which(c) or os.path.exists(c):
            # Ensure we use absolute path or ./ if it's in CWD
            if os.path.exists(c) and not os.path.isabs(c):
                return os.path.abspath(c)
            return c

    raise RuntimeError(
        "Could not find 'cipd' client. Is depot_tools initialized?")


def parse_vpython3(filename: str, platform_suffix: str) -> List[Dict[str, str]]:
    """
    Parses the .vpython3 file to extract required wheels.

    Args:
        filename: Path to .vpython3.
        platform_suffix: substitution string for ${vpython_platform}.

    Returns:
        A list of dicts with 'name' and 'version' keys.
    """
    with open(filename, 'r') as f:
        content = f.read()

    wheels = []
    # Remove comments
    content = re.sub(r'#.*', '', content)
    # Find blocks: wheel: < ... >
    blocks = re.findall(r'wheel:\s*<([^>]+)>', content, re.DOTALL)

    for block in blocks:
        name_match = re.search(r'name:\s*"([^"]+)"', block)
        version_match = re.search(r'version:\s*"([^"]+)"', block)

        if name_match and version_match:
            name = name_match.group(1)
            version = version_match.group(1)

            # Substitute variables
            # .vpython3 uses ${vpython_platform}
            name = name.replace('${vpython_platform}', platform_suffix)

            wheels.append({'name': name, 'version': version})

    return wheels


def generate_cipd_ensure(wheels: List[Dict[str, str]], python_version: str,
                         python_pkg_base: str, output_file: str) -> None:
    """
    Generates a CIPD ensure file to download Python and Wheels.

    Args:
        wheels: List of wheel dicts.
        python_version: The CIPD version tag for Python.
        python_pkg_base: The base CIPD package path for Python.
        output_file: Path to write the ensure file.
    """
    with open(output_file, 'w') as f:
        # Add Wheels first (to root)
        for w in wheels:
            f.write(f"{w['name']} {w['version']}\n")

        # Add Python Interpreter to python3 subdir
        f.write("\n@Subdir python3\n")
        f.write(f"{python_pkg_base} {python_version}\n")


def get_package_name(cipd_name: str) -> str:
    """
    Extracts the PyPI package name from a CIPD package name.
    
    Args:
        cipd_name: e.g. "infra/python/wheels/requests-py3"
    
    Returns:
        Normalized package name e.g. "requests".
    """
    parts = cipd_name.split('/')
    basename = parts[-1]

    # Check if basename looks like a platform tag (e.g. linux-amd64_cp311...)
    if re.match(r'^(linux|mac|win|manylinux)', basename):
        basename = parts[-2]

    # Remove -py* suffix
    name = re.sub(r'-py.*$', '', basename)

    if name == 'six': return 'six'
    return name


def generate_pyproject(wheels: List[Dict[str, str]], bridge_dir: str,
                       python_version: str) -> None:
    """
    Generates pyproject.toml with local wheel sources.

    Args:
        wheels: List of wheel dicts.
        bridge_dir: Directory containing downloaded wheels.
        python_version: Exact python version string (e.g. "3.11.8").
    """
    dependencies = []
    sources = {}
    whl_files = glob.glob(os.path.join(bridge_dir, '*.whl'))

    print(f"Found {len(whl_files)} wheels in {bridge_dir}")

    for w in wheels:
        pkg_name = get_package_name(w['name'])
        search_name = pkg_name.replace('-', '_')

        match = None
        for whl in whl_files:
            filename = os.path.basename(whl)
            dist_name = filename.split('-')[0]
            if dist_name.lower() == search_name.lower():
                match = whl
                break

        if match:
            rel_path = os.path.relpath(match, '.').replace('\\', '/')
            dependencies.append(f'"{pkg_name}"')
            sources[pkg_name] = f'{{ path = "{rel_path}" }}'
        else:
            print(
                f"Warning: Could not find downloaded wheel for {pkg_name} ({w['name']})"
            )

    with open('pyproject.toml', 'w') as f:
        f.write('[project]\n')
        f.write('name = "depot_tools_env"\n')
        f.write('version = "0.1.0"\n')
        f.write(f'requires-python = "=={python_version}"\n')
        f.write('dependencies = [\n')
        for d in dependencies:
            f.write(f'  {d},\n')
        f.write(']\n\n')

        f.write('[tool.uv.sources]\n')
        for pkg, src in sources.items():
            f.write(f'{pkg} = {src}\n')


def main() -> None:
    # 0. Check for staleness to allow fast-exit
    if os.path.exists('pyproject.toml') and os.path.exists(BRIDGE_DIR):
        vpython_mtime = os.path.getmtime(VPYTHON_FILE)
        pyproject_mtime = os.path.getmtime('pyproject.toml')
        
        # If pyproject.toml is newer than .vpython3, we assume we are up to date.
        # We also check if ensure file exists to be safe.
        if pyproject_mtime > vpython_mtime and os.path.exists(os.path.join(BRIDGE_DIR, 'cipd_ensure.txt')):
            # Optional: Check if we just want to run the bridge logic or if this is called by the wrapper.
            # For now, we print nothing to keep it quiet when up-to-date.
            return

    print(f"--- 0. Parsing {VPYTHON_FILE} for Python version ---")
    vpython_ver = get_vpython_version(VPYTHON_FILE)
    print(f"Target Python Major: {vpython_ver}")

    print("\n--- 1. Detecting Platform ---")
    cipd_platform, vpython_suffix = detect_platform(vpython_ver)
    print(f"Platform: {cipd_platform}")
    print(f"Wheel Suffix: {vpython_suffix}")

    try:
        py_version_tag = get_python_version_from_manifest()
        print(f"Python Version Tag: {py_version_tag}")

        # Extract exact version for requires-python: 2@3.11.8.chromium.35 -> 3.11.8
        match = re.search(r'@(\d+\.\d+\.\d+)', py_version_tag)
        if match:
            exact_ver = match.group(1)
            print(f"Exact Python Version: {exact_ver}")
        else:
            print(
                f"Warning: Could not extract exact version from {py_version_tag}, using {vpython_ver}"
            )
            exact_ver = vpython_ver

    except Exception as e:
        print(f"Error reading manifest: {e}")
        sys.exit(1)

    python_cipd_pkg = f'infra/3pp/tools/cpython3/{cipd_platform}'

    if not os.path.exists(BRIDGE_DIR):
        os.makedirs(BRIDGE_DIR)

    print(f"\n--- 2. Parsing {VPYTHON_FILE} for Wheels ---")
    wheels = parse_vpython3(VPYTHON_FILE, vpython_suffix)
    print(f"Found {len(wheels)} required wheels.")

    print(f"\n--- 3. Downloading Dependencies (CIPD) ---")
    ensure_file = os.path.join(BRIDGE_DIR, 'cipd_ensure.txt')
    generate_cipd_ensure(wheels, py_version_tag, python_cipd_pkg, ensure_file)

    cipd_exe = get_cipd_client()
    print(f"Using CIPD client: {cipd_exe}")
    try:
        subprocess.check_call([
            cipd_exe, 'ensure', '-ensure-file', ensure_file, '-root',
            BRIDGE_DIR, '-log-level', 'warning'
        ])
    except subprocess.CalledProcessError:
        print("Error: CIPD ensure failed.")
        sys.exit(1)

    print(f"\n--- 4. Generating Configuration ---")
    generate_pyproject(wheels, BRIDGE_DIR, exact_ver)

    print(f"\n--- 5. Syncing Environment (uv) ---")
    # Find the python binary
    if sys.platform == 'win32':
        python_bin = os.path.join(BRIDGE_DIR, 'python3', 'bin', 'python3.exe')
    else:
        python_bin = os.path.join(BRIDGE_DIR, 'python3', 'bin', 'python3')

    if not os.path.exists(python_bin):
        # On some platforms/packages it might just be 'python'
        if sys.platform == 'win32':
            python_bin = os.path.join(BRIDGE_DIR, 'python3', 'bin',
                                      'python.exe')
        else:
            python_bin = os.path.join(BRIDGE_DIR, 'python3', 'bin', 'python')

    if not os.path.exists(python_bin):
        print(f"Error: Could not find python binary at {python_bin}")
        sys.exit(1)

    # Run uv venv
    print(f"Creating venv with {python_bin}...")
    try:
        subprocess.check_call(['uv', 'venv', '.venv', '--python', python_bin])
    except subprocess.CalledProcessError:
        print("Error: uv venv failed.")
        sys.exit(1)

    # Run uv sync
    print("Syncing dependencies...")
    try:
        subprocess.check_call(['uv', 'sync'])
    except subprocess.CalledProcessError:
        print("Error: uv sync failed.")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("SUCCESS: Setup complete.")
    print("VS Code .venv is ready.")
    print("=" * 60)


if __name__ == '__main__':
    main()
