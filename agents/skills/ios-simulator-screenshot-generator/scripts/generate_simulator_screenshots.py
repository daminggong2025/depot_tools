#!/usr/bin/env python3
import argparse
import os
import re
import shutil
import subprocess
import sys
import time

DEFAULT_BUNDLE_ID = "org.chromium.ost.chrome.ios.dev"
DEFAULT_APP_PATH = "out/Debug-iphonesimulator/Chromium.app"

def run_cmd(cmd):
    print(f"Running: {' '.join(cmd)}")
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res.stdout.strip()

def get_booted_simulator_udid(simctl_output=None):
    if simctl_output is None:
        simctl_output = run_cmd(["xcrun", "simctl", "list", "devices"])
    for line in simctl_output.splitlines():
        if "(Booted)" in line:
            match = re.search(r'\(([-A-F0-9]{36})\)', line, re.IGNORECASE)
            if match:
                return match.group(1)
    return None

def main():
    parser = argparse.ArgumentParser(description="Automated iOS Simulator Screenshot Generator")
    parser.add_argument("--udid", type=str, help="Target simulator UDID (auto-detected if omitted)")
    parser.add_argument("--bundle-id", type=str, default=DEFAULT_BUNDLE_ID, help="App bundle identifier")
    parser.add_argument("--app-path", type=str, default=DEFAULT_APP_PATH, help="Path to built Chromium .app bundle")
    parser.add_argument("--start-arm", type=int, default=1, help="Starting experiment arm (default: 1)")
    parser.add_argument("--end-arm", type=int, default=10, help="Ending experiment arm (default: 10)")
    parser.add_argument("--strings-dir", type=str, help="Path to ios/chrome/app/strings directory")
    args = parser.parse_args()

    udid = args.udid or get_booted_simulator_udid()
    if not udid:
        print("Error: No booted simulator found and --udid was not specified.")
        sys.exit(1)

    print(f"Target Simulator UDID: {udid}")

    # Determine base strings directory if not provided
    strings_dir = args.strings_dir
    if not strings_dir:
        candidate = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..', 'dev', 'bling', 'src', 'ios', 'chrome', 'app', 'strings'))
        if os.path.exists(candidate):
            strings_dir = candidate

    ios_strings_dir = os.path.join(strings_dir, 'ios_strings_grd') if strings_dir else None
    google_dir = os.path.join(strings_dir, 'ios_google_chrome_strings_grd') if strings_dir else None
    chromium_dir = os.path.join(strings_dir, 'ios_chromium_strings_grd') if strings_dir else None

    # Ensure simulator is booted
    run_cmd(["xcrun", "simctl", "boot", udid])
    time.sleep(2)

    generated_files = []

    for arm in range(args.start_arm, args.end_arm + 1):
        print(f"=== Capturing Arm {arm} (FRE Disabled, Non-Modal Promo Displayed) ===")
        run_cmd(["xcrun", "simctl", "terminate", udid, args.bundle_id])
        run_cmd(["xcrun", "simctl", "uninstall", udid, args.bundle_id])
        time.sleep(1)

        # Re-install fresh app binary
        run_cmd(["xcrun", "simctl", "install", udid, args.app_path])

        # Write NSUserDefaults directly into simulator environment
        run_cmd(["xcrun", "simctl", "spawn", udid, "defaults", "write", args.bundle_id, "FirstRunForceDisabled", "-bool", "true"])
        run_cmd(["xcrun", "simctl", "spawn", udid, "defaults", "write", args.bundle_id, "ForceShowDefaultBrowserNonModalPromo", "-bool", "true"])
        run_cmd(["xcrun", "simctl", "spawn", udid, "defaults", "write", args.bundle_id, "OmniboxPastePromoExperimentArm", "-int", str(arm)])

        # Launch Chromium directly
        run_cmd(["xcrun", "simctl", "launch", udid, args.bundle_id])
        time.sleep(5)

        tmp_png = f"/tmp/promo_arm_{arm}.png"
        run_cmd(["xcrun", "simctl", "io", udid, "screenshot", tmp_png])

        if not os.path.exists(tmp_png):
            print(f"Failed to generate {tmp_png}")
            continue

        title_id = f"IDS_IOS_DEFAULT_BROWSER_NON_MODAL_OMNIBOX_NAVIGATION_TITLE_EXP{arm}"
        desc_id = f"IDS_IOS_DEFAULT_BROWSER_NON_MODAL_OMNIBOX_NAVIGATION_DESCRIPTION_EXP{arm}"

        arm_outputs = []
        if ios_strings_dir and os.path.exists(ios_strings_dir):
            title_png = os.path.join(ios_strings_dir, f"{title_id}.png")
            shutil.copyfile(tmp_png, title_png)
            arm_outputs.append(title_png)

        if google_dir and os.path.exists(google_dir):
            google_desc_png = os.path.join(google_dir, f"{desc_id}.png")
            shutil.copyfile(tmp_png, google_desc_png)
            arm_outputs.append(google_desc_png)

        if chromium_dir and os.path.exists(chromium_dir):
            chromium_desc_png = os.path.join(chromium_dir, f"{desc_id}.png")
            shutil.copyfile(tmp_png, chromium_desc_png)
            arm_outputs.append(chromium_desc_png)

        generated_files.extend(arm_outputs)
        print(f"Arm {arm} captured and saved to GRD string directories.")

    print("\n" + "=" * 70)
    print("SCREENSHOT GENERATION COMPLETE")
    print("Saved screenshots to GRD string directories:")
    print("=" * 70)
    for path in generated_files:
        print(f"  - {path}")
    print("=" * 70 + "\n")

if __name__ == '__main__':
    main()
