#!/usr/bin/env python3
# Copyright 2026 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import sys
import os
import re

def normalize_name(name):
    return name.lower().replace('_', '-').replace('.', '-')

def parse_version(ver_str):
    # Simplistic version parser to tuple of ints for comparison
    return tuple(int(x) for x in re.findall(r'\d+', ver_str))

def compare_versions(v1, op, v2):
    t1 = parse_version(v1)
    t2 = parse_version(v2)
    if op == '==':
        return t1 == t2
    elif op == '>=':
        return t1 >= t2
    elif op == '<=':
        return t1 <= t2
    elif op == '>':
        return t1 > t2
    elif op == '<':
        return t1 < t2
    elif op == '!=':
        return t1 != t2
    return False

def parse_vpython_toml(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Remove comments
    content = re.sub(r'#.*$', '', content, flags=re.MULTILINE)
    
    # Parse dependencies block
    deps = []
    deps_match = re.search(r'dependencies\s*=\s*\[(.*?)\]', content, re.DOTALL)
    if deps_match:
        deps_block = deps_match.group(1)
        # Find all strings in the block
        deps = re.findall(r'[\'"]([^\'"]+)[\'"]', deps_block)
        
    return deps

def parse_pep723(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Find the script block: # /// script ... # /// or # /// vpython ... # ///
    match = re.search(r'^#\s*///\s*(script|vpython)\s*\n(.*?)\n#\s*///\s*$', content, re.MULTILINE | re.DOTALL)
    if not match:
        return []
        
    block_content = match.group(2)
    # Strip leading '#' from each line in the block
    toml_lines = []
    for line in block_content.splitlines():
        line = line.strip()
        if line.startswith('#'):
            toml_lines.append(line[1:].strip())
        else:
            toml_lines.append(line)
            
    toml_content = '\n'.join(toml_lines)
    
    # Parse dependencies block from the extracted TOML
    deps = []
    deps_match = re.search(r'dependencies\s*=\s*\[(.*?)\]', toml_content, re.DOTALL)
    if deps_match:
        deps_block = deps_match.group(1)
        deps = re.findall(r'[\'"]([^\'"]+)[\'"]', deps_block)
        
    return deps

def parse_uv_lock(path):
    if not os.path.exists(path):
        return None
        
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Join backslash-continued lines
    lines = []
    current_line = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.endswith('\\'):
            current_line.append(line[:-1].strip())
        else:
            current_line.append(line)
            lines.append(' '.join(current_line))
            current_line = []
            
    deps = {}
    for line in lines:
        # Extract package spec before options/markers
        pkg_spec = line.split(';')[0].split('--')[0].strip()
        # pkg_spec is e.g. "six==1.15.0"
        if '==' in pkg_spec:
            name, ver = pkg_spec.split('==', 1)
            deps[normalize_name(name.strip())] = ver.strip()
            
    return deps

def verify(spec_path, lock_path):
    if not os.path.exists(spec_path):
        print(f"Error: Spec file not found at {spec_path}")
        return 1
        
    if spec_path.endswith('.py'):
        toml_deps = parse_pep723(spec_path)
    else:
        toml_deps = parse_vpython_toml(spec_path)
        
    if not toml_deps:
        # No dependencies in spec, lockfile not required (or should be empty)
        return 0
        
    lock_deps = parse_uv_lock(lock_path)
    if lock_deps is None:
        print(f"Error: Lockfile {lock_path} is missing!")
        return 1
        
    out_of_sync = False
    errors = []
    
    # Check if all spec requirements are satisfied by lockfile
    for dep in toml_deps:
        # Parse requirement: name and optional specifier
        # e.g. "six==1.15.0" or "six>=1.12" or "six"
        match = re.match(r'^([^>=<!~;]+)(.*)$', dep)
        if not match:
            errors.append(f"Invalid dependency format in spec: {dep}")
            out_of_sync = True
            continue
            
        name = normalize_name(match.group(1).strip())
        spec = match.group(2).strip()
        
        if name not in lock_deps:
            errors.append(f"Package '{name}' is in spec but missing from lockfile")
            out_of_sync = True
            continue
            
        locked_ver = lock_deps[name]
        
        if spec:
            # Strip markers from spec (e.g. "==1.15.0; python_version < '3'" -> "==1.15.0")
            spec_no_marker = spec.split(';')[0].strip()
            # Parse specifier op and version
            spec_match = re.match(r'^([>=<!~]+)(.*)$', spec_no_marker)
            if spec_match:
                op = spec_match.group(1).strip()
                req_ver = spec_match.group(2).strip()
                if not compare_versions(locked_ver, op, req_ver):
                    errors.append(f"Locked version '{name}=={locked_ver}' does not satisfy requirement '{dep}'")
                    out_of_sync = True
            else:
                errors.append(f"Unsupported specifier format for '{name}': {spec}")
                out_of_sync = True
                
    if out_of_sync:
        print(f"Spec and lockfile {os.path.basename(lock_path)} are OUT OF SYNC:")
        for err in errors:
            print(f"  - {err}")
        return 1
        
    print(f"Spec and lockfile {os.path.basename(lock_path)} are IN SYNC")
    return 0

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: verify_lockfile.py <spec_file> <lock_file>")
        sys.exit(1)
    sys.exit(verify(sys.argv[1], sys.argv[2]))
