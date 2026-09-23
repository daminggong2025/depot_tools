#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Validates CRACS agent_configs.textproto / agent_configs.txtpb files."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import functools
import os
import re
import sys
from typing import Any

from google.protobuf import descriptor_pb2
from google.protobuf import message_factory
from google.protobuf import text_format

_TYPE_STR = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
_TYPE_MSG = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
_TYPE_BOOL = descriptor_pb2.FieldDescriptorProto.TYPE_BOOL

_SCHEMA: dict[str, list[tuple[str, int, int, bool, str | None]]] = {
    "Filter": [
        ("project", 1, _TYPE_STR, True, None),
        ("path_regex", 2, _TYPE_STR, True, None),
    ],
    "AgentConfig": [
        ("id", 1, _TYPE_STR, False, None),
        ("display_name", 2, _TYPE_STR, False, None),
        ("description", 3, _TYPE_STR, False, None),
        ("skills", 4, _TYPE_STR, True, None),
        ("include_filters", 5, _TYPE_MSG, True, "Filter"),
        ("exclude_filters", 6, _TYPE_MSG, True, "Filter"),
        ("automatic", 7, _TYPE_BOOL, False, None),
        ("usernames", 8, _TYPE_STR, True, None),
        ("mdb_groups", 9, _TYPE_STR, True, None),
        ("subagents", 10, _TYPE_STR, True, None),
        ("tags", 11, _TYPE_STR, True, None),
    ],
    "HostAgents": [("configs", 1, _TYPE_MSG, True, "AgentConfig")],
}


@functools.lru_cache(maxsize=1)
def _create_host_agents_message_class() -> type[Any]:
    """Creates and caches a dynamic HostAgents protobuf message class."""
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = "agent.proto"
    file_proto.package = (
        "google.corp.android.engprod.codereviewagentconfiguration.v1"
    )
    for msg_name, fields in _SCHEMA.items():
        msg = file_proto.message_type.add(name=msg_name)
        for name, num, ftype, repeated, type_name in fields:
            label = (
                descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
                if repeated
                else descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
            )
            field = msg.field.add(
                name=name, number=num, type=ftype, label=label
            )
            if type_name is not None:
                field.type_name = type_name
    return message_factory.GetMessages([file_proto])[
        f"{file_proto.package}.HostAgents"
    ]


def _resolve_skill_path(
    skill_ref: str,
    root_path: str | os.PathLike[str],
    config_path: str | os.PathLike[str] | None = None,
) -> str | None:
    """Resolves a skill reference to a SKILL.md file path within root_path."""
    parts = [p for p in re.split(r"[/\\]+", skill_ref.strip(r"\/")) if p]
    if not parts or ".." in parts:
        return None
    root_str = os.path.abspath(os.fspath(root_path))
    bases = [root_str]
    if config_path is not None:
        bases.append(
            os.path.join(
                os.path.dirname(os.path.abspath(os.fspath(config_path))),
                "skills",
            )
        )
    for subdir in ("agents", ".agents", "depot_tools_agents"):
        bases.append(os.path.join(root_str, subdir, "skills"))

    is_md = parts[-1].upper() == "SKILL.MD"
    for base in bases:
        candidate = (
            os.path.join(base, *parts)
            if is_md
            else os.path.join(base, *parts, "SKILL.md")
        )
        if os.path.isfile(candidate):
            return candidate
    return None


