from __future__ import annotations

import logging
import time
from pathlib import Path

from adb_controller import ADBController
from battle_end_controller import BattleEndController
from battlefield_fingerprint import build_battlefield_fingerprint, compare_fingerprints
from decision_engine import BotConfig, Decision, DecisionResult, evaluate_resources
from resource_reader import ResourceReadResult, ResourceReader
from screen_detector import ScreenDetectionResult, ScreenState, detect_screen
from strategies.attack_plan import save_attack_plan_debug_image
from strategies.sneaky_goblin import SneakyGoblinPlanner, SneakyGoblinPlanningError
from tap_utils import TapPointError, select_random_point_in_box


CURRENT_SCREENSHOT_PATH = Path("screenshots/current/current.png")
DEBUG_DIRECTORY = Path("screenshots/debug")
STATE_TRANSITION_TIMEOUT_SECONDS = 15.0
TWO_POINT_DEPLOYMENT_GROUPS = 2
GOBLINS_PER_TEST_POINT = 2
POST_DEPLOYMENT_WAIT_SECONDS = 5.0
ATTACK_PLAN_DEBUG_PATH = DEBUG_DIRECTORY / "attack_plan_sneaky_goblin.png"


class TrialFlowControllerError(Exception):
    """Raised when the one-pass HOME-to-HOME trial cannot complete safely."""


