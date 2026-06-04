#!/usr/bin/env python3
# Copyright 2026 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import re
import sys

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

try:
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
except ImportError:
    Requirement = None
    SpecifierSet = None
    Version = None


def normalize_name(name):
    # Strip extras e.g. requests[security] -> requests
    name = re.sub(r"\[.*\]", "", name)
    return name.lower().replace("_", "-").replace(".", "-")


def parse_version(ver_str):
    if Version is not None:
        try:
            return Version(ver_str)
        except Exception:
            pass
    # Fallback version parser tuple with pre-release support
    m = re.match(r"^(\d+(?:\.\d+)*)(?:([a-zA-Z]+)(\d+))?", ver_str)
    if m:
        nums = tuple(int(x) for x in m.group(1).split("."))
        if m.group(2):  # pre-release like b1, a1, rc1
            pre_num = int(m.group(3)) if m.group(3) else 0
            return nums + (0, pre_num)
        return nums + (1, 0)
    parts = [int(x) for x in re.findall(r"\d+", ver_str)]
    return tuple(parts)


def compare_versions(v1, op, v2):
    if SpecifierSet is not None and Version is not None:
        try:
            spec = SpecifierSet(f"{op}{v2}")
            ver = Version(v1)
            return spec.contains(ver, prereleases=True)
        except Exception:
            pass

    if op == "~=":
        parts = [int(x) for x in re.findall(r"\d+", v2)]
        if len(parts) >= 2:
            upper_parts = list(parts[:-1])
            upper_parts[-1] += 1
            upper_v = ".".join(str(x) for x in upper_parts)
            return compare_versions(v1, ">=", v2) and compare_versions(
                v1, "<", upper_v
            )
        return compare_versions(v1, ">=", v2)

    t1 = parse_version(v1)
    t2 = parse_version(v2)
    if isinstance(t1, tuple) and isinstance(t2, tuple):
        max_len = max(len(t1), len(t2))
        t1 = t1 + (0,) * (max_len - len(t1))
        t2 = t2 + (0,) * (max_len - len(t2))

    if op == "==":
        return t1 == t2
    if op == ">=":
        return t1 >= t2
    if op == "<=":
        return t1 <= t2
    if op == ">":
        return t1 > t2
    if op == "<":
        return t1 < t2
    if op == "!=":
        return t1 != t2
    return False


def parse_vpython_toml(path):
    if tomllib is None:
        print("Error: tomllib module not available.")
        return None
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return data.get("dependencies", [])
    except Exception as e:
        print(f"Error: Failed to parse TOML in {path}: {e}")
        return None


def parse_pep723(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    match = re.search(
        r"^#\s*///\s*(script|vpython)\s*\n(.*?)\n#\s*///\s*$",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return []

    block_content = match.group(2)
    toml_lines = []
    for line in block_content.splitlines():
        if line.startswith("# "):
            toml_lines.append(line[2:])
        elif line.startswith("#"):
            toml_lines.append(line[1:])
        else:
            toml_lines.append(line)
    toml_content = "\n".join(toml_lines)

    if tomllib is None:
        print("Error: tomllib module not available.")
        return None
    try:
        data = tomllib.loads(toml_content)
        return data.get("dependencies", [])
    except Exception as e:
        print(f"Error: Failed to parse PEP 723 inline TOML in {path}: {e}")
        return None


def parse_uv_lock(path):
    if not os.path.exists(path):
        return None

    # First attempt TOML parsing (standard uv.lock format)
    if tomllib is not None:
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
            if isinstance(data, dict) and "package" in data:
                deps = {}
                for pkg in data.get("package", []):
                    if "name" in pkg and "version" in pkg:
                        name = normalize_name(pkg["name"])
                        ver = pkg["version"]
                        deps.setdefault(name, []).append(ver)
                return deps
        except Exception:
            pass

    # Fallback to requirements.txt-style parsing (lines formatted as name==version)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = []
    current_line = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            current_line.append(line[:-1].strip())
        else:
            current_line.append(line)
            lines.append(" ".join(current_line))
            current_line = []

    deps = {}
    for line in lines:
        pkg_spec = line.split(";")[0].split("--")[0].strip()
        if "==" in pkg_spec:
            name, ver = pkg_spec.split("==", 1)
            name = normalize_name(name.strip())
            deps.setdefault(name, []).append(ver.strip())

    return deps


def verify(spec_path, lock_path):
    if not os.path.exists(spec_path):
        print(f"Error: Spec file not found at {spec_path}")
        return 1

    if spec_path.endswith(".py"):
        toml_deps = parse_pep723(spec_path)
    else:
        toml_deps = parse_vpython_toml(spec_path)

    if toml_deps is None:
        return 1

    if not toml_deps:
        return 0

    lock_deps = parse_uv_lock(lock_path)
    if lock_deps is None:
        print(f"Error: Lockfile {lock_path} is missing!")
        return 1

    out_of_sync = False
    errors = []

    for dep in toml_deps:
        if (
            Requirement is not None
            and SpecifierSet is not None
            and Version is not None
        ):
            try:
                req = Requirement(dep)
                name = normalize_name(req.name)
                if name not in lock_deps:
                    errors.append(
                        f"Package '{name}' is in spec but missing from lockfile"
                    )
                    out_of_sync = True
                    continue

                locked_versions = lock_deps[name]
                satisfied = False
                for ver_str in locked_versions:
                    try:
                        if req.specifier.contains(
                            Version(ver_str), prereleases=True
                        ):
                            satisfied = True
                            break
                    except Exception:
                        pass

                if not satisfied:
                    errors.append(
                        f"No locked version of '{name}' ({locked_versions}) satisfies requirement '{dep}'"
                    )
                    out_of_sync = True
                continue
            except Exception:
                pass

        # Fallback manual requirement parsing
        match = re.match(r"^([^>=<!~;]+)(.*)$", dep)
        if not match:
            errors.append(f"Invalid dependency format in spec: {dep}")
            out_of_sync = True
            continue

        name = normalize_name(match.group(1).strip())
        spec = match.group(2).strip()

        if name not in lock_deps:
            errors.append(
                f"Package '{name}' is in spec but missing from lockfile"
            )
            out_of_sync = True
            continue

        locked_versions = lock_deps[name]

        if spec:
            spec_no_marker = spec.split(";")[0].strip()
            clauses = [
                c.strip() for c in spec_no_marker.split(",") if c.strip()
            ]
            satisfied = False
            for locked_ver in locked_versions:
                clause_ok = True
                for clause in clauses:
                    spec_match = re.match(r"^([>=<!~]+)(.*)$", clause)
                    if spec_match:
                        op = spec_match.group(1).strip()
                        req_ver = spec_match.group(2).strip()
                        if not compare_versions(locked_ver, op, req_ver):
                            clause_ok = False
                            break
                    else:
                        clause_ok = False
                        break
                if clause_ok:
                    satisfied = True
                    break

            if not satisfied:
                errors.append(
                    f"No locked version of '{name}' ({locked_versions}) satisfies requirement '{dep}'"
                )
                out_of_sync = True

    if out_of_sync:
        print(
            f"Spec and lockfile {os.path.basename(lock_path)} are OUT OF SYNC:"
        )
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"Spec and lockfile {os.path.basename(lock_path)} are IN SYNC")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: verify_lockfile.py <spec_file> <lock_file>")
        sys.exit(1)
    sys.exit(verify(sys.argv[1], sys.argv[2]))
