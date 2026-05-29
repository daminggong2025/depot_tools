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
    # CIPD ensure is quite slow (hundreds of milliseconds).
    # Thus, we check if the inputs to `cipd ensure` are newer than the stamp file.
    if [ ! -f "$STAMP_FILE" ] || \
       [ "$ENSURE" -nt "$STAMP_FILE" ] || \
       [ "$MYPATH/cipd_manifest.versions" -nt "$STAMP_FILE" ] || \
       [ "$MYPATH/cipd_client_version" -nt "$STAMP_FILE" ]; then
        # CIPD ensure may result in a partial failure where it updates some
        # packages but not others.
        # Thus, we invalidate the cache until we know that it fully succeeded.
        rm -f "$STAMP_FILE"
        mkdir -p "$ROOT"
        (
        source "$MYPATH/cipd" ensure \
            -log-level warning \
            -ensure-file "$ENSURE" \
            -root "$ROOT"
        ) && touch "$STAMP_FILE"
    fi

    echo $ROOT
}
