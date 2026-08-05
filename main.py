from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from adb_controller import ADBController, ADBError
from screen_detector import ScreenDetectionError, ScreenState, detect_screen


DEFAULT_PACKAGE_NAME = "com.supercell.clashofclans"
CURRENT_SCREENSHOT_PATH = Path("screenshots/current/current.png")
DEBUG_DIRECTORY = Path("screenshots/debug")
DRY_RUN = True
TRANSITION_DELAY_SECONDS = 2.0
MAX_HOME_TO_ATTACK_MENU_RETRIES = 2


def configure_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format="[%(levelname)s] %(message)s")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Milestone 3B runner for CoC Vision Automation Lab.")
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
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=TRANSITION_DELAY_SECONDS,
        help=f"Delay after tapping before verification. Default: {TRANSITION_DELAY_SECONDS}",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=DRY_RUN,
        help="Enable dry-run mode. This is the default behavior.",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Disable dry-run mode and allow one controlled HOME -> ATTACK_MENU transition.",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser


def _log_dry_run_action(detection_result) -> None:
    logging.info("Dry-run mode is enabled")
    if detection_result.state is ScreenState.HOME and detection_result.center is not None:
        logging.info(
            "Dry-run: would tap the HOME Attack button at (%s, %s)",
            detection_result.center[0],
            detection_result.center[1],
        )
    else:
        logging.info("Dry-run: no tap will be sent for state %s", detection_result.state.value)


def _transition_home_to_attack_menu(
    *,
    controller: ADBController,
    initial_detection,
    threshold: float,
    delay_seconds: float,
) -> bool:
    detection_result = initial_detection

    for attempt_index in range(MAX_HOME_TO_ATTACK_MENU_RETRIES + 1):
        _validate_tap_target(detection_result)
        center_x, center_y = detection_result.center
        logging.info("Sending tap to detected HOME Attack button at (%s, %s)", center_x, center_y)
        controller.tap(center_x, center_y)

        time.sleep(delay_seconds)

        screenshot_path = controller.capture_screenshot(CURRENT_SCREENSHOT_PATH)
        logging.info("Verification screenshot captured")
        detection_result = detect_screen(
            screenshot_path,
            threshold=threshold,
            debug_directory=DEBUG_DIRECTORY,
        )

        if detection_result.state is ScreenState.ATTACK_MENU:
            logging.info("Transition successful: HOME -> ATTACK_MENU")
            return True

        logging.warning("Transition verification failed. Detected state: %s", detection_result.state.value)

        if attempt_index >= MAX_HOME_TO_ATTACK_MENU_RETRIES:
            failure_path = _save_transition_failure_screenshot(screenshot_path)
            logging.error("Transition failed after %s retries", MAX_HOME_TO_ATTACK_MENU_RETRIES)
            logging.info("Failure screenshot saved to %s", failure_path.as_posix())
            return False

        if detection_result.state is not ScreenState.HOME or detection_result.center is None:
            failure_path = _save_transition_failure_screenshot(screenshot_path)
            logging.error("Stopping safely because the latest screen is not HOME.")
            logging.info("Failure screenshot saved to %s", failure_path.as_posix())
            return False

        logging.info("Retrying transition with freshly detected HOME coordinates")

    return False


def _validate_tap_target(detection_result) -> None:
    if detection_result.center is None:
        raise ADBError("Cannot tap because no center coordinates were detected.")

    width, height = detection_result.screenshot_size
    x, y = detection_result.center

    if x < 0 or y < 0:
        raise ADBError(f"Tap coordinates must be non-negative. Received: ({x}, {y})")
    if x >= width or y >= height:
        raise ADBError(
            f"Tap coordinates are outside the screen bounds. "
            f"Received ({x}, {y}) for screen size ({width}, {height})."
        )


def _save_transition_failure_screenshot(screenshot_path: Path) -> Path:
    DEBUG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = DEBUG_DIRECTORY / f"transition_failed_{timestamp}.png"
    shutil.copyfile(screenshot_path, output_path)
    return output_path


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

        screenshot_path = controller.capture_screenshot(CURRENT_SCREENSHOT_PATH)
        logging.info("Screenshot captured")
        logging.info("Screenshot saved to %s", screenshot_path.as_posix())

        detection_result = detect_screen(
            screenshot_path,
            threshold=args.screen_threshold,
            debug_directory=DEBUG_DIRECTORY,
        )

        if detection_result.state is ScreenState.UNKNOWN:
            logging.warning("Detected screen: UNKNOWN")
            logging.info("Best candidate confidence: %.2f", detection_result.best_candidate_confidence or 0.0)
            if detection_result.debug_image_path:
                logging.info("Unknown screenshot saved to %s", detection_result.debug_image_path.as_posix())
        else:
            logging.info("Detected screen: %s", detection_result.state.value)
            logging.info("Matched template: %s", detection_result.matched_template_name)
            logging.info("Confidence: %.2f", detection_result.confidence)
            if detection_result.center is not None:
                logging.info("Matched center: (%s, %s)", detection_result.center[0], detection_result.center[1])
            if detection_result.debug_image_path:
                logging.info("Debug image saved to %s", detection_result.debug_image_path.as_posix())

        if args.dry_run:
            _log_dry_run_action(detection_result)
            return 0

        if detection_result.state is not ScreenState.HOME:
            logging.info("No transition performed because the current screen is %s", detection_result.state.value)
            return 0

        transition_succeeded = _transition_home_to_attack_menu(
            controller=controller,
            initial_detection=detection_result,
            threshold=args.screen_threshold,
            delay_seconds=args.delay_seconds,
        )
        return 0 if transition_succeeded else 1

    except ADBError as error:
        logging.error(str(error))
        return 1
    except ScreenDetectionError as error:
        logging.error(str(error))
        return 1
    except Exception as error:  # pragma: no cover - defensive guard for CLI use
        logging.exception("Unexpected failure: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
