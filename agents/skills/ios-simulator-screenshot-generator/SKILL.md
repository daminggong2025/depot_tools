---
name: ios-simulator-screenshot-generator
description: Automated tool and workflow for capturing full-screen iOS Simulator screenshots of Chromium features with FRE disabled and custom experiment flags. Saves screenshots matching string ID conventions in ios/chrome/app/strings/<GRD_FOLDER>/<STRING_ID>.png.
---

# iOS Simulator Screenshot Generator

This skill provides an automated workflow and script for launching iOS Simulator, disabling the First Run Experience (FRE), setting experiment feature flags via `NSUserDefaults`, and capturing full-resolution screenshots of active UI components (such as non-modal promo banners) over the main Chrome browser interface.

Screenshots are saved directly in `ios/chrome/app/strings/<GRD_FOLDER>/<STRING_ID>.png` matching Chromium's official translation conventions:
- `ios/chrome/app/strings/ios_strings_grd/<TITLE_STRING_ID>.png`
- `ios/chrome/app/strings/ios_google_chrome_strings_grd/<DESC_STRING_ID>.png`
- `ios/chrome/app/strings/ios_chromium_strings_grd/<DESC_STRING_ID>.png`

## Quick Start

Execute the helper script to auto-detect booted simulator, configure defaults, launch Chromium, and export PNG screenshots:

```bash
python3 agents/skills/ios-simulator-screenshot-generator/scripts/generate_simulator_screenshots.py
```

### CLI Options

- `--udid <SIM_UDID>`: Specify target simulator UDID (auto-detected if omitted).
- `--bundle-id <BUNDLE_ID>`: Set target app bundle ID (default: `org.chromium.ost.chrome.ios.dev`).
- `--app-path <APP_PATH>`: Set path to built application bundle (default: `out/Debug-iphonesimulator/Chromium.app`).
- `--start-arm <N>` / `--end-arm <M>`: Capture specific range of experiment arms.
- `--strings-dir <PATH>`: Custom path to `ios/chrome/app/strings` directory.

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

4. **Saving Translation Assets**:
   - Saves screenshots under string ID names directly in GRD folders (e.g. `ios/chrome/app/strings/ios_strings_grd/IDS_IOS_DEFAULT_BROWSER_NON_MODAL_OMNIBOX_NAVIGATION_TITLE_EXP1.png`) so they are ready for inspection or `tools/translation/upload_screenshots.py`.
