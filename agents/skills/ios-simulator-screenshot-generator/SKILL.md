---
name: ios-simulator-screenshot-generator
description: Automated tool and workflow for capturing full-screen iOS Simulator screenshots of Chromium features with FRE disabled and custom experiment flags. Automatically detects booted simulator, saves local PNG files, and reports output paths for inspection.
---

# iOS Simulator Screenshot Generator

This skill provides an automated workflow and script for launching iOS Simulator, disabling the First Run Experience (FRE), setting experiment feature flags via `NSUserDefaults`, and capturing full-resolution screenshots of active UI components (such as non-modal promo banners) over the main Chrome browser interface.

Screenshots are saved locally in the designated output directory (default: `/tmp/screenshots`), and output file paths are clearly reported for visual verification before any upload or translation process.

## Quick Start

Execute the helper script to auto-detect booted simulator, configure defaults, launch Chromium, and export local screenshots:

```bash
python3 agents/skills/ios-simulator-screenshot-generator/scripts/generate_simulator_screenshots.py
```

### CLI Options

- `--udid <SIM_UDID>`: Specify target simulator UDID (auto-detected if omitted).
- `--bundle-id <BUNDLE_ID>`: Set target app bundle ID (default: `org.chromium.ost.chrome.ios.dev`).
- `--app-path <APP_PATH>`: Set path to built application bundle (default: `out/Debug-iphonesimulator/Chromium.app`).
- `--start-arm <N>` / `--end-arm <M>`: Capture specific range of experiment arms.
- `--output-dir <DIR_PATH>`: Custom directory to save local PNG screenshots (default: `/tmp/screenshots`).

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
     xcrun simctl io <DEVICE_UDID> screenshot /tmp/screenshots/promo_arm_<ARM_NUM>.png
     ```

4. **Output Verification**:
   - The script outputs a summary table detailing exact local file paths (e.g. `/tmp/screenshots/promo_arm_1.png`) so the developer or agent can inspect the generated PNG images.
