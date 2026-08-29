"""Read-only emulator calibration and diagnostics utility."""

from __future__ import annotations

import argparse
from pathlib import Path

from adb_controller import ADBController
from project_paths import DEBUG_DIRECTORY, RUNTIME_DATA_DIR, asset_path
from resource_reader import ResourceReader
from screen_detector import detect_screen


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture and inspect the current CoC emulator screen without input.")
    parser.add_argument("--adb-path")
    parser.add_argument("--device-id")
    args = parser.parse_args()
    adb = ADBController(args.adb_path, args.device_id)
    adb.check_adb_available()
    device = adb.select_device(args.device_id)
    output = RUNTIME_DATA_DIR / "screenshots" / "debug" / "calibration_latest.png"
    adb.capture_screenshot(output)
    from PIL import Image
    with Image.open(output) as image:
        print(f"Screenshot: {output}\nDimensions: {image.width}x{image.height}")
    detection = detect_screen(output, threshold=0.85, debug_directory=DEBUG_DIRECTORY)
    print(f"Screen: {detection.state.value}\nConfidence: {detection.confidence:.2f}\nTemplate: {detection.matched_template_name}")
    resources = ResourceReader().read_resources(output, threshold=0.85)
    print(f"Gold: {resources.gold.value}\nElixir: {resources.elixir.value}\nDark Elixir: {resources.dark_elixir.value}")
    templates = list(asset_path("templates").rglob("*.png"))
    missing = [str(path) for path in templates if not path.is_file()]
    print(f"Device: {device.serial}\nTemplates checked: {len(templates)}\nMissing templates: {len(missing)}")
    print(f"Artifacts: {output}, {DEBUG_DIRECTORY}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
