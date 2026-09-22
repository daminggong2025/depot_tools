#!/usr/bin/env vpython3
# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Validates CRACS agent_configs.textproto / agent_configs.txtpb files."""

import argparse
import os
import re
import sys
import yaml

from google.protobuf import descriptor_pb2
from google.protobuf import message_factory
from google.protobuf import text_format

_TYPE_STR = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
_TYPE_MSG = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
_TYPE_BOOL = descriptor_pb2.FieldDescriptorProto.TYPE_BOOL

_SCHEMA = {
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


def _create_host_agents_message_class():
    """Creates a dynamic HostAgents protobuf message class."""
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


def _parse_frontmatter(content):
    """Parses YAML frontmatter from markdown content. Returns (dict, error)."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, "Missing starting YAML frontmatter delimiter '---'."
    end = next(
        (i for i in range(1, len(lines)) if lines[i].strip() == "---"), None
    )
    if end is None:
        return None, "Missing closing YAML frontmatter delimiter '---'."
    yaml_str = "\n".join(lines[1:end]).strip()
    if not yaml_str:
        return None, "YAML frontmatter is empty."
    try:
        data = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        return None, f"invalid YAML: {e}"
    if not isinstance(data, dict):
        return None, "frontmatter not a dict."
    return data, None


def _resolve_skill_path(skill_ref, root_path, config_path=None):
    """Resolves a skill reference to a SKILL.md file path."""
    clean_ref = skill_ref.strip("/")
    root_str = os.fspath(root_path)
    bases = [root_str]
    if config_path is not None:
        bases.append(
            os.path.join(os.path.dirname(os.fspath(config_path)), "skills")
        )
    bases.extend(
        os.path.join(root_str, d, "skills")
        for d in ("agents", ".agents", "depot_tools_agents")
    )
    candidates = [
        os.path.join(b, clean_ref)
        if os.path.basename(clean_ref).upper() == "SKILL.MD"
        else os.path.join(b, clean_ref, "SKILL.md")
        for b in bases
    ]
    return next((c for c in candidates if os.path.isfile(c)), candidates[0])


def _validate_skill_reference(
    agent_id, expected_desc, skill_ref, root_path, config_path=None
):
    """Validates a single skill reference and its SKILL.md frontmatter."""
    skill_path = _resolve_skill_path(skill_ref, root_path, config_path)
    if not os.path.isfile(skill_path):
        return [f"Agent '{agent_id}': skill file does not exist: '{skill_ref}'"]
    try:
        with open(skill_path, "r", encoding="utf-8") as f:
            frontmatter, err = _parse_frontmatter(f.read())
    except (OSError, UnicodeDecodeError) as e:
        return [f"Agent '{agent_id}': could not read skill '{skill_ref}': {e}"]
    if err is not None:
        return [f"Agent '{agent_id}': skill '{skill_ref}': {err}"]

    errors = []
    fm_id = str(frontmatter.get("id") or frontmatter.get("name", "")).strip()
    if agent_id != fm_id and not (fm_id and agent_id.endswith(f"-{fm_id}")):
        errors.append(
            f"Agent id '{agent_id}' does not match frontmatter id/name "
            f"'{fm_id}' in '{skill_ref}'."
        )
    fm_desc = str(frontmatter.get("description", "")).strip()
    if (
        not expected_desc.strip()
        or not fm_desc
        or ("id" in frontmatter and fm_desc != expected_desc.strip())
    ):
        errors.append(
            f"Agent '{agent_id}': description in agent_configs.textproto does "
            f"not match description in '{skill_ref}'."
        )
    return errors


def _validate_path_regexes(agent_id, filters, filter_kind):
    """Validates that all path_regex entries compile as regular expressions."""
    errors = []
    for filter_msg in filters:
        for regex in filter_msg.path_regex:
            try:
                re.compile(regex)
            except re.error as e:
                errors.append(
                    f"Agent '{agent_id}': invalid {filter_kind} path_regex "
                    f"'{regex}': {e}"
                )
    return errors


def _validate_agent_config(config, root_path, config_path=None):
    """Validates a single AgentConfig message."""
    agent_id = config.id
    if not agent_id:
        return ["AgentConfig is missing required 'id' field."]
    errors = []
    if not config.skills:
        errors.append(f"Agent '{agent_id}': missing 'skills' field.")
    else:
        for skill_ref in config.skills:
            errors.extend(
                _validate_skill_reference(
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
            _validate_path_regexes(agent_id, config.include_filters, "include")
        )
    errors.extend(
        _validate_path_regexes(agent_id, config.exclude_filters, "exclude")
    )
    return errors


def _infer_repo_root(config_file):
    """Infers the repository root directory from a config file path."""
    curr = os.path.dirname(os.path.abspath(config_file))
    while os.path.dirname(curr) != curr:
        if os.path.exists(os.path.join(curr, ".git")):
            return curr
        curr = os.path.dirname(curr)
    return os.path.dirname(os.path.dirname(os.path.abspath(config_file)))


def validate_agent_configs(content, root_path, config_path=None):
    """Validates the textproto content of an agent_configs file."""
    msg = _create_host_agents_message_class()()
    try:
        text_format.Parse(content, msg)
    except text_format.ParseError as e:
        return [f"Protobuf syntax error in agent_configs.textproto: {e}"]
    if not msg.configs:
        return ["No agent configs found in agent_configs.textproto."]

    errors = []
    seen_ids = set()
    for config in msg.configs:
        if config.id:
            if config.id in seen_ids:
                errors.append(f"Duplicate agent id found: '{config.id}'")
            seen_ids.add(config.id)
        errors.extend(
            _validate_agent_config(config, root_path, config_path=config_path)
        )
    return errors


def main(argv):
    """Entrypoint for the agent config validator script."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", help="Agent config files.")
    parser.add_argument("--root", default=None, help="Repository root path.")
    args = parser.parse_args(list(argv[1:]))
    explicit_root = os.path.abspath(args.root) if args.root else None
    has_errors = False

    for file_arg in args.files:
        config_file = os.path.abspath(file_arg)
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError) as e:
            print(f"[FAIL] {file_arg}: {e}", file=sys.stderr)
            has_errors = True
            continue
        root_path = explicit_root or _infer_repo_root(config_file)
        errors = validate_agent_configs(
            content, root_path, config_path=config_file
        )
        if errors:
            has_errors = True
            print(f"Validation errors in {file_arg}:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
        else:
            print(f"[PASS] {file_arg}")
    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
