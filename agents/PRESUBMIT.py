# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Presubmit script for //agents.

See http://dev.chromium.org/developers/how-tos/depottools/presubmit-scripts
for more details about the presubmit API built into depot_tools.
"""

PRESUBMIT_VERSION = '2.0.0'


def CheckSkillFiles(input_api, output_api):
    """Validates SKILL.md files have correct frontmatter and formatting."""
    return input_api.canned_checks.CheckSkillFiles(input_api, output_api)
