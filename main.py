from __future__ import annotations

import argparse
import logging
import sys

from adb_controller import ADBController, ADBError
from battlefield_fingerprint import BattlefieldFingerprintError
from decision_engine import DecisionEngineError, load_bot_config
from resource_reader import ResourceReader, ResourceReaderError
from screen_detector import ScreenDetectionError
from search_controller import SearchController, SearchControllerError


DEFAULT_PACKAGE_NAME = "com.supercell.clashofclans"


def configure_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format="[%(levelname)s] %(message)s")
    logging.getLogger("pytesseract").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded enemy-base resource search.")
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
        "--battlefield-diff-threshold",
        type=float,
        default=0.05,
        help="Minimum normalized battlefield difference required to confirm a new base. Default: 0.05",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Allow live ADB actions for explicitly enabled tests.",
    )
    parser.add_argument(
        "--three-point-deployment-test",
        action="store_true",
        help="Test exactly 2 Sneaky Goblins at each of planned points 1, 2, and 3.",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    configure_logging(args.debug)

    try:
        bot_config = load_bot_config()
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
            raise ADBError(f"Package not installed: {args.package}")
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

        resource_reader = ResourceReader()
        search_controller = SearchController(
            adb_controller=controller,
            resource_reader=resource_reader,
            bot_config=bot_config,
            package_name=args.package,
            screen_threshold=args.screen_threshold,
            debug=args.debug,
            live_override=args.no_dry_run,
            battlefield_diff_threshold=args.battlefield_diff_threshold,
            three_point_deployment_test=args.three_point_deployment_test,
        )
        return search_controller.run()

    except SearchControllerError:
        return 1
    except (ADBError, BattlefieldFingerprintError) as error:
        logging.error(str(error))
        return 1
    except (DecisionEngineError, ResourceReaderError, ScreenDetectionError) as error:
        logging.error(str(error))
        logging.info("No gameplay action was performed")
        return 1
    except Exception as error:  # pragma: no cover
        logging.exception("Unexpected failure: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
