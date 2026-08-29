from __future__ import annotations

import argparse
import logging
import sys

from adb_controller import ADBController, ADBError
from battle_end_controller import BattleEndController, BattleEndControllerError
from battlefield_fingerprint import BattlefieldFingerprintError
from decision_engine import DecisionEngineError, load_bot_config
from resource_reader import ResourceReader, ResourceReaderError
from screen_detector import ScreenDetectionError
from search_controller import SearchController, SearchControllerError
from trial_flow_controller import TrialFlowController, TrialFlowControllerError


DEFAULT_PACKAGE_NAME = "com.supercell.clashofclans"


def parse_deployment_point_indices(value: str) -> tuple[int, ...]:
    try:
        indices = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as error:
        raise argparse.ArgumentTypeError("Deployment points must be comma-separated positive integers.") from error
    if not indices or any(index <= 0 for index in indices) or len(set(indices)) != len(indices):
        raise argparse.ArgumentTypeError("Deployment points must be unique positive integers.")
    return indices


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
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument(
        "--three-point-deployment-test",
        action="store_true",
        help="Test exactly 2 Sneaky Goblins at each of planned points 1, 2, and 3.",
    )
    action_group.add_argument(
        "--deployment-points-test",
        type=parse_deployment_point_indices,
        metavar="POINTS",
        help="Test exactly 2 Sneaky Goblins at comma-separated planned point numbers, then stop.",
    )
    action_group.add_argument(
        "--end-battle-test",
        "--end-battle-no-deployment-test",
        dest="end_battle_test",
        action="store_true",
        help="Test one ENEMY_BASE to HOME transition, with or without the confirmation dialog.",
    )
    action_group.add_argument(
        "--full-flow-test",
        action="store_true",
        help="Run one HOME -> ENEMY_BASE -> HOME resource-search trial without deploying troops.",
    )
    action_group.add_argument(
        "--full-flow-two-point-deployment-test",
        action="store_true",
        help="Run one full flow, deploy 2 Goblins at points 1 and 2, then return HOME.",
    )
    action_group.add_argument(
        "--full-flow-deployment-points-test",
        type=parse_deployment_point_indices,
        metavar="POINTS",
        help="Run one full flow, deploy 2 Goblins at the selected points, wait 5 seconds, then return HOME.",
    )
    action_group.add_argument(
        "--full-flow-super-wall-breaker-point-1-test",
        action="store_true",
        help="Run one full flow, deploy exactly one Super Wall Breaker at point 1, then return HOME.",
    )
    action_group.add_argument(
        "--full-flow-setup-1-test",
        action="store_true",
        help="Run setup 1: two clockwise Goblin rounds with one Super Wall Breaker on each edge.",
    )
    action_group.add_argument(
        "--full-flow-setup-2-test",
        action="store_true",
        help="Run setup 2: complete each edge's Goblin/Super Wall Breaker sequence before the next edge.",
    )
    action_group.add_argument("--full-flow-setup-3-test", action="store_true", help="Run setup 3: two counter-clockwise Goblin rounds starting at point 10.")
    action_group.add_argument(
        "--full-flow-setup-4-test",
        action="store_true",
        help="Run setup 4: two Goblins on AB/DA, then two one-Goblin BC/CD rounds with Super Wall Breakers.",
    )
    action_group.add_argument(
        "--full-flow-setup-5-test",
        action="store_true",
        help="Run setup 5: one Super Wall Breaker per edge, then two Goblins at random edge points.",
    )
    action_group.add_argument(
        "--full-flow-setup-6-test",
        action="store_true",
        help="Run setup 6: one Super Wall Breaker per edge and mixed random Goblin counts by edge.",
    )
    action_group.add_argument(
        "--full-flow-setup-7-test",
        action="store_true",
        help="Run setup 7: mixed random Goblin counts by edge without Super Wall Breakers.",
    )
    action_group.add_argument(
        "--full-flow-setup-8-test",
        action="store_true",
        help="Run setup 8: clockwise and counter-clockwise Goblin sweeps separated by Super Wall Breakers.",
    )
    action_group.add_argument("--full-flow-random-setup-test", action="store_true", help="Run one full flow and randomly choose Setup 1 through 8 after ATTACK.")
    parser.add_argument(
        "--return-home-timeout-seconds",
        type=float,
        default=10.0,
        help="Maximum wait after End Battle before HOME must appear. Default: 10 seconds.",
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

        if args.end_battle_test:
            if args.return_home_timeout_seconds <= 0:
                parser.error("--return-home-timeout-seconds must be greater than zero.")
            return BattleEndController(
                adb_controller=controller,
                package_name=args.package,
                threshold=args.screen_threshold,
                dry_run=False if args.no_dry_run else bot_config.dry_run,
                return_home_timeout_seconds=args.return_home_timeout_seconds,
                screen_transition_poll_seconds_options=bot_config.screen_transition_poll_seconds_options,
            ).run()

        if (
            args.full_flow_test
            or args.full_flow_two_point_deployment_test
            or args.full_flow_deployment_points_test
            or args.full_flow_super_wall_breaker_point_1_test
            or args.full_flow_setup_1_test
            or args.full_flow_setup_2_test
            or args.full_flow_setup_3_test
            or args.full_flow_setup_4_test
            or args.full_flow_setup_5_test
            or args.full_flow_setup_6_test
            or args.full_flow_setup_7_test
            or args.full_flow_setup_8_test
            or args.full_flow_random_setup_test
        ):
            return TrialFlowController(
                adb_controller=controller,
                resource_reader=ResourceReader(),
                bot_config=bot_config,
                package_name=args.package,
                screen_threshold=args.screen_threshold,
                battlefield_diff_threshold=args.battlefield_diff_threshold,
                dry_run=False if args.no_dry_run else bot_config.dry_run,
                two_point_deployment_test=args.full_flow_two_point_deployment_test,
                deployment_point_test_indices=args.full_flow_deployment_points_test or (),
                super_wall_breaker_test_point_1=args.full_flow_super_wall_breaker_point_1_test,
                setup_1_test=args.full_flow_setup_1_test,
                setup_2_test=args.full_flow_setup_2_test,
                setup_3_test=args.full_flow_setup_3_test,
                setup_4_test=args.full_flow_setup_4_test,
                setup_5_test=args.full_flow_setup_5_test,
                setup_6_test=args.full_flow_setup_6_test,
                setup_7_test=args.full_flow_setup_7_test,
                setup_8_test=args.full_flow_setup_8_test,
                random_setup_test=args.full_flow_random_setup_test,
            ).run()

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
            deployment_point_test_indices=args.deployment_points_test or (),
        )
        return search_controller.run()

    except SearchControllerError:
        return 1
    except BattleEndControllerError as error:
        logging.error(str(error))
        return 1
    except TrialFlowControllerError as error:
        logging.error(str(error))
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
