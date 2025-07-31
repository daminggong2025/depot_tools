#!/usr/bin/env vpython3
# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# [VPYTHON:BEGIN]
# python_version: "3.11"

# wheel: <
#   name: "infra/python/wheels/cffi/${vpython_platform}"
#   version: "version:1.15.1.chromium.2"
# >

# wheel: <
#   name: "infra/python/wheels/cryptography/${vpython_platform}"
#   version: "version:43.0.0"
# >

# wheel: <
#   name: "infra/python/wheels/pycparser-py2_py3"
#   version: "version:2.21"
# >

# # TODO: Replace this with production package.
# wheel: <
#   name: "experimental/qjw_at_chromium.org/infra/python/wheels/fido2-py2_py3"
#   version: "version:2.0.0"
# >
# [VPYTHON:END]

import io
import sys
import json
import dataclasses
import logging

from fido2.client import WebAuthnClient, DefaultClientDataCollector, UserInteraction, AssertionSelection
from fido2.webauthn import PublicKeyCredentialRequestOptions, UserVerificationRequirement
from fido2.hid import CtapHidDevice

# Use Windows WebAuthn API if available, and if we aren't running as admin.
try:
    from fido2.client.windows import WindowsClient
    use_winclient = (WindowsClient.is_available()
                     and not ctypes.windll.shell32.IsUserAnAdmin())
except ImportError:
    use_winclient = False

_PLUGIN_ENDIANNESS = 'little'
_PLUGIN_HEADER_SIZE = 4

# Exit codes.
_EXIT_NO_FIDO2_DEVICES = 11


# Reads exactly `size` amount of data from b.
# Raises exception on error or EOF.
def read_full(r: io.BufferedReader, size: int) -> bytes:
    b = r.read(size)
    if len(b) != size:
        raise EOFError(f"premature EOF when reading {size} bytes from {r}.")
    return b


# Writes `b` fully to `w`.
# Raises IOError if the write isn't complete.
def write_full(w: io.BufferedWriter, b: bytes):
    written = w.write(b)
    if written != len(b):
        raise IOError(
            f"failed to write plugin frame header, wrote {written} bytes out of {_PLUGIN_HEADER_SIZE} bytes."
        )


# Read a framed Google WebAuthn plugin message.
#
# A frame consists of: 4 bytes of little endian uint32 length, plus this amount
# bytes of binary data.
def plugin_read(r: io.BufferedReader) -> bytes:
    header = read_full(r, _PLUGIN_HEADER_SIZE)
    length = int.from_bytes(header, _PLUGIN_ENDIANNESS, signed=False)
    return read_full(r, length)


# Write a framed Google Webauthn plugin message.
#
# A frame consists of: 4 bytes of little endian uint32 length, plus this amount
# bytes of binary data.
def plugin_write(w: io.BufferedWriter, b: bytes) -> bytes:
    length = len(b)
    header = length.to_bytes(_PLUGIN_HEADER_SIZE,
                             _PLUGIN_ENDIANNESS,
                             signed=False)
    write_full(header)
    write_full(b)


@dataclasses.dataclass
class PluginRequest:
    origin: str
    public_key_credential_request: PublicKeyCredentialRequestOptions


# Parse a plugin request JSON string into
def parse_plugin_request(b: bytes) -> PluginRequest:
    j = json.loads(b)

    req = PublicKeyCredentialRequestOptions.from_dict(j["requestData"])

    # Apply overrides to certain fields.
    req = PublicKeyCredentialRequestOptions(
        challenge=req.challenge,
        timeout=req.timeout,
        rp_id=req.rp_id,
        allow_credentials=req.allow_credentials,
        hints=req.hints,

        # Discourage UV.
        #
        # ReAuth flow is triggered for user who's already logged in, so there's no
        # need to ask for PIN/password authentication factor.
        #
        # Here we only want to test for user presence and ownership of the private
        # key.
        user_verification=UserVerificationRequirement.DISCOURAGED,

        # Don't support extensions for now.
        extensions=None,
    )

    return PluginRequest(
        origin=j["origin"],
        public_key_credential_request=req,
    )


# Encode a plugin response from an obtained assertion.
def to_plugin_response(a: AssertionSelection) -> bytes:
    return json.dumps({
        "type": "getResponse",
        "responseData": dict(a.get_response()),
        "error": None,
    })


# Handler when user interaction is required.
#
# This plugin's stdin/stdout talks with git-credential-luci, so we fail
# actions that require user input (this plugin shouldn't set any flag
# that require user interaction).
class DiscardInteraction(UserInteraction):

    def prompt_up(self):
        logging.info("Touch your blinking security key to continue.")

    def request_pin(self, permissions, rd_id):
        # This plugin shouldn't set assertion flags that will require
        # PIN entry.
        return None

    def request_uv(self, permissions, rd_id):
        # Don't allow user verification (UV), because we don't allow PIN
        # entry, UV will fail.
        return False


# Returns a list of WebAuthnClients where we can send assertion requests to.
#
# On windows, this method returns a client that talks with Win32 API if
# available.
def get_clients(origin: str) -> list[WebAuthnClient]:
    client_data_collector = DefaultClientDataCollector(origin)
    user_interaction = DiscardInteraction()

    if use_winclient:
        return WindowsClient(origin)

    clients = []
    for dev in CtapHidDevice.list_devices():
        desc = dev.descriptor
        logging.info(
            "Found CtapHidDevice %s (serial: %s, vid/pid: %04x %04x) at %s",
            desc.product_name,
            desc.serial_number,
            desc.vid,
            desc.pid,
            desc.path,
        )
        clients.append(
            Fido2Client(
                dev,
                client_data_collector=client_data_collector,
                user_interaction=user_interaction,
            ))

    return clients


def main():
    logging.basicConfig(level=logging.DEBUG)
    plugin_req = parse_plugin_request(plugin_read(sys.stdin.buffer))

    clients = get_clients(plugin_req.origin)
    if not clients:
        logging.error("No available FIDO devices.")
        sys.exit(_EXIT_NO_FIDO2_DEVICES)

    # Race an assertion across all clients.
    assertion = clients[0].get_assertion(
        plugin_req.public_key_credential_request)

    # Write the first completed assertion.
    plugin_write(to_plugin_response(assertion))


if __name__ == "__main__":
    main()
