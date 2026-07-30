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
DEFAULT_OUTPUT_DIR = "/tmp/screenshots"


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


def main():
    parser = argparse.ArgumentParser(
        description="General Automated iOS Simulator Screenshot Generator"
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
        "--output-dir",
        type=str,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to save PNG screenshots",
    )
    parser.add_argument(
        "--string-id",
        type=str,
        help="Optional string ID name for naming screenshot asset",
    )
    parser.add_argument(
        "--strings-dir",
        type=str,
        help="Path to ios/chrome/app/strings directory",
    )
    parser.add_argument(
        "--flag",
        nargs=3,
        action="append",
        metavar=("KEY", "TYPE", "VALUE"),
        help="Custom NSUserDefaults flag to write (e.g. --flag MyFlag -bool true)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Uninstall existing binary to purge stale state (wipes app data)",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt when --clean is specified",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Skip reinstalling app binary; reuse existing installed app & state on simulator",
    )
    parser.add_argument(
        "--enable-features",
        type=str,
        help="Comma-separated feature flags to enable on launch (e.g. --enable-features MyFeature)",
    )
    parser.add_argument(
        "--launch-arg",
        action="append",
        dest="launch_args",
        help="Additional command line argument to pass to Chromium on launch (e.g. --launch-arg --foo)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=5.0,
        help="Delay in seconds to wait after launching app before capturing screenshot (default: 5.0)",
    )
    args = parser.parse_args()

    if args.clean and args.skip_install:
        print("Error: Cannot specify both --clean and --skip-install.")
        sys.exit(1)

    udid = args.udid or get_booted_simulator_udid()
    if not udid:
        print("Error: No booted simulator found and --udid was not specified.")
        sys.exit(1)

    print(f"Target Simulator UDID: {udid}")
    os.makedirs(args.output_dir, exist_ok=True)

    # Ensure simulator is booted
    run_cmd(["xcrun", "simctl", "boot", udid])
    time.sleep(2)

    # Terminate running app
    run_cmd(["xcrun", "simctl", "terminate", udid, args.bundle_id])

    if args.clean:
        if not args.yes and sys.stdin.isatty():
            confirm = input(
                f"Uninstalling {args.bundle_id} will wipe all simulator state. Continue? [y/N]: "
            )
            if confirm.lower() not in ("y", "yes"):
                print("Aborting clean uninstall.")
                sys.exit(0)
        run_cmd(["xcrun", "simctl", "uninstall", udid, args.bundle_id])
        time.sleep(1)

    if not args.skip_install:
        run_cmd(["xcrun", "simctl", "install", udid, args.app_path])

    # Always disable FRE by default
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

    # Apply any custom NSUserDefaults flags requested
    if args.flag:
        for key, ftype, val in args.flag:
            run_cmd(
                [
                    "xcrun",
                    "simctl",
                    "spawn",
                    udid,
                    "defaults",
                    "write",
                    args.bundle_id,
                    key,
                    ftype,
                    val,
                ]
            )

    # Launch Chromium with optional feature flags / launch arguments
    launch_cmd = ["xcrun", "simctl", "launch", udid, args.bundle_id]
    if args.enable_features:
        launch_cmd.append(f"--enable-features={args.enable_features}")
    if args.launch_args:
        launch_cmd.extend(args.launch_args)
    run_cmd(launch_cmd)
    time.sleep(args.delay)

    filename = f"{args.string_id}.png" if args.string_id else "screenshot.png"
    png_path = os.path.join(args.output_dir, filename)
    run_cmd(["xcrun", "simctl", "io", udid, "screenshot", png_path])

    saved_paths = []
    if os.path.exists(png_path):
        file_size = os.path.getsize(png_path)
        saved_paths.append(png_path)

        # If --string-id and --strings-dir are specified, copy to matching GRD directories
        if (
            args.string_id
            and args.strings_dir
            and os.path.exists(args.strings_dir)
        ):
            for sub in [
                "ios_strings_grd",
                "ios_google_chrome_strings_grd",
                "ios_chromium_strings_grd",
            ]:
                sub_path = os.path.join(args.strings_dir, sub)
                if os.path.exists(sub_path):
                    target_file = os.path.join(
                        sub_path, f"{args.string_id}.png"
                    )
                    shutil.copyfile(png_path, target_file)
                    saved_paths.append(target_file)

    print("\n" + "=" * 70)
    print("SCREENSHOT GENERATION COMPLETE")
    print("Saved screenshot assets:")
    print("=" * 70)
    for path in saved_paths:
        print(f"  - {path}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
