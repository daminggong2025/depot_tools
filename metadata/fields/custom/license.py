#!/usr/bin/env python3
# Copyright 2023 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import re
import sys
from typing import List, Tuple, Optional

_THIS_DIR = os.path.abspath(os.path.dirname(__file__))
# The repo's root directory.
_ROOT_DIR = os.path.abspath(os.path.join(_THIS_DIR, "..", "..", ".."))
# Bad delimiter characters.
BAD_DELIMITERS = ["/", ";", " and ", " or "]
BAD_DELIMITERS_REGEX = re.compile("|".join(re.escape(delimiter) for delimiter in BAD_DELIMITERS))

# Add the repo's root directory for clearer imports.
sys.path.insert(0, _ROOT_DIR)

import metadata.fields.field_types as field_types
import metadata.fields.util as util
import metadata.validation_result as vr
import metadata.fields.custom.license_allowlist as allowlist_util


class LicenseField(field_types.SingleLineTextField):
    """Custom field for the package's license type(s).

    e.g. Apache-2.0, MIT, BSD-2.0
    """

    def __init__(self):
        super().__init__(name="License")

    def _process_licenses(self, value: str) -> List[Tuple[str, bool]]:
        """Process a license field value, which may list multiple licenses.

        Args:
            value: the value to process, which may include both verbose and
                atomic delimiters, e.g. "Apache, 2.0 and MIT and custom"
            atomic_delimiter: the delimiter to use as a final step; values
                will not be further split after using this
                delimiter.

        Returns: a list of the constituent licenses within the given value,
                and whether the constituent license is a recognized license type.
                e.g. [("Apache, 2.0", False), ("MIT", True),
                    ("custom", False)]
        """
        breakdown = []
        for atomic_value in self._extract_licenses(value):
            breakdown.append(
                (atomic_value,
                 allowlist_util.is_a_known_license(atomic_value, )))
        return breakdown

    def _extract_licenses(self, value: str) -> List[str]:
        """Split a license field value into its constituent licenses and process each.

        Args:
            value: the value to process, e.g. "Apache-2.0, LicenseRef-MIT, bad license"

        Returns: a list of the processed constituent licenses.
                e.g. ["Apache-2.0, MIT, bad license"]
        """
        breakdown = []
        # Split using the standard value delimiter. This results in
        # atomic values; there is no further splitting possible.
        for atomic_value in value.split(self.VALUE_DELIMITER):
            breakdown.append(allowlist_util.normalize_value(atomic_value))
        return breakdown

    def all_licenses_allowed(self, license_field_value: str,
                             is_open_source_project: bool) -> bool:
        """Returns whether all licenses in the field are allowlisted.
        Assumes a non-empty license_field_value"""
        for lic in self._extract_licenses(license_field_value):
            if not allowlist_util.is_license_allowed(
                    lic, is_open_source_project=is_open_source_project):
                return False
        return True

    def validate(self, value: str) -> Optional[vr.ValidationResult]:
        """Checks the given value consists of recognized license types.

        Note: this field supports multiple values.
        """
        not_allowlisted = []
        licenses = self._process_licenses(value)
        for license, allowed in licenses:
            if util.is_empty(license):
                return vr.ValidationError(
                    reason=f"{self._name} has an empty value.")
            if BAD_DELIMITERS_REGEX.search(license):
                return vr.ValidationError(
                    reason=f"{self._name} contains a bad license separator. "
                    "Separate licenses by commas only.",
                    # Try and preemptively address the root cause of this behaviour,
                    # which is having multiple choices for a license.
                    additional=[
                        "When given a choice of licenses, choose the most "
                        "permissive one, do not list all options."
                    ])
            if not allowed:
                # Preserve the original casing for the warning message.
                not_allowlisted.append(license)

        if not_allowlisted:
            return vr.ValidationWarning(
                reason="License not in the allowlist."
                " Follow the steps at https://source.chromium.org/chromium/chromium/tools/depot_tools/+/main:metadata/fields/custom/license_allowlist.py.",
                additional=[
                    "Licenses not allowlisted: "
                    f"{util.quoted(not_allowlisted)}.",
                ])

        return None

    def filter_open_source_project_only_licenses(
            self, license_field_value: str) -> List[str]:
        """Returns a list of licenses that are only allowed in open source projects."""
        return list(
            filter(
                allowlist_util.is_open_source_license,
                self._extract_licenses(license_field_value),
            ))

    def narrow_type(self, value: str) -> Optional[List[str]]:
        if not value:
            # Empty License field is equivalent to "not declared".
            return None

        parts = value.split(self.VALUE_DELIMITER)
        return list(filter(bool, map(lambda str: str.strip(), parts)))
