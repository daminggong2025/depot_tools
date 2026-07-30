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
- `--clean`: Uninstall existing binary to purge stale preference state (prompts for confirmation if interactive).
- `--yes`: Skip confirmation prompt when `--clean` is specified.
- `--skip-install`: Skip reinstalling app binary to preserve existing simulator state (e.g., signed-in user or manual setup).
- `--enable-features <FEATURES>`: Pass `--enable-features=<FEATURES>` directly to Chromium on launch.
- `--launch-args <ARGS...>`: Pass arbitrary command-line arguments to Chromium on launch.
- `--delay <SECONDS>`: Seconds to wait after app launch before taking screenshot (default: `5.0`).
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
   - Write custom keys directly to `NSUserDefaults` or pass `--enable-features` on launch:
     ```bash
     # NSUserDefaults flag
     xcrun simctl spawn <DEVICE_UDID> defaults write <BUNDLE_ID> MyExperimentFlag -bool true

     # Command line feature flag on launch
     xcrun simctl launch <DEVICE_UDID> <BUNDLE_ID> --enable-features=MyFeature
     ```

3. **App Launch & Screenshot Capture**:
   - **State Preservation & User Prompt**: Uninstalling the binary purges stale preference state, but wipes existing simulator state and preferences. **The agent MUST prompt the user before uninstalling.**
   - Option A (Default - Preserves State): Re-install bundle directly without uninstalling:
     ```bash
     xcrun simctl install <DEVICE_UDID> out/Debug-iphonesimulator/Chromium.app
     ```
   - Option B (Clean Re-install - Purges State with Confirmation): Prompt user before uninstalling:
     ```bash
     xcrun simctl uninstall <DEVICE_UDID> <BUNDLE_ID>
     xcrun simctl install <DEVICE_UDID> out/Debug-iphonesimulator/Chromium.app
     ```
   - Option C (Skip Install - Reuses Existing App & State): Launch existing installed app without reinstalling:
     ```bash
     xcrun simctl launch <DEVICE_UDID> <BUNDLE_ID> --enable-features=MyFeature
     ```
   - **Manual Navigation Guidance**: If the target UI feature cannot be launched directly via feature flags or URL schemes and requires manual navigation (e.g., opening a deeply nested settings page or dialog), the agent SHOULD inform the user before running the capture command:
     > *"Launching Chromium on the simulator with feature flag enabled. If manual navigation is needed to reach string `<STRING_ID>`, please navigate to that screen on the simulator before the delay expires (or specify `--delay` for extra time)."*
   - Launch application and capture display output:
     ```bash
     xcrun simctl launch <DEVICE_UDID> <BUNDLE_ID>
     sleep 5
     xcrun simctl io <DEVICE_UDID> screenshot /tmp/screenshots/screenshot.png
     ```

4. **Output Verification**:
   - The script outputs a summary table detailing exact local file paths so developers and AI agents can visually inspect generated PNG images before translation upload.
