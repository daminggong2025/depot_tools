#!/usr/bin/env python3
import argparse
import hashlib
import os
import re
import subprocess
import sys
import time

DEFAULT_BUNDLE_ID = "org.chromium.ost.chrome.ios.dev"
DEFAULT_APP_PATH = "out/Debug-iphonesimulator/Chromium.app"


def run_cmd(cmd):
    print(f"Running: {' '.join(cmd)}")
    res = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    return res.stdout.strip()


def get_booted_simulator_udid(simctl_output=None):
    if simctl_output is None:
        simctl_output = run_cmd(["xcrun", "simctl", "list", "devices"])
    for line in simctl_output.splitlines():
        if "(Booted)" in line:
            match = re.search(r"\(([-A-F0-9]{36})\)", line, re.IGNORECASE)
            if match:
                return match.group(1)
    return None


def compute_sha1(content):
    return hashlib.sha1(content).hexdigest()


def main():
    parser = argparse.ArgumentParser(
        description="Automated iOS Simulator Screenshot Generator"
    )
    parser.add_argument(
        "--udid",
        type=str,
        help="Target simulator UDID (auto-detected if omitted)",
    )
    parser.add_argument(
        "--bundle-id",
        type=str,
        default=DEFAULT_BUNDLE_ID,
        help="App bundle identifier",
    )
    parser.add_argument(
        "--app-path",
        type=str,
        default=DEFAULT_APP_PATH,
        help="Path to built Chromium .app bundle",
    )
    parser.add_argument(
        "--start-arm",
        type=int,
        default=1,
        help="Starting experiment arm (default: 1)",
    )
    parser.add_argument(
        "--end-arm",
        type=int,
        default=10,
        help="Ending experiment arm (default: 10)",
    )
    parser.add_argument(
        "--strings-dir",
        type=str,
        help="Path to ios/chrome/app/strings directory",
    )
    args = parser.parse_args()

    udid = args.udid or get_booted_simulator_udid()
    if not udid:
        print("Error: No booted simulator found and --udid was not specified.")
        sys.exit(1)

    print(f"Target Simulator UDID: {udid}")

    # Ensure simulator is booted
    run_cmd(["xcrun", "simctl", "boot", udid])
    time.sleep(2)

    for arm in range(args.start_arm, args.end_arm + 1):
        print(
            f"=== Capturing Arm {arm} (FRE Disabled, Non-Modal Promo Displayed) ==="
        )
        run_cmd(["xcrun", "simctl", "terminate", udid, args.bundle_id])
        run_cmd(["xcrun", "simctl", "uninstall", udid, args.bundle_id])
        time.sleep(1)

        # Re-install fresh app binary
        run_cmd(["xcrun", "simctl", "install", udid, args.app_path])

        # Write NSUserDefaults directly into simulator environment
        run_cmd(
            [
                "xcrun",
                "simctl",
                "spawn",
                udid,
                "defaults",
                "write",
                args.bundle_id,
                "FirstRunForceDisabled",
                "-bool",
                "true",
            ]
        )
        run_cmd(
            [
                "xcrun",
                "simctl",
                "spawn",
                udid,
                "defaults",
                "write",
                args.bundle_id,
                "ForceShowDefaultBrowserNonModalPromo",
                "-bool",
                "true",
            ]
        )
        run_cmd(
            [
                "xcrun",
                "simctl",
                "spawn",
                udid,
                "defaults",
                "write",
                args.bundle_id,
                "OmniboxPastePromoExperimentArm",
                "-int",
                str(arm),
            ]
        )

        # Launch Chromium directly
        run_cmd(["xcrun", "simctl", "launch", udid, args.bundle_id])
        time.sleep(5)

        tmp_png = f"/tmp/promo_arm_{arm}.png"
        run_cmd(["xcrun", "simctl", "io", udid, "screenshot", tmp_png])

        if not os.path.exists(tmp_png):
            print(f"Failed to generate {tmp_png}")
            continue

        with open(tmp_png, "rb") as src:
            content = src.read()

        sha1_hash = compute_sha1(content)

        if args.strings_dir and os.path.exists(args.strings_dir):
            title_id = f"IDS_IOS_DEFAULT_BROWSER_NON_MODAL_OMNIBOX_NAVIGATION_TITLE_EXP{arm}"
            desc_id = f"IDS_IOS_DEFAULT_BROWSER_NON_MODAL_OMNIBOX_NAVIGATION_DESCRIPTION_EXP{arm}"

            title_sha1 = os.path.join(
                args.strings_dir, "ios_strings_grd", f"{title_id}.png.sha1"
            )
            desc_google_sha1 = os.path.join(
                args.strings_dir,
                "ios_google_chrome_strings_grd",
                f"{desc_id}.png.sha1",
            )
            desc_chromium_sha1 = os.path.join(
                args.strings_dir,
                "ios_chromium_strings_grd",
                f"{desc_id}.png.sha1",
            )

            for fpath in [title_sha1, desc_google_sha1, desc_chromium_sha1]:
                if os.path.exists(os.path.dirname(fpath)):
                    with open(fpath, "w") as f:
                        f.write(sha1_hash + "\n")

        print(
            f"Arm {arm} captured! Size: {len(content)} bytes, SHA-1: {sha1_hash}"
        )


if __name__ == "__main__":
    main()
