#!/usr/bin/env vpython3
# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import logging
import urllib.parse
import sys
import json
from gerrit_util import SetReview

_GERRIT_HOSTS = [
    'aomedia',
    'boringssl',
    'chromium',
    'dawn',
    'pdfium',
    'skia',
    'swiftshader',
    'webrtc',
]

_CODE_REVIEW_VOTES = [
    '-2',
    '-1',
    '0',
    '1',
    '+1',
    '2',
    '+2',
]


def format_vote(n: int) -> str:
    if n > 0:
        return f"+{n}"
    return f"{n}"


# TODO(https://crbug.com/456341136): Delete this script when it's no longer needed.
def main():
    parser = argparse.ArgumentParser(
        description="A script to vote 'Code-Review' label on Gerrit CLs", )
    parser.add_argument(
        '--host',
        metavar='host',
        choices=_GERRIT_HOSTS,
        default='chromium',
        help=
        f'The Gerrit host, defaults to "chromium" choose from: {", ".join(_GERRIT_HOSTS)}'
    )
    parser.add_argument('cl', metavar='CL', type=int, help='CL number')
    parser.add_argument(
        'vote',
        metavar='VOTE',
        choices=_CODE_REVIEW_VOTES,
        help=
        f'Code Review vote, plus sign can be omitted, choose from: {", ".join(_CODE_REVIEW_VOTES)}'
    )
    args = parser.parse_args()

    print("""
WARNING: The ability to vote Code-Review via command line is temporary while we
         address issues in Gerrit. This command will be removed soon, please
         don't rely on it.
""".strip(),
          file=sys.stderr)

    response = SetReview(f"{args.host}-review.googlesource.com",
                         args.cl,
                         labels={'Code-Review': args.vote})

    voted = response.get('labels', {}).get('Code-Review', None)
    if voted == int(args.vote):
        print(f"Successfully voted Code-Review {format_vote(voted)}",
              file=sys.stderr)
        print(
            f"View your CL at: https://{args.host}-review.googlesource.com/c/{args.cl}",
            file=sys.stderr)
        sys.exit(0)
    else:
        print("Failed to vote Code-Review.", file=sys.stderr)
        print("Response from Gerrit server:", file=sys.stderr)
        print(json.dumps(response, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
