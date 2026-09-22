#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for agent_config_validator.py."""

import os
import tempfile
import unittest

from agent_config_validator import main
from agent_config_validator import validate_agent_configs


class AgentConfigsTestCase(unittest.TestCase):
    def test_repo_agent_configs(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        repo_root = os.path.abspath(
            os.path.join(script_dir, "..", "..", "..", "..")
        )
        config_file = os.path.join(
            repo_root, "depot_tools_agents", "agent_configs.txtpb"
        )
        self.assertTrue(
            os.path.isfile(config_file), f"Missing config file: {config_file}"
        )
        with open(config_file, "r", encoding="utf-8") as f:
            content = f.read()
        errors = validate_agent_configs(
            content, repo_root, config_path=config_file
        )
        error_details = "\n".join(errors)
        self.assertEqual(
            errors,
            [],
            f"Validation errors in depot_tools_agents/agent_configs.txtpb:\n"
            f"{error_details}",
        )


class AgentConfigsValidatorMockTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = self.temp_dir.name
        self.skill_dir = os.path.join(
            self.root_path, "agents", "skills", "my-skill"
        )
        os.makedirs(self.skill_dir, exist_ok=True)
        self.skill_file = os.path.join(self.skill_dir, "SKILL.md")
        with open(self.skill_file, "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "name: My Skill\n"
                "id: crossbench-my-skill\n"
                "description: Validates skills.\n"
                "---\n"
            )

    def tearDown(self):
        self.temp_dir.cleanup()
        super().tearDown()

    def test_valid_agent_config(self):
        proto_text = (
            "configs {\n"
            '  id: "crossbench-my-skill"\n'
            '  display_name: "My Skill"\n'
            '  description: "Validates skills."\n'
            '  skills: "agents/skills/my-skill/SKILL.md"\n'
            "  include_filters {\n"
            '    path_regex: ".*\\\\.py$"\n'
            "  }\n"
            "}\n"
        )
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertEqual(errors, [])

    def test_syntax_error(self):
        proto_text = "configs { invalid syntax\n"
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertTrue(any("syntax error" in e.lower() for e in errors))

    def test_missing_skills_file(self):
        proto_text = (
            "configs {\n"
            '  id: "crossbench-my-skill"\n'
            '  description: "Validates skills."\n'
            '  skills: "agents/skills/nonexistent/SKILL.md"\n'
            "  include_filters {\n"
            '    path_regex: ".*\\\\.py$"\n'
            "  }\n"
            "}\n"
        )
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertTrue(any("does not exist" in e for e in errors))

    def test_id_mismatch(self):
        proto_text = (
            "configs {\n"
            '  id: "crossbench-wrong-id"\n'
            '  description: "Validates skills."\n'
            '  skills: "agents/skills/my-skill/SKILL.md"\n'
            "  include_filters {\n"
            '    path_regex: ".*\\\\.py$"\n'
            "  }\n"
            "}\n"
        )
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertTrue(
            any("does not match frontmatter id/name" in e for e in errors)
        )

    def test_description_mismatch(self):
        proto_text = (
            "configs {\n"
            '  id: "crossbench-my-skill"\n'
            '  description: "Different description."\n'
            '  skills: "agents/skills/my-skill/SKILL.md"\n'
            "  include_filters {\n"
            '    path_regex: ".*\\\\.py$"\n'
            "  }\n"
            "}\n"
        )
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertTrue(any("does not match description" in e for e in errors))

    def test_invalid_path_regex(self):
        proto_text = (
            "configs {\n"
            '  id: "crossbench-my-skill"\n'
            '  description: "Validates skills."\n'
            '  skills: "agents/skills/my-skill/SKILL.md"\n'
            "  include_filters {\n"
            '    path_regex: "[invalid regex"\n'
            "  }\n"
            "}\n"
        )
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertTrue(any("invalid include path_regex" in e for e in errors))

    def test_skill_missing_frontmatter(self):
        with open(self.skill_file, "w", encoding="utf-8") as f:
            f.write("No frontmatter here\n")
        proto_text = (
            "configs {\n"
            '  id: "crossbench-my-skill"\n'
            '  description: "Validates skills."\n'
            '  skills: "agents/skills/my-skill/SKILL.md"\n'
            "  include_filters {\n"
            '    path_regex: ".*\\\\.py$"\n'
            "  }\n"
            "}\n"
        )
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertTrue(
            any(
                "Missing starting YAML frontmatter delimiter" in e
                for e in errors
            )
        )

    def test_skill_empty_frontmatter(self):
        with open(self.skill_file, "w", encoding="utf-8") as f:
            f.write("---\n---\n")
        proto_text = (
            "configs {\n"
            '  id: "crossbench-my-skill"\n'
            '  description: "Validates skills."\n'
            '  skills: "agents/skills/my-skill/SKILL.md"\n'
            "  include_filters {\n"
            '    path_regex: ".*\\\\.py$"\n'
            "  }\n"
            "}\n"
        )
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertTrue(any("YAML frontmatter is empty" in e for e in errors))

    def test_duplicate_agent_id(self):
        proto_text = (
            "configs {\n"
            '  id: "crossbench-my-skill"\n'
            '  description: "Validates skills."\n'
            '  skills: "agents/skills/my-skill/SKILL.md"\n'
            "  include_filters {\n"
            '    path_regex: ".*\\\\.py$"\n'
            "  }\n"
            "}\n"
            "configs {\n"
            '  id: "crossbench-my-skill"\n'
            '  description: "Validates skills."\n'
            '  skills: "agents/skills/my-skill/SKILL.md"\n'
            "  include_filters {\n"
            '    path_regex: ".*\\\\.py$"\n'
            "  }\n"
            "}\n"
        )
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertTrue(any("Duplicate agent id found" in e for e in errors))

    def test_cli_main_valid_and_invalid(self):
        config_path = os.path.join(
            self.root_path, "agents", "agent_configs.textproto"
        )
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                "configs {\n"
                '  id: "crossbench-my-skill"\n'
                '  display_name: "My Skill"\n'
                '  description: "Validates skills."\n'
                '  skills: "agents/skills/my-skill/SKILL.md"\n'
                "  include_filters {\n"
                '    path_regex: ".*\\\\.py$"\n'
                "  }\n"
                "}\n"
            )
        self.assertEqual(
            main(
                [
                    "agent_config_validator.py",
                    "--root",
                    self.root_path,
                    config_path,
                ]
            ),
            0,
        )

        with open(config_path, "w", encoding="utf-8") as f:
            f.write("configs { broken\n")
        self.assertEqual(
            main(
                [
                    "agent_config_validator.py",
                    "--root",
                    self.root_path,
                    config_path,
                ]
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
