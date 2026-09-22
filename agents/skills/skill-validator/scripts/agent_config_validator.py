#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Validates CRACS agent_configs.textproto / agent_configs.txtpb files."""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import Any, Final, Sequence

import yaml
from google.protobuf import descriptor_pb2, message_factory, text_format

_TYPE_STR: Final[int] = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
_TYPE_MSG: Final[int] = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
_TYPE_BOOL: Final[int] = descriptor_pb2.FieldDescriptorProto.TYPE_BOOL

_SCHEMA: Final[dict[str, list[tuple[Any, ...]]]] = {
    "Filter": [
        ("project", 1, _TYPE_STR, True),
        ("path_regex", 2, _TYPE_STR, True),
    ],
    "AgentConfig": [
        ("id", 1, _TYPE_STR, False),
        ("display_name", 2, _TYPE_STR, False),
        ("description", 3, _TYPE_STR, False),
        ("skills", 4, _TYPE_STR, True),
        ("include_filters", 5, _TYPE_MSG, True, "Filter"),
        ("exclude_filters", 6, _TYPE_MSG, True, "Filter"),
        ("automatic", 7, _TYPE_BOOL, False),
        ("usernames", 8, _TYPE_STR, True),
        ("mdb_groups", 9, _TYPE_STR, True),
        ("subagents", 10, _TYPE_STR, True),
        ("tags", 11, _TYPE_STR, True),
    ],
    "HostAgents": [("configs", 1, _TYPE_MSG, True, "AgentConfig")],
}


def create_host_agents_message_class() -> type[Any]:
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = "agent.proto"
    file_proto.package = (
        "google.corp.android.engprod.codereviewagentconfiguration.v1"
    )
    for msg_name, fields in _SCHEMA.items():
        m = file_proto.message_type.add(name=msg_name)
        for field_spec in fields:
            name, num, ftype, repeated = field_spec[:4]
            label = (
                descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
                if repeated
                else descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
            )
            f = m.field.add(name=name, number=num, type=ftype, label=label)
            if len(field_spec) > 4:
                f.type_name = field_spec[4]
    return message_factory.GetMessages([file_proto])[
        f"{file_proto.package}.HostAgents"
    ]


def extract_md_frontmatter(content: str) -> str:
    if not content.strip():
        raise ValueError("Markdown content is empty.")
    lines = content.splitlines()
    if lines[0].strip() != "---":
        raise ValueError("Missing starting YAML frontmatter delimiter '---'.")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i])
    raise ValueError("Missing closing YAML frontmatter delimiter '---'.")


def resolve_skill_path(
    skill_ref: str,
    root_path: pathlib.Path,
    config_path: pathlib.Path | None = None,
) -> pathlib.Path:
    clean_ref = skill_ref.strip("/")
    bases = [root_path]
    if config_path is not None:
        bases.append(config_path.parent / "skills")
    bases.extend(
        root_path / d / "skills"
        for d in ("agents", ".agents", "depot_tools_agents")
    )
    candidates = [
        (b / clean_ref)
        if (b / clean_ref).name.upper() == "SKILL.MD"
        else (b / clean_ref / "SKILL.md")
        for b in bases
    ]
    return next((c for c in candidates if c.is_file()), candidates[0])


