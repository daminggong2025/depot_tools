#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for agent_config_validator.py."""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from agent_config_validator import main, validate_agent_configs


class AgentConfigsTestCase(unittest.TestCase):
    def test_repo_agent_configs(self) -> None:
        repo_root = pathlib.Path(__file__).parents[4]
        config_file = repo_root / "depot_tools_agents" / "agent_configs.txtpb"
        self.assertTrue(
            config_file.is_file(), f"Missing config file: {config_file}"
        )
        content = config_file.read_text(encoding="utf-8")
        errors = validate_agent_configs(
            content, repo_root, config_path=config_file
        )
        self.assertEqual(
            errors,
            [],
            "Validation errors in depot_tools_agents/agent_configs.txtpb:\n"
            + "\n".join(errors),
        )


class AgentConfigsValidatorMockTestCase(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = pathlib.Path(self.temp_dir.name)
        self.skill_dir = self.root_path / "agents" / "skills" / "my-skill"
        self.skill_dir.mkdir(parents=True, exist_ok=True)
        self.skill_file = self.skill_dir / "SKILL.md"
        self.skill_file.write_text(
            "---\n"
            "name: My Skill\n"
            "id: crossbench-my-skill\n"
            "description: Validates skills.\n"
            "---\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        super().tearDown()

    def test_valid_agent_config(self) -> None:
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
            '    path_regex: ".*\\\\.py$"\n'
            "  }\n"
            "}\n"
        )
        errors = validate_agent_configs(proto_text, self.root_path)
        self.assertTrue(any("does not exist" in e for e in errors))

    def test_id_mismatch(self) -> None:
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

    def test_description_mismatch(self) -> None:
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

    def test_invalid_path_regex(self) -> None:
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

    def test_skill_missing_frontmatter(self) -> None:
        self.skill_file.write_text("No frontmatter here\n", encoding="utf-8")
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

    def test_skill_empty_frontmatter(self) -> None:
        self.skill_file.write_text("---\n---\n", encoding="utf-8")
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

    def test_duplicate_agent_id(self) -> None:
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

    def test_cli_main_valid_and_invalid(self) -> None:
        config_path = self.root_path / "agents" / "agent_configs.textproto"
        config_path.write_text(
            "configs {\n"
            '  id: "crossbench-my-skill"\n'
            '  display_name: "My Skill"\n'
            '  description: "Validates skills."\n'
            '  skills: "agents/skills/my-skill/SKILL.md"\n'
            "  include_filters {\n"
            '    path_regex: ".*\\\\.py$"\n'
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        self.assertEqual(
            main(
                [
                    "agent_config_validator.py",
                    "--root",
                    str(self.root_path),
                    str(config_path),
                ]
            ),
            0,
        )

        config_path.write_text("configs { broken\n", encoding="utf-8")
        self.assertEqual(
            main(
                [
                    "agent_config_validator.py",
                    "--root",
                    str(self.root_path),
                    str(config_path),
                ]
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
