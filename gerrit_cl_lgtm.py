#!/usr/bin/env vpython3
# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import urllib.parse
from gerrit_util import SetReview


def main():
    # Test utility.
    parser = argparse.ArgumentParser(description="Lookz good to me!", )
    parser.add_argument('cl', metavar='CL', type=int, help='CL number')
    parser.add_argument('score',
                        metavar='SCORE',
                        type=str,
                        help='Code Review value (e.g, -2, -1, 0, +1, +2)')
    args = parser.parse_args()

    result = SetReview("chromium-review.googlesource.com",
                       args.cl,
                       labels={'Code-Review': args.score})
    print(result)


if __name__ == "__main__":
    main()
