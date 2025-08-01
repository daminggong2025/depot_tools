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

# wheel: <
#   name: "infra/python/wheels/fido2-py3"
#   version: "version:2.0.0"
# >
# [VPYTHON:END]

import io
import ctypes
import sys
import json
import dataclasses
import logging
from threading import Event
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import signal

from fido2.client import WebAuthnClient, DefaultClientDataCollector, UserInteraction, AssertionResponse, Fido2Client
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
_EXIT_ALL_ASSERTIONS_FAILED = 12
_EXIT_NO_MATCHING_CRED = 13


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
    write_full(w, header)
    write_full(w, b)


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
        rp_id=req.rp_id,
        allow_credentials=req.allow_credentials,
        hints=req.hints,

        # Default to 30s timeout.
        timeout=req.timeout if req.timeout else 30 * 1000,

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
def to_plugin_response(a: AssertionResponse) -> bytes:
    return json.dumps({
        "type": "getResponse",
        "responseData": dict(a),
        "error": None,
    }).encode('utf-8')


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


# Returns a list of (WebAuthnClient, client description) where we can send
# assertion requests to.
#
# On windows, this method returns a client that talks with Win32 API if
# available.
def get_clients(origin: str) -> list[tuple[WebAuthnClient, str]]:
    client_data_collector = DefaultClientDataCollector(origin)
    user_interaction = DiscardInteraction()

    if use_winclient:
        return [(WindowsClient(client_data_collector), "WindowsWebAuthn")]

    clients = []
    for dev in CtapHidDevice.list_devices():
        desc = dev.descriptor
        descStr = f"CtapHidDevice {desc.product_name} (VID 0x{desc.vid:04x}, PID 0x{desc.pid:04x}) at {desc.path}"
        logging.info("Found %s", descStr)
        clients.append((
            Fido2Client(
                dev,
                client_data_collector=client_data_collector,
                user_interaction=user_interaction,
            ),
            descStr,
        ))

    return clients


def assert_on_client(client: WebAuthnClient, clientDesc: str,
                     req: PublicKeyCredentialRequestOptions, cancel: Event):
    try:
        return client.get_assertion(req, cancel)
    except Exception as e:
        if not cancel.is_set():
            logging.error("Assertion failed on %s: %s", clientDesc, e)
        return None


# Returns a context manager that sets `event` when `signum` is signaled.
@contextmanager
def set_event_on_signal(signum: int, event: Event):
    original_handler = signal.getsignal(signum)

    def handler(signum, _):
        logging.info("Signal %s received.", signal.strsignal(signum))
        event.set()

    signal.signal(signum, handler)
    try:
        yield
    # pylint: disable=try-except-raise
    except:
        # Re-raise exception.
        raise
    finally:
        # Restore to the original handler.
        signal.signal(signum, original_handler)


def main():
    logging.basicConfig(level=logging.INFO)
    plugin_req = parse_plugin_request(plugin_read(sys.stdin.buffer))

    clients = get_clients(plugin_req.origin)
    if not clients:
        logging.error("No available FIDO devices.")
        sys.exit(_EXIT_NO_FIDO2_DEVICES)

    # Race and retrieve the first successful assertion.
    outcome = None
    cancel = Event()
    with set_event_on_signal(signal.SIGINT, cancel), set_event_on_signal(
            signal.SIGTERM,
            cancel), ThreadPoolExecutor(max_workers=len(clients)) as executor:
        futures = [
            executor.submit(assert_on_client, client, desc,
                            plugin_req.public_key_credential_request, cancel)
            for client, desc in clients
        ]
        for future in as_completed(futures):
            if result := future.result():
                outcome = result
                cancel.set()
                break

    if not outcome:
        logging.error("All assertions failed or timed out.")
        sys.exit(_EXIT_ALL_ASSERTIONS_FAILED)

    assertions = outcome.get_assertions()
    if not assertions:
        logging.error("No matching credential.")
        sys.exit(_EXIT_NO_MATCHING_CRED)
    elif len(assertions) > 1:
        logging.warning(
            "Multiple assertions returned for rp_id %s, selecting the first one.",
            plugin_req.public_key_credential_request.rp_id)

    # Write the first completed assertion.
    plugin_write(sys.stdout.buffer, to_plugin_response(outcome.get_response(0)))


if __name__ == "__main__":
    main()
