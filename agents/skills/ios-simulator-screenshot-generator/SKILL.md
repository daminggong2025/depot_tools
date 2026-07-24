---
name: ios-simulator-screenshot-generator
description: Automated tool and workflow for capturing full-screen iOS Simulator screenshots of Chromium features with FRE disabled and custom experiment flags. Automatically detects booted simulator or accepts target UDID.
---

# iOS Simulator Screenshot Generator

This skill provides an automated workflow and script for launching iOS Simulator, disabling the First Run Experience (FRE), setting experiment feature flags via `NSUserDefaults`, and capturing full-resolution screenshots of active UI components (such as non-modal promo banners) over the main Chrome browser interface.

## Quick Start

Execute the helper script to auto-detect booted simulator, configure defaults, launch Chromium, and export screenshots:

```bash
python3 agents/skills/ios-simulator-screenshot-generator/scripts/generate_simulator_screenshots.py
```

### CLI Options

- `--udid <SIM_UDID>`: Specify target simulator UDID (auto-detected if omitted).
- `--bundle-id <BUNDLE_ID>`: Set target app bundle ID (default: `org.chromium.ost.chrome.ios.dev`).
- `--app-path <APP_PATH>`: Set path to built application bundle (default: `out/Debug-iphonesimulator/Chromium.app`).
- `--start-arm <N>` / `--end-arm <M>`: Capture specific range of experiment arms.

## Key Workflow Details

1. **Disabling First Run Experience (FRE)**:
   - Setting `FirstRunForceDisabled = true` in `NSUserDefaults` ensures that modal FRE splash screens do not block the browser interface on launch.
   - Command:
     ```bash
     xcrun simctl spawn <DEVICE_UDID> defaults write <BUNDLE_ID> FirstRunForceDisabled -bool true
     ```

2. **Setting Experiment Arm Flags**:
   - Write custom keys directly to `NSUserDefaults` before launching the application:
     ```bash
     xcrun simctl spawn <DEVICE_UDID> defaults write <BUNDLE_ID> ForceShowDefaultBrowserNonModalPromo -bool true
     xcrun simctl spawn <DEVICE_UDID> defaults write <BUNDLE_ID> OmniboxPastePromoExperimentArm -int <ARM_NUM>
     ```

3. **Clean App Launch & Screenshot Capture**:
   - Uninstall existing binary to purge stale state:
     ```bash
     xcrun simctl uninstall <DEVICE_UDID> <BUNDLE_ID>
     xcrun simctl install <DEVICE_UDID> out/Debug-iphonesimulator/Chromium.app
     ```
   - Launch application and capture display output:
     ```bash
     xcrun simctl launch <DEVICE_UDID> <BUNDLE_ID>
     sleep 5
     xcrun simctl io <DEVICE_UDID> screenshot /tmp/promo_arm_<ARM_NUM>.png
     ```

4. **Updating Translation Checksums (.sha1)**:
   - Calculate SHA-1 hash of the captured PNG file and write it to matching `.png.sha1` files under `ios/chrome/app/strings/`.
