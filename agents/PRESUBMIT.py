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
    # TODO(ziwlin): Migrate to input_api.canned_checks.CheckSkillFiles() once
    # presubmit_canned_checks.CheckSkillFiles lands in depot_tools.
    skill_files = [
        f.AbsoluteLocalPath()
        for f in input_api.AffectedFiles(include_deletes=False)
        if f.LocalPath().endswith('SKILL.md')
    ]

    if not skill_files:
        return []

    validator_path = input_api.os_path.join(input_api.PresubmitLocalPath(),
                                            'skills', 'skill-validator',
                                            'scripts', 'skill_validator.py')

    cmd = ['vpython3', validator_path] + skill_files
    try:
        p = input_api.subprocess.Popen(cmd,
                                       stdout=input_api.subprocess.PIPE,
                                       stderr=input_api.subprocess.PIPE)
        stdout, stderr = p.communicate()
    except OSError as e:
        return [
            output_api.PresubmitError(f'Failed to run skill validator: {e}.\n'
                                      'Is vpython3 in your PATH?')
        ]

    if p.returncode != 0:
        message = (f'Skill validator ({validator_path}) failed with exit '
                   f'code {p.returncode}.')
        long_text = (f'STDOUT:\n{stdout.decode("utf-8", "replace")}\n'
                     f'STDERR:\n{stderr.decode("utf-8", "replace")}\n')
        return [output_api.PresubmitError(message, long_text=long_text)]

    return []
