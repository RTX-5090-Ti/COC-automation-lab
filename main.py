from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from adb_controller import ADBController, ADBError
from resource_reader import ResourceReadResult, ResourceReader, ResourceReaderError
from screen_detector import ScreenDetectionError, ScreenDetectionResult, ScreenState, detect_screen


DEFAULT_PACKAGE_NAME = "com.supercell.clashofclans"
CURRENT_SCREENSHOT_PATH = Path("screenshots/current/current.png")
DEBUG_DIRECTORY = Path("screenshots/debug")


def configure_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format="[%(levelname)s] %(message)s")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ENEMY_BASE resource reader.")
    parser.add_argument("--adb-path", help="Optional explicit path to the adb executable.")
    parser.add_argument("--device-id", help="Optional explicit device id.")
    parser.add_argument(
        "--package",
        default=DEFAULT_PACKAGE_NAME,
        help=f"Android package name to inspect. Default: {DEFAULT_PACKAGE_NAME}",
    )
    parser.add_argument(
        "--screen-threshold",
        type=float,
        default=0.85,
        help="Confidence threshold for screen template matching. Default: 0.85",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser


def _log_detection_result(detection_result: ScreenDetectionResult) -> None:
    if detection_result.state is ScreenState.UNKNOWN:
        logging.warning("Detected screen: UNKNOWN")
        logging.info("Best candidate confidence: %.2f", detection_result.best_candidate_confidence or 0.0)
        if detection_result.debug_image_path:
            logging.info("Unknown screenshot saved to %s", detection_result.debug_image_path.as_posix())
        return

    logging.info("Detected screen: %s", detection_result.state.value)
    logging.info("Matched template: %s", detection_result.matched_template_name)
    logging.info("Confidence: %.2f", detection_result.confidence)
    if detection_result.center is not None:
        logging.info("Matched center: (%s, %s)", detection_result.center[0], detection_result.center[1])
    if detection_result.debug_image_path:
        logging.info("Debug image saved to %s", detection_result.debug_image_path.as_posix())

def _detect_current_screen(
    controller: ADBController,
    *,
    threshold: float,
) -> ScreenDetectionResult:
    screenshot_path = controller.capture_screenshot(CURRENT_SCREENSHOT_PATH)
    logging.info("Screenshot captured")
    logging.info("Screenshot saved to %s", screenshot_path.as_posix())
    return detect_screen(
        screenshot_path,
        threshold=threshold,
        debug_directory=DEBUG_DIRECTORY,
    )


def _log_resource_result(resource_result: ResourceReadResult) -> None:
    if resource_result.gold.value is not None:
        logging.info("Gold parsed value: %s", resource_result.gold.value)
    else:
        logging.error(
            "Gold reading failed. Icon confidence: %.2f, raw OCR text: %r",
            resource_result.gold.icon_confidence,
            resource_result.gold.raw_ocr_text,
        )

    if resource_result.elixir.value is not None:
        logging.info("Elixir parsed value: %s", resource_result.elixir.value)
    else:
        logging.error(
            "Elixir reading failed. Icon confidence: %.2f, raw OCR text: %r",
            resource_result.elixir.icon_confidence,
            resource_result.elixir.raw_ocr_text,
        )

    if resource_result.dark_elixir.value is not None:
        logging.info("Dark Elixir parsed value: %s", resource_result.dark_elixir.value)
    else:
        logging.error(
            "Dark Elixir reading failed. Icon confidence: %.2f, raw OCR text: %r",
            resource_result.dark_elixir.icon_confidence,
            resource_result.dark_elixir.raw_ocr_text,
        )

    if resource_result.overall_success:
        logging.info("Resource reading completed")
    else:
        logging.error("Resource reading completed with failures")

    logging.info("No gameplay action was performed")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    configure_logging(args.debug)

    try:
        controller = ADBController(adb_path=args.adb_path, device_id=args.device_id)
        logging.info("ADB is available at: %s", controller.adb_path)
        controller.check_adb_available()

        device = controller.select_device(preferred_serial=args.device_id)
        logging.info("Device connected: %s", device.serial)
        if device.details:
            logging.debug("Device details: %s", device.details)

        installed_packages = controller.get_installed_packages()
        logging.debug("Installed package count: %s", len(installed_packages))

        if args.package not in installed_packages:
            similar = [pkg for pkg in installed_packages if "clash" in pkg.lower() or "supercell" in pkg.lower()]
            detail = f" Nearby matches: {', '.join(similar)}" if similar else ""
            raise ADBError(f"Package not installed: {args.package}.{detail}")
        logging.info("Clash of Clans package is installed: %s", args.package)

        if controller.is_app_running(args.package):
            logging.info("Clash of Clans process is running")
        else:
            logging.warning("Clash of Clans process is not running")

        foreground_app = controller.get_foreground_app()
        if foreground_app:
            logging.info("Foreground app: %s", foreground_app)
        else:
            logging.warning("Could not determine foreground app")

        if foreground_app == args.package:
            logging.info("Clash of Clans is in the foreground")
        else:
            logging.warning("Clash of Clans is not in the foreground")

        detection_result = _detect_current_screen(controller, threshold=args.screen_threshold)
        _log_detection_result(detection_result)

        if detection_result.state is not ScreenState.ENEMY_BASE:
            logging.info("This milestone expects ENEMY_BASE. Current screen is %s.", detection_result.state.value)
            logging.info("No gameplay action was performed")
            return 0

        resource_reader = ResourceReader()
        resource_result = resource_reader.read_resources(
            CURRENT_SCREENSHOT_PATH,
            threshold=args.screen_threshold,
        )
        _log_resource_result(resource_result)
        return 0 if resource_result.overall_success else 1

    except ADBError as error:
        logging.error(str(error))
        return 1
    except ResourceReaderError as error:
        logging.error(str(error))
        logging.info("No gameplay action was performed")
        return 1
    except ScreenDetectionError as error:
        logging.error(str(error))
        return 1
    except Exception as error:  # pragma: no cover
        logging.exception("Unexpected failure: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
