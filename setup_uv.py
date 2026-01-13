#!/usr/bin/env python3
# Copyright (c) 2026 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Setup script for a uv-based environment from a .vpython3 spec.

This script parses a .vpython3 file (Protobuf Text Format), resolves
conditional dependencies (match tags), downloads them via CIPD, and
generates a pyproject.toml for uv to sync. It bridges the gap between
the internal CIPD/vpython3 infrastructure and the modern 'uv' package manager.
"""

import argparse
import glob
import os
import platform
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

# --- Constants ---

DEFAULT_VPYTHON_FILE = '.vpython3'
BRIDGE_DIR = '.uv_bridge'
CPYTHON_PACKAGE_BASE = 'infra/3pp/tools/cpython3'
BOOTSTRAP_MANIFEST = os.path.join(os.path.dirname(__file__), 'bootstrap', 'manifest.txt')

CIPD_SETUPTOOLS = 'infra/python/wheels/setuptools-py3'
CIPD_WHEEL = 'infra/python/wheels/wheel-py2_py3'

# Known fallback versions if not found in environment or manifest
DEFAULT_PYTHON_VERSIONS = {
    '3.8': 'version:2@3.8.10.chromium.26',
    '3.11': 'version:2@3.11.8.chromium.35',
}

# --- Protobuf Text Parser ---

class ProtoParser:
    """A simple parser for Protobuf Text Format used in .vpython files."""

    def __init__(self, content: str):
        self.content = content
        self.pos = 0
        self.length = len(content)

    def parse(self) -> Dict[str, Any]:
        """Parses the entire content into a dict (treating root as a message)."""
        return self._parse_message_body()

    def _skip_whitespace_and_comments(self):
        while self.pos < self.length:
            char = self.content[self.pos]
            if char.isspace():
                self.pos += 1
            elif char == '#':
                # Consume comment until newline
                while self.pos < self.length and self.content[self.pos] != '\n':
                    self.pos += 1
            else:
                break

    def _peek_char(self) -> Optional[str]:
        self._skip_whitespace_and_comments()
        return self.content[self.pos] if self.pos < self.length else None

    def _consume_char(self) -> Optional[str]:
        c = self._peek_char()
        if c:
            self.pos += 1
        return c

    def _parse_identifier(self) -> str:
        start = self.pos
        while self.pos < self.length:
            c = self.content[self.pos]
            if c.isalnum() or c in '_-':
                self.pos += 1
            else:
                break
        return self.content[start:self.pos]

    def _parse_string(self) -> str:
        quote = self._consume_char()
        start = self.pos
        value = ""
        while self.pos < self.length:
            c = self.content[self.pos]
            if c == '\\':
                self.pos += 2
                continue
            elif c == quote:
                value += self.content[start:self.pos]
                self.pos += 1
                return value
            self.pos += 1
        raise ValueError("Unterminated string")

    def _parse_value(self) -> Any:
        self._skip_whitespace_and_comments()
        c = self._peek_char()
        if c in ('"', "'"):
            return self._parse_string()
        elif c in ('<', '{'):
            opener = self._consume_char()
            closer = '>' if opener == '<' else '}'
            result = self._parse_message_body(closer)
            if self._peek_char() != closer:
                 raise ValueError(f"Expected '{closer}' but found '{self._peek_char()}' at pos {self.pos}")
            self._consume_char()
            return result
        elif c == '[':
            self._consume_char()
            values = []
            while True:
                self._skip_whitespace_and_comments()
                if self._peek_char() is None:
                     raise ValueError("Unexpected EOF inside list")
                if self._peek_char() == ']':
                    self._consume_char()
                    break
                values.append(self._parse_value())
                self._skip_whitespace_and_comments()
                if self._peek_char() == ',':
                    self._consume_char()
            return values
        else:
            val = self._parse_identifier()
            if val == 'true':
                return True
            if val == 'false':
                return False
            return val

    def _parse_message_body(self, closer: Optional[str] = None) -> Dict[str, Any]:
        msg = {}
        while self.pos < self.length:
            self._skip_whitespace_and_comments()
            if closer and self._peek_char() == closer:
                break
            
            # Check for field separators (comma or semicolon) which might appear between fields
            if self._peek_char() in (',', ';'):
                self._consume_char()
                continue

            key = self._parse_identifier()
            if not key:
                break
            self._skip_whitespace_and_comments()
            if self._peek_char() == ':':
                self._consume_char()
            val = self._parse_value()
            if key in msg:
                if not isinstance(msg[key], list):
                    msg[key] = [msg[key]]
                msg[key].append(val)
            else:
                msg[key] = val

        for repeated in ['wheel', 'match', 'not_match_tag', 'verify_pep425_tag']:
            if repeated in msg and not isinstance(msg[repeated], list):
                msg[repeated] = [msg[repeated]]
            elif repeated not in msg:
                msg[repeated] = []
        return msg


# --- Platform & Matching Logic ---

class Platform:
    """Represents the host platform and handles vpython conditional matching."""

    def __init__(self, os_name: str, arch: str, python_version: str):
        self.os = os_name
        self.arch = arch
        self.python_version = python_version
        self.tags = self._generate_tags()

    def _generate_tags(self) -> List[Dict[str, str]]:
        """Generates a list of PEP425 tags compatible with this platform."""
        py_tag = f"cp{self.python_version.replace('.', '')}"
        plat_tags = []
        if self.os == 'linux':
            if self.arch == 'amd64':
                plat_tags = ['linux_x86_64', 'manylinux1_x86_64', 'manylinux2014_x86_64']
            elif self.arch == 'arm64':
                plat_tags = ['linux_aarch64', 'manylinux2014_aarch64']
        elif self.os == 'mac':
            if self.arch == 'amd64':
                plat_tags = ['macosx_10_10_intel', 'macosx_10_9_x86_64']
            elif self.arch == 'arm64':
                plat_tags = ['macosx_11_0_arm64']
        elif self.os == 'windows':
            if self.arch == 'amd64':
                plat_tags = ['win_amd64']
            else:
                plat_tags = ['win32']

        tags = []
        for p in plat_tags:
            for abi in (py_tag, 'none'):
                tags.append({'python': py_tag, 'abi': abi, 'platform': p})
            tags.append({'python': 'py3', 'abi': 'none', 'platform': p})
        return tags

    def matches_generic(self, constraint: Dict[str, str]) -> bool:
        """Evaluates generic match: < os: "linux" cpu: "x86_64" >"""
        if 'os' in constraint:
            if constraint['os'] != self.os:
                return False
        if 'cpu' in constraint:
            # cpu in vpython: x86, x86_64, arm, arm64, etc.
            my_cpu = 'x86_64' if self.arch == 'amd64' else self.arch
            if constraint['cpu'] != my_cpu:
                return False
        return True

    def matches_tag(self, tags: List[Dict[str, str]]) -> bool:
        """Evaluates not_match_tag or match_tag (PEP425 tags)."""
        if not tags:
            return False

        for constraint in tags:
            for my_tag in self.tags:
                # Check if my_tag supersets the constraint
                if all(my_tag.get(k) == v for k, v in constraint.items()):
                    return True
        return False


def detect_system_platform() -> Tuple[str, str]:
    """Detects the current OS and architecture."""
    system = sys.platform
    machine = platform.machine().lower()

    if system.startswith('linux'):
        os_name = 'linux'
    elif system == 'darwin':
        os_name = 'mac'
    elif system == 'win32':
        os_name = 'windows'
    else:
        raise RuntimeError(f"Unsupported OS: {system}")

    if machine in ('x86_64', 'amd64'):
        arch = 'amd64'
    elif machine in ('arm64', 'aarch64'):
        arch = 'arm64'
    elif machine == 'x86':
        arch = '386'
    else:
        raise RuntimeError(f"Unsupported Architecture: {machine}")
    
    return os_name, arch


# --- Helpers ---

def get_cipd_client() -> str:
    """Finds the CIPD client executable."""
    if sys.platform == 'win32':
        candidates = ['cipd.bat', 'cipd.exe']
    else:
        candidates = ['./cipd', 'cipd']

    # Also check the directory of this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.extend([os.path.join(script_dir, c) for c in candidates])

    for c in candidates:
        if shutil.which(c) or os.path.exists(c):
            if os.path.exists(c) and not os.path.isabs(c):
                return os.path.abspath(c)
            return c

    # Fallback to just 'cipd' in path
    if shutil.which('cipd'):
        return 'cipd'

    raise RuntimeError("Could not find 'cipd' client. Is depot_tools initialized?")


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

    # Remove -py* suffix, but be careful not to match "-python"
    # Matches -py followed by a digit (e.g. -py3, -py2_py3)
    name = re.sub(r'-py[0-9].*$', '', basename)

    if name == 'six': return 'six'
    return name


def get_python_version_from_manifest() -> Optional[str]:
    """Parses the exact cpython3 version tag from bootstrap/manifest.txt."""
    if not os.path.exists(BOOTSTRAP_MANIFEST):
        return None

    try:
        with open(BOOTSTRAP_MANIFEST, 'r') as f:
            for line in f:
                if 'infra/3pp/tools/cpython3/' in line:
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        return parts[1]
    except Exception:
        pass
    return None


def resolve_wheels(spec: Dict[str, Any], plat: Platform, os_name: str, python_ver: str) -> Dict[str, str]:
    """Resolves required wheels from the spec, filtering by platform."""
    
    cipd_platform = f"{plat.os}-{plat.arch}"
    vpython_platform = f"{cipd_platform}_cp{python_ver.replace('.','')}_cp{python_ver.replace('.','')}"
    
    required_wheels = {}
    for w in spec['wheel']:
        # Check 'match' (Generic OS/CPU)
        if 'match' in w:
            if any(not plat.matches_generic(m) for m in w['match']):
                continue
        
        # Check 'not_match_tag' (Explicit exclusions)
        if 'not_match_tag' in w:
            if plat.matches_tag(w['not_match_tag']):
                continue

        name = w['name']
        version = w['version']
        
        # Substitutions
        name = name.replace('${vpython_platform}', vpython_platform)
        name = name.replace('${os}', os_name)
        
        if name in required_wheels:
            if required_wheels[name] != version:
                print(f"Warning: Duplicate wheel '{name}' found with different versions: {required_wheels[name]} vs {version}. Using {version}.")
        
        required_wheels[name] = version
    
    return required_wheels


def inject_implicit_deps(required_wheels: Dict[str, str], python_ver: str) -> None:
    """Injects setuptools and wheel if they are missing."""
    has_setuptools = False
    has_wheel = False
    
    for name in required_wheels:
        pkg = get_package_name(name)
        if pkg == 'setuptools': has_setuptools = True
        if pkg == 'wheel': has_wheel = True
    
    if not has_setuptools:
        print("Injecting implicit dependency: setuptools")
        if python_ver.startswith("3.8"):
             # Setuptools 69+ drops Python 3.8 support
             required_wheels[CIPD_SETUPTOOLS] = 'version:68.0.0'
        else:
             required_wheels[CIPD_SETUPTOOLS] = 'version:75.6.0'
        
    if not has_wheel:
        print("Injecting implicit dependency: wheel")
        required_wheels[CIPD_WHEEL] = 'version:0.37.1'


def generate_cipd_ensure(ensure_path: str, required_wheels: Dict[str, str], python_ver: str, cipd_platform: str) -> None:
    """Writes the CIPD ensure file."""
    
    python_cipd_version = None
    if python_ver == "3.11":
        python_cipd_version = get_python_version_from_manifest()
    
    if not python_cipd_version:
        python_cipd_version = DEFAULT_PYTHON_VERSIONS.get(python_ver)
    
    if not python_cipd_version:
        print(f"Warning: No CIPD version found for Python {python_ver}. Using default 3.11 fallback.")
        python_cipd_version = "version:2@3.11.8.chromium.35"

    with open(ensure_path, 'w') as f:
        for name, version in required_wheels.items():
            f.write(f"{name} {version}\n")
        f.write(f"\n@Subdir python3\n{CPYTHON_PACKAGE_BASE}/{cipd_platform} {python_cipd_version}\n")


def generate_pyproject(required_wheels: Dict[str, str], python_ver: str) -> None:
    """Generates pyproject.toml mapping packages to local wheels."""
    whl_files = glob.glob(os.path.join(BRIDGE_DIR, '*.whl'))
    dependencies = []
    sources = {}

    for name in required_wheels:
        pkg_name = get_package_name(name)
        normalized_search = pkg_name.replace('-', '_').lower()
        match = None
        
        for whl in whl_files:
            filename = os.path.basename(whl)
            if filename.lower().startswith(normalized_search + '-') :
                match = whl
                break
        
        if match:
             rel_path = os.path.relpath(match, '.').replace('\\', '/')
             dependencies.append(pkg_name)
             sources[pkg_name] = f'{{ path = "{rel_path}" }}'
        else:
            print(f"Warning: Could not find downloaded wheel for {pkg_name} ({name})")

    with open('pyproject.toml', 'w') as f:
        f.write('[project]\n')
        f.write('name = "vpython_env"\n')
        f.write('version = "0.1.0"\n')
        f.write(f'requires-python = ">={python_ver}"\n')
        f.write('dependencies = [\n')
        for d in dependencies:
            f.write(f'  "{d}",\n')
        f.write(']\n\n')

        f.write('[tool.uv]\n')
        f.write('index = []\n\n')

        f.write('[tool.uv.sources]\n')
        for pkg, src in sources.items():
            f.write(f'{pkg} = {src}\n')


# --- Main Logic ---

def main():
    parser = argparse.ArgumentParser(description="Setup uv environment from .vpython3")
    parser.add_argument("spec_file", nargs="?", default=DEFAULT_VPYTHON_FILE, help="Path to .vpython3 file")
    parser.add_argument("--force", action="store_true", help="Force regeneration")
    args = parser.parse_args()

    if not os.path.exists(args.spec_file):
        print(f"Error: Spec file {args.spec_file} not found.")
        sys.exit(1)

    # 0. Check for staleness
    if not args.force and os.path.exists('pyproject.toml') and os.path.exists(BRIDGE_DIR):
        vpython_mtime = os.path.getmtime(args.spec_file)
        pyproject_mtime = os.path.getmtime('pyproject.toml')
        if pyproject_mtime > vpython_mtime and os.path.exists(os.path.join(BRIDGE_DIR, 'cipd_ensure.txt')):
             return

    print(f"Parsing {args.spec_file}...")
    with open(args.spec_file, 'r') as f:
        spec = ProtoParser(f.read()).parse()

    # 1. Determine Python Version
    python_ver = str(spec.get('python_version', '3.11'))
    if isinstance(python_ver, list):
         python_ver = python_ver[0]
    
    print(f"Target Python Version: {python_ver}")

    os_name, arch = detect_system_platform()
    plat = Platform(os_name, arch, python_ver)
    print(f"Platform: {os_name}-{arch}")

    # 2. Filter Wheels & Inject Deps
    required_wheels = resolve_wheels(spec, plat, os_name, python_ver)
    inject_implicit_deps(required_wheels, python_ver)
    print(f"Found {len(required_wheels)} applicable wheels.")

    # 3. Setup Bridge Dir
    if not os.path.exists(BRIDGE_DIR):
        os.makedirs(BRIDGE_DIR)
    
    # 4. Generate CIPD Ensure & Download
    cipd_platform = f"{os_name}-{arch}"
    ensure_path = os.path.join(BRIDGE_DIR, 'cipd_ensure.txt')
    generate_cipd_ensure(ensure_path, required_wheels, python_ver, cipd_platform)

    print("Downloading dependencies via CIPD...")
    try:
        cipd_exe = get_cipd_client()
        subprocess.check_call([
            cipd_exe, 'ensure', '-ensure-file', ensure_path, '-root', 
            BRIDGE_DIR, '-log-level', 'warning'
        ])
    except Exception as e:
        print(f"CIPD Ensure failed: {e}")
        sys.exit(1)

    # 5. Generate pyproject.toml
    print("Generating pyproject.toml...")
    generate_pyproject(required_wheels, python_ver)

    # 6. Run uv sync
    print("Syncing with uv...")
    python_bin = os.path.join(BRIDGE_DIR, 'python3', 'bin', 'python3')
    if sys.platform == 'win32':
        python_bin = os.path.join(BRIDGE_DIR, 'python3', 'bin', 'python.exe')
    
    if not os.path.exists(python_bin):
        print(f"Error: Python binary not found at {python_bin}")
        sys.exit(1)

    # Create venv without pip/seed first
    subprocess.check_call(['uv', 'venv', '.venv', '--python', python_bin])
    
    # Sync using only local sources
    subprocess.check_call(['uv', 'sync', '--no-index'])
    print("\nEnvironment ready in .venv")

if __name__ == '__main__':
    main()