def validate_skill_reference(
    agent_id: str,
    expected_desc: str,
    skill_ref: str,
    root_path: pathlib.Path,
    config_path: pathlib.Path | None = None,
) -> list[str]:
    skill_path = resolve_skill_path(skill_ref, root_path, config_path)
    if not skill_path.is_file():
        return [f"Agent '{agent_id}': skill file does not exist: '{skill_ref}'"]
    try:
        skill_content = skill_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return [f"Agent '{agent_id}': could not read skill '{skill_ref}': {e}"]
    try:
        frontmatter_raw = extract_md_frontmatter(skill_content).strip()
    except ValueError as e:
        return [f"Agent '{agent_id}': skill '{skill_ref}': {e}"]
    if not frontmatter_raw:
        return [
            f"Agent '{agent_id}': skill '{skill_ref}': YAML frontmatter is empty."
        ]
    try:
        frontmatter = yaml.safe_load(frontmatter_raw)
    except yaml.YAMLError as e:
        return [f"Agent '{agent_id}': invalid YAML in skill '{skill_ref}': {e}"]
    if not isinstance(frontmatter, dict):
        return [
            f"Agent '{agent_id}': skill '{skill_ref}' frontmatter not a dict."
        ]

    errors: list[str] = []
    fm_id = str(frontmatter.get("id") or frontmatter.get("name", "")).strip()
    if agent_id != fm_id and not (fm_id and agent_id.endswith(f"-{fm_id}")):
        errors.append(
            f"Agent id '{agent_id}' does not match frontmatter id/name "
            f"'{fm_id}' in '{skill_ref}'."
        )
    fm_desc = str(frontmatter.get("description", "")).strip()
    if fm_desc != expected_desc.strip():
        errors.append(
            f"Agent '{agent_id}': description in agent_configs.textproto does "
            f"not match description in '{skill_ref}'."
        )
    return errors


def validate_path_regexes(
    agent_id: str, filters: Sequence[Any], filter_kind: str
) -> list[str]:
    errors: list[str] = []
    for f in filters:
        for regex in f.path_regex:
            try:
                re.compile(regex)
            except re.error as e:
                errors.append(
                    f"Agent '{agent_id}': invalid {filter_kind} path_regex "
                    f"'{regex}': {e}"
                )
    return errors


def validate_agent_config(
    config: Any,
    root_path: pathlib.Path,
    config_path: pathlib.Path | None = None,
) -> list[str]:
    agent_id: str = config.id
    if not agent_id:
        return ["AgentConfig is missing required 'id' field."]
    errors: list[str] = []
    if not config.skills:
        errors.append(f"Agent '{agent_id}': missing 'skills' field.")
    else:
        for skill_ref in config.skills:
            errors.extend(
                validate_skill_reference(
                    agent_id,
                    config.description,
                    skill_ref,
                    root_path,
                    config_path=config_path,
                )
            )
    if not config.include_filters:
        errors.append(
            f"Agent '{agent_id}': missing 'include_filters' "
            f"(agent would be disabled by CRACS)."
        )
    else:
        errors.extend(
            validate_path_regexes(agent_id, config.include_filters, "include")
        )
    errors.extend(
        validate_path_regexes(agent_id, config.exclude_filters, "exclude")
    )
    return errors


def validate_agent_configs(
    content: str,
    root_path: pathlib.Path,
    config_path: pathlib.Path | None = None,
) -> list[str]:
    msg = create_host_agents_message_class()()
    try:
        text_format.Parse(content, msg)
    except text_format.ParseError as e:
        return [f"Protobuf syntax error in agent_configs.textproto: {e}"]
    if not msg.configs:
        return ["No agent configs found in agent_configs.textproto."]

    errors: list[str] = []
    seen_ids: set[str] = set()
    for config in msg.configs:
        if config.id:
            if config.id in seen_ids:
                errors.append(f"Duplicate agent id found: '{config.id}'")
            seen_ids.add(config.id)
        errors.extend(
            validate_agent_config(config, root_path, config_path=config_path)
        )
    return errors


def _infer_repo_root(config_file: pathlib.Path) -> pathlib.Path:
    return next(
        (p for p in config_file.resolve().parents if (p / ".git").exists()),
        config_file.resolve().parent.parent,
    )


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", help="Agent config files.")
    parser.add_argument("--root", default=None, help="Repository root path.")
    args = parser.parse_args(list(argv[1:]))
    explicit_root = pathlib.Path(args.root).resolve() if args.root else None
    has_errors = False

    for file_arg in args.files:
        config_file = pathlib.Path(file_arg).resolve()
        try:
            content = config_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"[FAIL] {file_arg}: {e}", file=sys.stderr)
            has_errors = True
            continue
        root_path = explicit_root or _infer_repo_root(config_file)
        if errors := validate_agent_configs(
            content, root_path, config_path=config_file
        ):
            has_errors = True
            print(f"Validation errors in {file_arg}:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
        else:
            print(f"[PASS] {file_arg}")
    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
