---
name: ios-simulator-screenshot-generator
description: General automated tool and workflow for capturing full-screen iOS Simulator screenshots of Chromium features with FRE disabled, custom NSUserDefaults, and experiment feature flags.
---

# iOS Simulator Screenshot Generator

This skill provides a general automated workflow and script for capturing full-resolution iOS Simulator screenshots of Chromium UI features under custom configurations (e.g., First Run Experience disabled, experiment flags enabled, or custom `NSUserDefaults` set).

It automates booting the iOS Simulator, deploying fresh builds, configuring simulator preferences, capturing display output, and saving PNG assets to `/tmp/screenshots/screenshot.png` or directly into `ios/chrome/app/strings/<GRD_FOLDER>/<STRING_ID>.png` for Chromium translation pipelines (`tools/translation/upload_screenshots.py`).

## Quick Start

Execute the helper script to auto-detect booted simulator, disable FRE, launch Chromium, and export a clean display screenshot:

```bash
python3 agents/skills/ios-simulator-screenshot-generator/scripts/generate_simulator_screenshots.py
```

### CLI Options

- `--udid <SIM_UDID>`: Specify target simulator UDID (auto-detected if omitted).
- `--bundle-id <BUNDLE_ID>`: Set target app bundle ID (default: `org.chromium.ost.chrome.ios.dev`).
- `--app-path <APP_PATH>`: Set path to built application bundle (default: `out/Debug-iphonesimulator/Chromium.app`).
- `--output-dir <DIR_PATH>`: Directory to save screenshots (default: `/tmp/screenshots`).
- `--flag <KEY> <TYPE> <VALUE>`: Write custom `NSUserDefaults` key before launch (e.g., `--flag MyExperimentFlag -bool true`).
- `--string-id <STRING_ID>`: Specify target string ID for naming screenshot asset.
- `--strings-dir <PATH>`: Path to `ios/chrome/app/strings` directory for automatic GRD asset placement.

## Key Workflow Details

1. **Disabling First Run Experience (FRE)**:
   - Setting `FirstRunForceDisabled = true` in `NSUserDefaults` ensures that modal FRE splash screens do not block the browser interface on launch.
   - Command:
     ```bash
     xcrun simctl spawn <DEVICE_UDID> defaults write <BUNDLE_ID> FirstRunForceDisabled -bool true
     ```

2. **Setting Feature & Experiment Flags**:
   - Write custom keys directly to `NSUserDefaults` before launching the application:
     ```bash
     xcrun simctl spawn <DEVICE_UDID> defaults write <BUNDLE_ID> MyExperimentFlag -bool true
     ```

3. **Clean App Launch & Screenshot Capture**:
   - Uninstall existing binary to purge stale preference state:
     ```bash
     xcrun simctl uninstall <DEVICE_UDID> <BUNDLE_ID>
     xcrun simctl install <DEVICE_UDID> out/Debug-iphonesimulator/Chromium.app
     ```
   - Launch application and capture display output:
     ```bash
     xcrun simctl launch <DEVICE_UDID> <BUNDLE_ID>
     sleep 5
     xcrun simctl io <DEVICE_UDID> screenshot /tmp/screenshots/screenshot.png
     ```

4. **Output Verification**:
   - The script outputs a summary table detailing exact local file paths so developers and AI agents can visually inspect generated PNG images before translation upload.