def _validate_skill_reference(
    agent_id: str,
    skill_ref: str,
    root_path: str | os.PathLike[str],
    config_path: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Validates that a skill reference resolves to an existing SKILL.md file."""
    if not skill_ref.strip():
        return [f"Agent '{agent_id}': empty skill reference."]
    if _resolve_skill_path(skill_ref, root_path, config_path) is None:
        return [f"Agent '{agent_id}': skill file does not exist: '{skill_ref}'"]
    return []


def _validate_filters(
    agent_id: str, filters: Sequence[Any], filter_kind: str
) -> list[str]:
    """Validates Filter messages and their path_regex patterns."""
    errors: list[str] = []
    for filter_msg in filters:
        if not filter_msg.project and not filter_msg.path_regex:
            errors.append(
                f"Agent '{agent_id}': empty {filter_kind}_filters entry "
                f"(must specify 'project' or 'path_regex')."
            )
        for proj in filter_msg.project:
            if not proj.strip():
                errors.append(
                    f"Agent '{agent_id}': empty project in "
                    f"{filter_kind}_filters."
                )
        for regex in filter_msg.path_regex:
            if not regex.strip():
                errors.append(
                    f"Agent '{agent_id}': empty {filter_kind} path_regex."
                )
                continue
            try:
                re.compile(regex)
            except re.error as e:
                errors.append(
                    f"Agent '{agent_id}': invalid {filter_kind} path_regex "
                    f"'{regex}': {e}"
                )
    return errors


def _validate_agent_config(
    config: Any,
    root_path: str | os.PathLike[str],
    config_path: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Validates a single AgentConfig message."""
    agent_id = config.id.strip()
    if not agent_id:
        return ["AgentConfig is missing required 'id' field."]
    errors: list[str] = []
    if not config.skills:
        errors.append(f"Agent '{agent_id}': missing 'skills' field.")
    else:
        for skill_ref in config.skills:
            errors.extend(
                _validate_skill_reference(
                    agent_id,
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
            _validate_filters(agent_id, config.include_filters, "include")
        )
    errors.extend(
        _validate_filters(agent_id, config.exclude_filters, "exclude")
    )
    return errors


def _infer_repo_root(config_file: str) -> str:
    """Infers the repository root directory from a config file path."""
    curr = os.path.dirname(os.path.abspath(config_file))
    while os.path.dirname(curr) != curr:
        if os.path.exists(os.path.join(curr, ".git")):
            return curr
        curr = os.path.dirname(curr)
    return os.path.dirname(os.path.dirname(os.path.abspath(config_file)))


def validate_agent_configs(
    content: str,
    root_path: str | os.PathLike[str],
    config_path: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Validates the textproto content of an agent_configs file."""
    msg = _create_host_agents_message_class()()
    try:
        text_format.Parse(content, msg)
    except text_format.ParseError as e:
        return [f"Protobuf syntax error in agent_configs.textproto: {e}"]
    if not msg.configs:
        return ["No agent configs found in agent_configs.textproto."]

    errors: list[str] = []
    seen_ids: set[str] = set()
    for config in msg.configs:
        agent_id = config.id.strip()
        if agent_id:
            if agent_id in seen_ids:
                errors.append(f"Duplicate agent id found: '{agent_id}'")
            seen_ids.add(agent_id)
        errors.extend(
            _validate_agent_config(config, root_path, config_path=config_path)
        )
    return errors


def _validate_config_file(
    file_arg: str, explicit_root: str | None = None
) -> bool:
    """Validates a single agent_configs file and prints its status."""
    config_file = os.path.abspath(file_arg)
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"[FAIL] {file_arg}: {e}", file=sys.stderr)
        return False

    root_path = explicit_root or _infer_repo_root(config_file)
    errors = validate_agent_configs(content, root_path, config_path=config_file)
    if errors:
        print(f"Validation errors in {file_arg}:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return False

    print(f"[PASS] {file_arg}")
    return True


def main(argv: Sequence[str]) -> int:
    """Entrypoint for the agent config validator script."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", help="Agent config files.")
    parser.add_argument("--root", default=None, help="Repository root path.")
    args = parser.parse_args(argv[1:])
    explicit_root = os.path.abspath(args.root) if args.root else None
    has_errors = False
    for file_arg in args.files:
        if not _validate_config_file(file_arg, explicit_root=explicit_root):
            has_errors = True
    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
