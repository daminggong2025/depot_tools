# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

function cipd_bin_setup {
    local MYPATH="${DEPOT_TOOLS_DIR:-$(dirname "${BASH_SOURCE[0]}")}"
    local ENSURE="$MYPATH/cipd_manifest.txt"
    local ROOT="$MYPATH/.cipd_bin"

    UNAME="${DEPOT_TOOLS_UNAME_S:-$(uname -s | tr '[:upper:]' '[:lower:]')}"
    case $UNAME in
      cygwin*)
        ENSURE="$(cygpath -w $ENSURE)"
        ROOT="$(cygpath -w $ROOT)"
        ;;
    esac

    # value in .cipd_client_root file overrides the default root.
    CIPD_ROOT_OVERRIDE_FILE="${MYPATH}/.cipd_client_root"
    if [ -f "${CIPD_ROOT_OVERRIDE_FILE}" ]; then
        ROOT=$(<"${CIPD_ROOT_OVERRIDE_FILE}")
    fi

    local STAMP_FILE="$ROOT/.manifest_stamp"
    local NEW_STAMP_FILE="$STAMP_FILE.tmp"
    local CURRENT_STATE
    # CIPD ensure is quite slow (hundreds of milliseconds).
    # Thus, we hash the inputs to `cipd ensure` and only run it if the inputs have changed.
    mkdir -p "$ROOT"
    cat "$ENSURE" "$MYPATH/cipd_manifest.versions" "$MYPATH/cipd_client_version" | md5sum > "$NEW_STAMP_FILE"
    if ! diff -q "$STAMP_FILE" "$NEW_STAMP_FILE" &>/dev/null; then
        # CIPD ensure may result in a partial failure where it updates some
        # packages but not others.
        # Thus, we invalidate the cache until we know that it fully succeeded.
        rm -f "$STAMP_FILE"
        (
        source "$MYPATH/cipd" ensure \
            -log-level warning \
            -ensure-file "$ENSURE" \
            -root "$ROOT"
        # Move to atomically create the stamp file.
        ) && mv "$NEW_STAMP_FILE" "$STAMP_FILE"
    fi
    rm -f "$NEW_STAMP_FILE"
    
    echo $ROOT
}
