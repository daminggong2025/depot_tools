#!/usr/bin/env bash
# Copyright 2009 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

base_dir=$(dirname "$0")

if [[ "#grep#fetch#cleanup#diff#help#getdep#root#setdep#recurse#--help#" != *"#$1#"* ]]; then
  # Shall skip authomatic update?
  if [[ $DEPOT_TOOLS_UPDATE != 0 ]]; then
    "$base_dir"/update_depot_tools "$@"
    case $? in
      123)
        # msys environment was upgraded, need to quit.
        exit 0
        ;;
      0)
        ;;
      *)
        exit $?
    esac
  fi
fi

# Prepend python-bin to PATH so subprocesses (e.g. gclient hooks) use CIPD
# Python, and ensure depot_tools is on PATH for standalone usage.
base_dir_abs=$(CDPATH= cd -- "$base_dir" 2>/dev/null && pwd -P || echo "$base_dir")
PATH="$base_dir_abs/python-bin:$PATH:$base_dir_abs"

PYTHONDONTWRITEBYTECODE=1 exec vpython3 "$base_dir/gclient.py" "$@"
