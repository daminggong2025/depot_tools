#!/usr/bin/env python3
import argparse
import os
import re
import subprocess
import sys
import time

DEFAULT_BUNDLE_ID = "org.chromium.ost.chrome.ios.dev"
DEFAULT_APP_PATH = "out/Debug-iphonesimulator/Chromium.app"
DEFAULT_OUTPUT_DIR = "/tmp/screenshots"

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
    parser.add_argument("--output-dir", type=str, default=DEFAULT_OUTPUT_DIR, help="Directory to save PNG screenshots")
    args = parser.parse_args()

    udid = args.udid or get_booted_simulator_udid()
    if not udid:
        print("Error: No booted simulator found and --udid was not specified.")
        sys.exit(1)

    print(f"Target Simulator UDID: {udid}")
    os.makedirs(args.output_dir, exist_ok=True)

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

        png_path = os.path.join(args.output_dir, f"promo_arm_{arm}.png")
        run_cmd(["xcrun", "simctl", "io", udid, "screenshot", png_path])

        if os.path.exists(png_path):
            file_size = os.path.getsize(png_path)
            generated_files.append((arm, png_path, file_size))
            print(f"Arm {arm} captured: {png_path} ({file_size} bytes)")
        else:
            print(f"Failed to generate {png_path}")

    print("\n" + "=" * 60)
    print("SCREENSHOT GENERATION COMPLETE")
    print(f"All screenshots have been saved to: {args.output_dir}")
    print("=" * 60)
    for arm, path, size in generated_files:
        print(f"  - Arm {arm:2d}: {path} ({size:,} bytes)")
    print("=" * 60 + "\n")

if __name__ == '__main__':
    main()
