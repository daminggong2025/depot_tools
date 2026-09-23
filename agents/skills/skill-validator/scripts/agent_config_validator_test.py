#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for agent_config_validator.py."""

from __future__ import annotations

import os
import tempfile
import unittest

from agent_config_validator import main
from agent_config_validator import validate_agent_configs


class AgentConfigsTestCase(unittest.TestCase):
    def test_repo_agent_configs(self) -> None:
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
    def setUp(self) -> None:
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
                "---\nname: my-skill\ndescription: Validates skills.\n---\n"
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        super().tearDown()

    def test_valid_agent_config(self) -> None:
        proto_text = (
            "configs {\n"
            '  id: "custom-agent-id"\n'
            '  display_name: "My Skill"\n'
            '  description: "Custom agent description."\n'
            '  skills: "agents/skills/my-skill/SKILL.md"\n'
            "  include_filters {\n"
            r'    path_regex: ".*\\.py$"'
            "\n"
            "  }\n"
            "}\n"
        )
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertEqual(errors, [])

    def test_valid_shorthand_skill_name(self) -> None:
        proto_text = (
            "configs {\n"
            '  id: "custom-agent-id"\n'
            '  skills: "my-skill"\n'
            "  include_filters {\n"
            r'    path_regex: ".*\\.py$"'
            "\n"
            "  }\n"
            "}\n"
        )
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertEqual(errors, [])

    def test_syntax_error(self) -> None:
        proto_text = "configs { invalid syntax\n"
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertTrue(any("syntax error" in e.lower() for e in errors))

    def test_missing_skills_file(self) -> None:
        proto_text = (
            "configs {\n"
            '  id: "crossbench-my-skill"\n'
            '  description: "Validates skills."\n'
            '  skills: "agents/skills/nonexistent/SKILL.md"\n'
            "  include_filters {\n"
            r'    path_regex: ".*\\.py$"'
            "\n"
            "  }\n"
            "}\n"
        )
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertTrue(any("does not exist" in e for e in errors))

    def test_path_traversal_rejected(self) -> None:
        proto_text = (
            "configs {\n"
            '  id: "crossbench-my-skill"\n'
            '  skills: "../outside/SKILL.md"\n'
            "  include_filters {\n"
            r'    path_regex: ".*\\.py$"'
            "\n"
            "  }\n"
            "}\n"
        )
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertTrue(any("does not exist" in e for e in errors))

    def test_missing_include_filters(self) -> None:
        proto_text = (
            "configs {\n"
            '  id: "crossbench-my-skill"\n'
            '  skills: "agents/skills/my-skill/SKILL.md"\n'
            "}\n"
        )
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertTrue(any("missing 'include_filters'" in e for e in errors))

    def test_empty_filter_and_empty_regex(self) -> None:
        proto_text = (
            "configs {\n"
            '  id: "crossbench-my-skill"\n'
            '  skills: "agents/skills/my-skill/SKILL.md"\n'
            "  include_filters {}\n"
            "  exclude_filters {\n"
            '    path_regex: "   "\n'
            "  }\n"
            "}\n"
        )
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertTrue(any("empty include_filters entry" in e for e in errors))
        self.assertTrue(any("empty exclude path_regex" in e for e in errors))

    def test_invalid_include_path_regex(self) -> None:
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

    def test_invalid_exclude_path_regex(self) -> None:
        proto_text = (
            "configs {\n"
            '  id: "crossbench-my-skill"\n'
            '  skills: "agents/skills/my-skill/SKILL.md"\n'
            "  include_filters {\n"
            r'    path_regex: ".*\\.py$"'
            "\n"
            "  }\n"
            "  exclude_filters {\n"
            '    path_regex: "(unclosed"\n'
            "  }\n"
            "}\n"
        )
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertTrue(any("invalid exclude path_regex" in e for e in errors))

    def test_duplicate_agent_id(self) -> None:
        proto_text = (
            "configs {\n"
            '  id: "crossbench-my-skill"\n'
            '  skills: "agents/skills/my-skill/SKILL.md"\n'
            "  include_filters {\n"
            r'    path_regex: ".*\\.py$"'
            "\n"
            "  }\n"
            "}\n"
            "configs {\n"
            '  id: "crossbench-my-skill"\n'
            '  skills: "agents/skills/my-skill/SKILL.md"\n'
            "  include_filters {\n"
            r'    path_regex: ".*\\.py$"'
            "\n"
            "  }\n"
            "}\n"
        )
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertTrue(any("Duplicate agent id found" in e for e in errors))

    def test_cli_main_valid_and_invalid(self) -> None:
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
                r'    path_regex: ".*\\.py$"'
                "\n"
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