class TrialFlowController:
    """Runs one bounded resource-search trial without deploying troops."""

    def __init__(
        self,
        *,
        adb_controller: ADBController,
        resource_reader: ResourceReader,
        bot_config: BotConfig,
        package_name: str,
        screen_threshold: float,
        battlefield_diff_threshold: float,
        dry_run: bool,
        two_point_deployment_test: bool = False,
    ) -> None:
        self.adb_controller = adb_controller
        self.resource_reader = resource_reader
        self.bot_config = bot_config
        self.package_name = package_name
        self.screen_threshold = screen_threshold
        self.battlefield_diff_threshold = battlefield_diff_threshold
        self.dry_run = dry_run
        self.two_point_deployment_test = two_point_deployment_test

    def run(self) -> int:
        home = self._capture_and_detect()
        if home.state is not ScreenState.HOME:
            raise TrialFlowControllerError(
                f"Full-flow test expects HOME; detected {home.state.value}."
            )

        if self.dry_run:
            self._tap_detection(home, "Attack button", dry_run=True)
            logging.info("Dry-run stops before changing the screen")
            logging.info("No gameplay action was performed")
            return 0

        self._tap_detection(home, "Attack button")
        attack_menu = self._wait_for_state(ScreenState.ATTACK_MENU, STATE_TRANSITION_TIMEOUT_SECONDS)
        self._tap_detection(attack_menu, "Find a Match button")

        army_confirmation = self._wait_for_state(
            ScreenState.ARMY_CONFIRMATION,
            STATE_TRANSITION_TIMEOUT_SECONDS,
        )
        self._tap_detection(army_confirmation, "Confirm Attack button", use_action_template=True)

        enemy_base = self._wait_for_state(
            ScreenState.ENEMY_BASE,
            self.bot_config.new_base_timeout_seconds,
        )
        bases_checked = 1
        next_taps = 0
        while True:
            decision = self._read_and_decide()
            if decision.decision is Decision.ATTACK:
                break

            if decision.decision is Decision.UNDECIDED:
                logging.warning("Resource OCR is unavailable; treating this base as SKIP")
            else:
                logging.info("Resource decision is SKIP")

            if (
                next_taps >= self.bot_config.max_next_taps
                or bases_checked >= self.bot_config.max_bases_to_check
            ):
                logging.warning("Search limit reached without an ATTACK decision")
                logging.info("No further gameplay action was performed")
                return 0

            old_fingerprint = build_battlefield_fingerprint(CURRENT_SCREENSHOT_PATH)
            self._tap_detection(enemy_base, "Next button")
            next_taps += 1
            bases_checked += 1
            enemy_base = self._wait_for_different_enemy_base(old_fingerprint)

        if self.two_point_deployment_test:
            return self._deploy_two_points_then_end_battle()

        logging.info("Suitable base found; ending the battle without deploying troops")
        return BattleEndController(
            adb_controller=self.adb_controller,
            package_name=self.package_name,
            threshold=self.screen_threshold,
            dry_run=False,
            return_home_timeout_seconds=self.bot_config.new_base_timeout_seconds,
        ).run()

    def _deploy_two_points_then_end_battle(self) -> int:
        try:
            planning_result = SneakyGoblinPlanner().plan_attack(
                screenshot_path=CURRENT_SCREENSHOT_PATH,
                config=self.bot_config,
            )
        except SneakyGoblinPlanningError as error:
            raise TrialFlowControllerError(str(error)) from error

        plan = planning_result.attack_plan
        slot = planning_result.troop_slot_result
        actions = plan.actions[:TWO_POINT_DEPLOYMENT_GROUPS]
        if not plan.valid or slot.bounding_box is None or len(actions) != TWO_POINT_DEPLOYMENT_GROUPS:
            raise TrialFlowControllerError(plan.error_message or "Two deployment points could not be generated.")

        save_attack_plan_debug_image(
            screenshot_path=CURRENT_SCREENSHOT_PATH,
            output_path=ATTACK_PLAN_DEBUG_PATH,
            battlefield_roi=planning_result.battlefield_roi,
            battlefield_polygon=planning_result.battlefield_polygon,
            excluded_regions=planning_result.excluded_regions,
            troop_slot_box=slot.bounding_box,
            attack_plan=plan,
        )
        logging.info("Attack plan debug image saved to %s", ATTACK_PLAN_DEBUG_PATH.as_posix())
        logging.info(
            "Two-point deployment test: point %s (%s, %s), point %s (%s, %s)",
            actions[0].sequence_number,
            actions[0].x,
            actions[0].y,
            actions[1].sequence_number,
            actions[1].x,
            actions[1].y,
        )
        slot_tap_point = self._select_slot_tap_point(slot.bounding_box, plan)

        self._assert_game_ready()
        self.adb_controller.tap(*slot_tap_point)
        logging.info("Sneaky Goblin slot tapped once at (%s, %s)", *slot_tap_point)
        time.sleep(0.2)

        for action in actions:
            self._validate_deployment_point(action.x, action.y, plan.screenshot_width, plan.screenshot_height)
            for _ in range(GOBLINS_PER_TEST_POINT):
                self.adb_controller.tap(action.x, action.y)
            logging.info(
                "Deployed %s Sneaky Goblins at point %s (%s, %s)",
                GOBLINS_PER_TEST_POINT,
                action.sequence_number,
                action.x,
                action.y,
            )

        logging.info("Waiting %.1f seconds before surrendering", POST_DEPLOYMENT_WAIT_SECONDS)
        time.sleep(POST_DEPLOYMENT_WAIT_SECONDS)
        return BattleEndController(
            adb_controller=self.adb_controller,
            package_name=self.package_name,
            threshold=self.screen_threshold,
            dry_run=False,
            return_home_timeout_seconds=self.bot_config.new_base_timeout_seconds,
        ).run()

    @staticmethod
    def _select_slot_tap_point(slot_box, plan) -> tuple[int, int]:
        try:
            return select_random_point_in_box(
                slot_box,
                (plan.screenshot_width, plan.screenshot_height),
            )
        except TapPointError as error:
            raise TrialFlowControllerError(str(error)) from error

    @staticmethod
    def _validate_deployment_point(x: int, y: int, width: int, height: int) -> None:
        if x < 0 or y < 0 or x >= width or y >= height:
            raise TrialFlowControllerError(f"Deployment point is outside the current screenshot: ({x}, {y})")

    def _read_and_decide(self) -> DecisionResult:
        resource_result = self.resource_reader.read_resources(
            CURRENT_SCREENSHOT_PATH,
            threshold=self.screen_threshold,
        )
        decision = evaluate_resources(resource_result, self.bot_config)
        self._log_resource_result(resource_result)
        self._log_decision(decision)
        return decision

    def _wait_for_state(self, expected_state: ScreenState, timeout_seconds: float) -> ScreenDetectionResult:
        deadline = time.monotonic() + timeout_seconds
        last_state = ScreenState.UNKNOWN
        while time.monotonic() < deadline:
            time.sleep(1.0)
            detection = self._capture_and_detect()
            last_state = detection.state
            if detection.state is expected_state:
                return detection

        self._save_failure_screenshot()
        raise TrialFlowControllerError(
            f"Timed out waiting for {expected_state.value}; last detected state was {last_state.value}."
        )

    def _wait_for_different_enemy_base(self, old_fingerprint) -> ScreenDetectionResult:
        deadline = time.monotonic() + self.bot_config.new_base_timeout_seconds
        last_state = ScreenState.UNKNOWN
        while time.monotonic() < deadline:
            time.sleep(1.0)
            detection = self._capture_and_detect()
            last_state = detection.state
            if detection.state is not ScreenState.ENEMY_BASE:
                continue
            new_fingerprint = build_battlefield_fingerprint(CURRENT_SCREENSHOT_PATH)
            difference = compare_fingerprints(old_fingerprint, new_fingerprint)
            logging.debug("New-base fingerprint difference: %.4f", difference)
            if difference >= self.battlefield_diff_threshold:
                logging.info("Transition successful: ENEMY_BASE -> NEW_ENEMY_BASE")
                return detection

        self._save_failure_screenshot()
        raise TrialFlowControllerError(
            "Timed out waiting for a different ENEMY_BASE after one Next tap. "
            f"Last detected state was {last_state.value}."
        )

    def _tap_detection(
        self,
        detection: ScreenDetectionResult,
        label: str,
        *,
        use_action_template: bool = False,
        dry_run: bool = False,
    ) -> None:
        if use_action_template:
            confidence = detection.action_confidence
            box = detection.action_bounding_box
            if confidence is None or box is None or confidence < self.screen_threshold:
                raise TrialFlowControllerError(f"{label} confidence is below threshold.")
        else:
            confidence = detection.confidence
            box = detection.bounding_box
            if box is None or confidence < self.screen_threshold:
                raise TrialFlowControllerError(f"{label} confidence is below threshold.")

        try:
            tap_point = select_random_point_in_box(box, detection.screenshot_size)
        except TapPointError as error:
            raise TrialFlowControllerError(str(error)) from error

        logging.info("%s confidence: %.2f", label, confidence)
        if dry_run:
            logging.info("Dry-run: would tap %s at (%s, %s)", label, *tap_point)
            return

        self._assert_game_ready()
        self.adb_controller.tap(*tap_point)
        logging.info("%s tapped once at (%s, %s)", label, *tap_point)

    def _capture_and_detect(self) -> ScreenDetectionResult:
        self._assert_game_ready()
        self.adb_controller.capture_screenshot(CURRENT_SCREENSHOT_PATH)
        logging.info("Screenshot captured")
        logging.info("Screenshot saved to %s", CURRENT_SCREENSHOT_PATH.as_posix())
        return detect_screen(
            CURRENT_SCREENSHOT_PATH,
            threshold=self.screen_threshold,
            debug_directory=DEBUG_DIRECTORY,
        )

    def _assert_game_ready(self) -> None:
        foreground_app = self.adb_controller.get_foreground_app()
        if foreground_app != self.package_name:
            raise TrialFlowControllerError(
                f"Clash of Clans left the foreground. Current foreground app: {foreground_app or 'unknown'}"
            )

    @staticmethod
    def _log_resource_result(result: ResourceReadResult) -> None:
        for name, reading in (
            ("Gold", result.gold),
            ("Elixir", result.elixir),
            ("Dark Elixir", result.dark_elixir),
        ):
            if reading.value is None:
                logging.error("%s reading failed. Raw OCR text: %r", name, reading.raw_ocr_text)
            else:
                logging.info("%s parsed value: %s", name, reading.value)

    @staticmethod
    def _log_decision(result: DecisionResult) -> None:
        for reason in result.reasons:
            logging.info("Reason: %s", reason)
        logging.info("Decision: %s", result.decision.value)

    @staticmethod
    def _save_failure_screenshot() -> Path:
        DEBUG_DIRECTORY.mkdir(parents=True, exist_ok=True)
        output = DEBUG_DIRECTORY / "full_flow_failure_latest.png"
        output.write_bytes(CURRENT_SCREENSHOT_PATH.read_bytes())
        return output
