#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for verify_lockfile.py"""

import os
import shutil
import sys
import tempfile
import unittest

DEPOT_TOOLS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DEPOT_TOOLS_ROOT)

from testing_support import coverage_utils
import verify_lockfile


class VerifyLockfileTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="verify_lockfile_test_")
        self.addCleanup(shutil.rmtree, self.test_dir, ignore_errors=True)

    def test_normalize_name(self):
        self.assertEqual(
            verify_lockfile.normalize_name("Package_Name.Foo"),
            "package-name-foo",
        )
        self.assertEqual(verify_lockfile.normalize_name("six"), "six")
        self.assertEqual(verify_lockfile.normalize_name("Urllib3"), "urllib3")
        self.assertEqual(
            verify_lockfile.normalize_name("requests[security]"),
            "requests",
        )
        self.assertEqual(
            verify_lockfile.normalize_name("requests [security]"),
            "requests",
        )

    def test_normalize_name_consecutive_delimiters(self):
        self.assertEqual(
            verify_lockfile.normalize_name("foo__bar..baz"),
            "foo-bar-baz",
        )

    def test_compare_versions(self):
        self.assertTrue(
            verify_lockfile.compare_versions("1.2.3", "==", "1.2.3")
        )
        self.assertFalse(
            verify_lockfile.compare_versions("1.2.3", "==", "1.2.4")
        )

        self.assertTrue(
            verify_lockfile.compare_versions("1.15.0", ">=", "1.12")
        )
        self.assertFalse(
            verify_lockfile.compare_versions("1.10.0", ">=", "1.12")
        )

        self.assertTrue(
            verify_lockfile.compare_versions("1.10.0", "<=", "1.12")
        )
        self.assertFalse(
            verify_lockfile.compare_versions("1.15.0", "<=", "1.12")
        )

        self.assertTrue(verify_lockfile.compare_versions("2.0", ">", "1.9.9"))
        self.assertFalse(verify_lockfile.compare_versions("1.0", ">", "1.0"))

        self.assertTrue(verify_lockfile.compare_versions("1.0", "<", "2.0"))
        self.assertFalse(verify_lockfile.compare_versions("2.0", "<", "1.0"))

        self.assertTrue(verify_lockfile.compare_versions("1.0", "!=", "2.0"))
        self.assertFalse(verify_lockfile.compare_versions("1.0", "!=", "1.0"))

        self.assertTrue(
            verify_lockfile.compare_versions("1.2.3", "===", "1.2.3")
        )
        self.assertFalse(
            verify_lockfile.compare_versions("1.2.3", "===", "1.2.4")
        )

    def test_compatible_release_operator(self):
        self.assertTrue(
            verify_lockfile.compare_versions("2.28.5", "~=", "2.28.0")
        )
        self.assertFalse(
            verify_lockfile.compare_versions("3.0.0", "~=", "2.28.0")
        )

    def test_parse_vpython_toml(self):
        toml_path = os.path.join(self.test_dir, "vpython.toml")
        with open(toml_path, "w", encoding="utf-8") as f:
            f.write(
                "# Some comments\n"
                'requires-python = ">=3.8"\n'
                "dependencies = [\n"
                '    "six==1.15.0",\n'
                '    "requests>=2.28.0",\n'
                "]\n"
            )
        deps = verify_lockfile.parse_vpython_toml(toml_path)
        self.assertEqual(deps, ["six==1.15.0", "requests>=2.28.0"])

    def test_parse_vpython_toml_empty(self):
        toml_path = os.path.join(self.test_dir, "vpython.toml")
        with open(toml_path, "w", encoding="utf-8") as f:
            f.write("# Empty file\n")
        deps = verify_lockfile.parse_vpython_toml(toml_path)
        self.assertEqual(deps, [])

    def test_parse_vpython_toml_malformed(self):
        toml_path = os.path.join(self.test_dir, "vpython.toml")
        with open(toml_path, "w", encoding="utf-8") as f:
            f.write("invalid toml = [ unclosed array\n")
        deps = verify_lockfile.parse_vpython_toml(toml_path)
        self.assertIsNone(deps)

    def test_parse_vpython_toml_array_of_tables_root(self):
        toml_path = os.path.join(self.test_dir, "vpython.toml")
        with open(toml_path, "w", encoding="utf-8") as f:
            f.write("[[array_root]]\nfoo = 'bar'\n")
        deps = verify_lockfile.parse_vpython_toml(toml_path)
        self.assertIsNotNone(deps)

    def test_parse_vpython_toml_non_list_dependencies(self):
        toml_path = os.path.join(self.test_dir, "vpython.toml")
        with open(toml_path, "w", encoding="utf-8") as f:
            f.write('dependencies = "six"\n')
        deps = verify_lockfile.parse_vpython_toml(toml_path)
        self.assertIsNone(deps)

    def test_parse_pep723_script_block(self):
        script_path = os.path.join(self.test_dir, "script.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(
                "#!/usr/bin/env vpython3\n"
                "# /// script\n"
                "# dependencies = [\n"
                '#   "six==1.15.0",\n'
                '#   "requests>=2.28.0",\n'
                "# ]\n"
                "# ///\n"
                "import six\n"
            )
        deps = verify_lockfile.parse_pep723(script_path)
        self.assertEqual(deps, ["six==1.15.0", "requests>=2.28.0"])

    def test_parse_pep723_embedded_in_docstring(self):
        script_path = os.path.join(self.test_dir, "script.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(
                '"""\n'
                "Example of PEP 723 format:\n"
                "# /// script\n"
                "# dependencies = ['six']\n"
                "# ///\n"
                '"""\n'
                "print('Hello world')\n"
            )
        deps = verify_lockfile.parse_pep723(script_path)
        self.assertEqual(deps, [])

    def test_parse_pep723_no_trailing_newline(self):
        script_path = os.path.join(self.test_dir, "script.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(
                "# /// script\n"
                "# dependencies = [\n"
                '#   "six==1.15.0",\n'
                "# ]\n"
                "# ///"
            )
        deps = verify_lockfile.parse_pep723(script_path)
        self.assertEqual(deps, ["six==1.15.0"])

    def test_parse_pep723_indented_comment_lines(self):
        script_path = os.path.join(self.test_dir, "script.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(
                "  # /// script\n"
                "  # dependencies = [\n"
                '  #   "six==1.15.0",\n'
                "  # ]\n"
                "  # ///\n"
            )
        deps = verify_lockfile.parse_pep723(script_path)
        self.assertEqual(deps, ["six==1.15.0"])

    def test_parse_pep723_tab_comment(self):
        script_path = os.path.join(self.test_dir, "script.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(
                "# /// script\n"
                "#\tdependencies = [\n"
                '#\t  "six==1.15.0",\n'
                "#\t]\n"
                "# ///\n"
            )
        deps = verify_lockfile.parse_pep723(script_path)
        self.assertEqual(deps, ["six==1.15.0"])

    def test_parse_pep723_vpython_block(self):
        script_path = os.path.join(self.test_dir, "script.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(
                "# /// vpython\n"
                "# dependencies = [\n"
                '#   "urllib3==2.0.0",\n'
                "# ]\n"
                "# ///\n"
            )
        deps = verify_lockfile.parse_pep723(script_path)
        self.assertEqual(deps, ["urllib3==2.0.0"])

    def test_parse_pep723_unclosed_block(self):
        script_path = os.path.join(self.test_dir, "script.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(
                "# /// script\n"
                "# dependencies = [\n"
                '#   "six==1.15.0",\n'
                "# ]\n"
                "# missing closing tag\n"
            )
        deps = verify_lockfile.parse_pep723(script_path)
        self.assertIsNone(deps)

    def test_parse_pep723_no_block(self):
        script_path = os.path.join(self.test_dir, "script.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("print('Hello world')\n")
        deps = verify_lockfile.parse_pep723(script_path)
        self.assertEqual(deps, [])

    def test_parse_uv_lock_requirements_style(self):
        lock_path = os.path.join(self.test_dir, "vpython.toml.uv.lock")
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write(
                "# UV lockfile (requirements format)\n"
                "six==1.15.0 # requested by dep\n"
                "requests==2.31.0; python_version < '4'\n"
                "urllib-3==2.0.0 \\\n"
                "    --hash=sha256:12345\n"
            )
        deps = verify_lockfile.parse_uv_lock(lock_path)
        self.assertEqual(
            deps,
            {
                "six": ["1.15.0"],
                "requests": ["2.31.0"],
                "urllib-3": ["2.0.0"],
            },
        )

    def test_parse_uv_lock_toml_style(self):
        lock_path = os.path.join(self.test_dir, "vpython.toml.uv.lock")
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write(
                "version = 1\n"
                "revision = 1\n\n"
                "[[package]]\n"
                'name = "requests"\n'
                'version = "2.31.0"\n\n'
                "[[package]]\n"
                'name = "six"\n'
                'version = "1.16.0"\n'
            )
        deps = verify_lockfile.parse_uv_lock(lock_path)
        self.assertEqual(
            deps,
            {
                "requests": ["2.31.0"],
                "six": ["1.16.0"],
            },
        )

    def test_parse_uv_lock_unversioned_package(self):
        lock_path = os.path.join(self.test_dir, "vpython.toml.uv.lock")
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write(
                "version = 1\n\n"
                "[[package]]\n"
                'name = "local-package"\n'
                "source = { path = '../local-package' }\n"
            )
        deps = verify_lockfile.parse_uv_lock(lock_path)
        self.assertEqual(deps, {"local-package": ["0.0.0"]})

    def test_parse_uv_lock_corrupted_toml(self):
        lock_path = os.path.join(self.test_dir, "vpython.toml.uv.lock")
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write("[[package]]\nname = [ invalid toml\n")
        deps = verify_lockfile.parse_uv_lock(lock_path)
        self.assertIsNone(deps)

    def test_parse_uv_lock_empty_toml(self):
        lock_path = os.path.join(self.test_dir, "vpython.toml.uv.lock")
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write("version = 1\nrevision = 1\n")
        deps = verify_lockfile.parse_uv_lock(lock_path)
        self.assertEqual(deps, {})

    def test_parse_uv_lock_multiple_package_versions(self):
        lock_path = os.path.join(self.test_dir, "vpython.toml.uv.lock")
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write(
                "version = 1\n\n"
                "[[package]]\n"
                'name = "six"\n'
                'version = "1.15.0"\n\n'
                "[[package]]\n"
                'name = "six"\n'
                'version = "1.16.0"\n'
            )
        deps = verify_lockfile.parse_uv_lock(lock_path)
        self.assertEqual(deps, {"six": ["1.15.0", "1.16.0"]})

    def test_parse_uv_lock_missing(self):
        lock_path = os.path.join(self.test_dir, "nonexistent.uv.lock")
        deps = verify_lockfile.parse_uv_lock(lock_path)
        self.assertIsNone(deps)

    def test_verify_success_toml(self):
        spec_path = os.path.join(self.test_dir, "vpython.toml")
        lock_path = spec_path + ".uv.lock"

        with open(spec_path, "w", encoding="utf-8") as f:
            f.write(
                "dependencies = [\n"
                '    "six==1.15.0",\n'
                '    "requests>=2.28.0,<3.0.0",\n'
                "]\n"
            )

        with open(lock_path, "w", encoding="utf-8") as f:
            f.write(
                "version = 1\n\n"
                "[[package]]\n"
                'name = "six"\n'
                'version = "1.15.0"\n\n'
                "[[package]]\n"
                'name = "requests"\n'
                'version = "2.31.0"\n'
            )

        ret = verify_lockfile.verify(spec_path, lock_path)
        self.assertEqual(ret, 0)

    def test_verify_dependency_with_extras(self):
        spec_path = os.path.join(self.test_dir, "vpython.toml")
        lock_path = spec_path + ".uv.lock"

        with open(spec_path, "w", encoding="utf-8") as f:
            f.write('dependencies = ["requests[security]>=2.28.0"]\n')

        with open(lock_path, "w", encoding="utf-8") as f:
            f.write(
                "version = 1\n\n"
                "[[package]]\n"
                'name = "requests"\n'
                'version = "2.31.0"\n'
            )

        ret = verify_lockfile.verify(spec_path, lock_path)
        self.assertEqual(ret, 0)

    def test_verify_non_string_dependency(self):
        spec_path = os.path.join(self.test_dir, "vpython.toml")
        lock_path = spec_path + ".uv.lock"

        with open(spec_path, "w", encoding="utf-8") as f:
            f.write("dependencies = [123]\n")

        with open(lock_path, "w", encoding="utf-8") as f:
            f.write("six==1.15.0\n")

        ret = verify_lockfile.verify(spec_path, lock_path)
        self.assertEqual(ret, 1)

    def test_verify_success_pep723(self):
        spec_path = os.path.join(self.test_dir, "foo.py")
        lock_path = spec_path + ".uv.lock"

        with open(spec_path, "w", encoding="utf-8") as f:
            f.write(
                "# /// script\n"
                "# dependencies = [\n"
                '#   "six==1.15.0",\n'
                "# ]\n"
                "# ///\n"
            )

        with open(lock_path, "w", encoding="utf-8") as f:
            f.write("six==1.15.0\n")

        ret = verify_lockfile.verify(spec_path, lock_path)
        self.assertEqual(ret, 0)

    def test_verify_no_deps_spec(self):
        spec_path = os.path.join(self.test_dir, "foo.py")
        lock_path = spec_path + ".uv.lock"

        with open(spec_path, "w", encoding="utf-8") as f:
            f.write("print('No dependencies here')\n")

        ret = verify_lockfile.verify(spec_path, lock_path)
        self.assertEqual(ret, 0)

    def test_verify_missing_spec(self):
        spec_path = os.path.join(self.test_dir, "nonexistent.toml")
        lock_path = spec_path + ".uv.lock"
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write("version = 1\n")

        ret = verify_lockfile.verify(spec_path, lock_path)
        self.assertEqual(ret, 1)

    def test_verify_both_deleted(self):
        spec_path = os.path.join(self.test_dir, "deleted.toml")
        lock_path = spec_path + ".uv.lock"

        ret = verify_lockfile.verify(spec_path, lock_path)
        self.assertEqual(ret, 0)

    def test_verify_missing_lockfile(self):
        spec_path = os.path.join(self.test_dir, "vpython.toml")
        lock_path = spec_path + ".uv.lock"

        with open(spec_path, "w", encoding="utf-8") as f:
            f.write('dependencies = ["six==1.15.0"]\n')

        ret = verify_lockfile.verify(spec_path, lock_path)
        self.assertEqual(ret, 1)

    def test_verify_missing_dep_in_lock(self):
        spec_path = os.path.join(self.test_dir, "vpython.toml")
        lock_path = spec_path + ".uv.lock"

        with open(spec_path, "w", encoding="utf-8") as f:
            f.write('dependencies = ["six==1.15.0", "requests>=2.28.0"]\n')

        with open(lock_path, "w", encoding="utf-8") as f:
            f.write("six==1.15.0\n")

        ret = verify_lockfile.verify(spec_path, lock_path)
        self.assertEqual(ret, 1)

    def test_verify_unsatisfied_version(self):
        spec_path = os.path.join(self.test_dir, "vpython.toml")
        lock_path = spec_path + ".uv.lock"

        with open(spec_path, "w", encoding="utf-8") as f:
            f.write('dependencies = ["requests>=2.28.0"]\n')

        with open(lock_path, "w", encoding="utf-8") as f:
            f.write("requests==2.20.0\n")

        ret = verify_lockfile.verify(spec_path, lock_path)
        self.assertEqual(ret, 1)

    def test_fallback_manual_version_comparison(self):
        orig_version = verify_lockfile.Version
        orig_requirement = verify_lockfile.Requirement
        orig_specifiers = verify_lockfile.SpecifierSet
        try:
            verify_lockfile.Version = None
            verify_lockfile.Requirement = None
            verify_lockfile.SpecifierSet = None

            # Check unconstrained dependency e.g. ["six"]
            spec_path_unconstrained = os.path.join(
                self.test_dir, "unconstrained.toml"
            )
            lock_path_unconstrained = spec_path_unconstrained + ".uv.lock"
            with open(spec_path_unconstrained, "w", encoding="utf-8") as f:
                f.write('dependencies = ["six"]\n')
            with open(lock_path_unconstrained, "w", encoding="utf-8") as f:
                f.write("six==1.15.0\n")

            self.assertEqual(
                verify_lockfile.verify(
                    spec_path_unconstrained, lock_path_unconstrained
                ),
                0,
            )

            # Check version equality normalization e.g. 1.2.0 == 1.2
            self.assertTrue(
                verify_lockfile.compare_versions("1.2.0", "==", "1.2")
            )
            self.assertTrue(
                verify_lockfile.compare_versions("1.2", "==", "1.2.0")
            )

            # Check arbitrary equality operator (===)
            self.assertTrue(
                verify_lockfile.compare_versions("1.2.3", "===", "1.2.3")
            )
            self.assertFalse(
                verify_lockfile.compare_versions("1.2.3", "===", "1.2.4")
            )

            # Check pre-release without trailing digits (e.g. 1.0.0b < 1.0.0)
            self.assertTrue(
                verify_lockfile.compare_versions("1.0.0b", "<", "1.0.0")
            )

            # Check dot-separated / hyphen-separated pre-release versions
            self.assertTrue(
                verify_lockfile.compare_versions("1.0.0-beta.1", "<", "1.0.0")
            )
            self.assertTrue(
                verify_lockfile.compare_versions("1.0.0.rc.2", "<", "1.0.0")
            )

            # Check pre-release and post-release ordering
            self.assertTrue(
                verify_lockfile.compare_versions("1.0.0a1", "<", "1.0.0")
            )
            self.assertTrue(
                verify_lockfile.compare_versions("1.0.0.post1", ">", "1.0.0")
            )

            # Check compatible release operator (~=)
            self.assertTrue(
                verify_lockfile.compare_versions("2.28.5", "~=", "2.28.0")
            )
            self.assertFalse(
                verify_lockfile.compare_versions("3.0.0", "~=", "2.28.0")
            )

            # Check fallback direct reference (@) dependency
            spec_path = os.path.join(self.test_dir, "vpython.toml")
            lock_path = spec_path + ".uv.lock"

            with open(spec_path, "w", encoding="utf-8") as f:
                f.write(
                    'dependencies = ["requests @ https://github.com/psf/requests/archive/main.zip"]\n'
                )

            with open(lock_path, "w", encoding="utf-8") as f:
                f.write("requests==2.31.0\n")

            ret = verify_lockfile.verify(spec_path, lock_path)
            self.assertEqual(ret, 0)
        finally:
            verify_lockfile.Version = orig_version
            verify_lockfile.Requirement = orig_requirement
            verify_lockfile.SpecifierSet = orig_specifiers

    def test_verify_environment_marker_universal_lockfile(self):
        spec_path = os.path.join(self.test_dir, "vpython.toml")
        lock_path = spec_path + ".uv.lock"

        with open(spec_path, "w", encoding="utf-8") as f:
            f.write(
                "dependencies = [\"pywin32>=300; sys_platform == 'win32'\"]\n"
            )

        with open(lock_path, "w", encoding="utf-8") as f:
            f.write(
                "version = 1\n\n"
                "[[package]]\n"
                'name = "pywin32"\n'
                'version = "306"\n'
            )

        ret = verify_lockfile.verify(spec_path, lock_path)
        self.assertEqual(ret, 0)

    def test_parse_pep723_tab_indented_markers_and_bounds(self):
        script_path = os.path.join(self.test_dir, "script.py")
        lock_path = script_path + ".uv.lock"

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(
                "# /// script\n"
                "#\tdependencies = [\n"
                "#\t  \"requests>=2.28.0; sys_platform == 'win32'\",\n"
                "#\t]\n"
                "# ///\n"
            )

        with open(lock_path, "w", encoding="utf-8") as f:
            f.write("requests==2.31.0\n")

        ret = verify_lockfile.verify(script_path, lock_path)
        self.assertEqual(ret, 0)

    def test_main_entrypoint(self):
        spec_path = os.path.join(self.test_dir, "vpython.toml")
        lock_path = spec_path + ".uv.lock"

        with open(spec_path, "w", encoding="utf-8") as f:
            f.write('dependencies = ["six==1.15.0"]\n')
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write("six==1.15.0\n")

        self.assertEqual(verify_lockfile.main([spec_path, lock_path]), 0)
        self.assertEqual(verify_lockfile.main([]), 1)


if __name__ == "__main__":
    sys.exit(
        coverage_utils.covered_main(
            [os.path.join(DEPOT_TOOLS_ROOT, "verify_lockfile.py")],
            required_percentage=85,
        )
    )
