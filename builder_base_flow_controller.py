from __future__ import annotations

import logging
import random
import time
from pathlib import Path

from adb_controller import ADBController
from builder_base_slots import save_builder_army_slots_debug
from decision_engine import BotConfig
from project_paths import CURRENT_SCREENSHOT_PATH, DEBUG_DIRECTORY, asset_path
from runtime.runtime_control import NULL_RUNTIME_CONTROL, RuntimeControl
from screen_detector import ScreenDetectionResult, ScreenState, detect_screen
from tap_utils import TapPointError, select_random_point_in_box


BUILDER_TEMPLATES = (
    (ScreenState.BUILDER_HOME, asset_path("templates", "builder_base", "builder_attack_button.png"), "builder_attack_button.png"),
    (ScreenState.BUILDER_ATTACK_MENU, asset_path("templates", "builder_base", "find_now_button.png"), "find_now_button.png"),
    (ScreenState.BUILDER_ENEMY_BASE, asset_path("templates", "builder_base", "enemy_base_banner.png"), "enemy_base_banner.png"),
)


class BuilderBaseFlowControllerError(Exception):
    """Raised when Builder Base navigation cannot complete safely."""


class BuilderBaseFlowController:
    """Navigates Builder Base to an enemy base without OCR or troop deployment."""

    def __init__(self, *, adb_controller: ADBController, bot_config: BotConfig, package_name: str, screen_threshold: float, dry_run: bool, control: RuntimeControl = NULL_RUNTIME_CONTROL) -> None:
        self.adb_controller = adb_controller
        self.bot_config = bot_config
        self.package_name = package_name
        self.screen_threshold = screen_threshold
        self.dry_run = dry_run
        self.control = control
        self.adb_controller.set_gameplay_input_allowed(not dry_run)

    def run(self) -> int:
        enemy_base = self.navigate_to_enemy_base()
        self.control.report(gameScreen=enemy_base.state.value, phase="BUILDER_ENEMY_BASE_READY", basesChecked=1, maxBases=1, decision="NO_ACTION", decisionReasons=["Builder Base deployment and resource filtering are not implemented yet."])
        logging.info("Builder enemy base is ready. No OCR, troop deployment, or return-home action was performed.")
        return 0

    def navigate_to_enemy_base(self) -> ScreenDetectionResult:
        home = self._capture_and_detect()
        if home.state is not ScreenState.BUILDER_HOME:
            raise BuilderBaseFlowControllerError(f"Builder Base flow expects BUILDER_HOME; detected {home.state.value}.")
        if self.dry_run:
            self._tap("Builder Attack button", ScreenState.BUILDER_HOME, dry_run=True)
            logging.info("Dry-run stops before changing the screen")
            return home

        self._tap("Builder Attack button", ScreenState.BUILDER_HOME)
        self._wait_for_state(ScreenState.BUILDER_ATTACK_MENU, 15.0)
        self._tap("Find Now button", ScreenState.BUILDER_ATTACK_MENU)
        enemy_base = self._wait_for_state(ScreenState.BUILDER_ENEMY_BASE, self.bot_config.new_base_timeout_seconds)
        self._wait_with_checkpoints(random.choice(self.bot_config.enemy_base_settle_seconds_options))
        if self._capture_and_detect().state is not ScreenState.BUILDER_ENEMY_BASE:
            raise BuilderBaseFlowControllerError("Builder enemy base changed before its loading delay completed.")
        slots_debug_path = save_builder_army_slots_debug(
            CURRENT_SCREENSHOT_PATH,
            DEBUG_DIRECTORY / "builder_base_army_slots.png",
        )
        self.control.report(debugArtifactPaths=[slots_debug_path.as_posix()])
        return enemy_base

    def _wait_for_state(self, expected_state: ScreenState, timeout_seconds: float) -> ScreenDetectionResult:
        deadline = time.monotonic() + timeout_seconds
        last_state = ScreenState.UNKNOWN
        while time.monotonic() < deadline:
            self.control.checkpoint(f"WAIT_{expected_state.value}")
            self._wait_with_checkpoints(random.choice(self.bot_config.screen_transition_poll_seconds_options))
            detection = self._capture_and_detect()
            last_state = detection.state
            if detection.state is expected_state:
                return detection
        self._save_failure_screenshot()
        raise BuilderBaseFlowControllerError(f"Timed out waiting for {expected_state.value}; last detected state was {last_state.value}.")

    def _tap(self, label: str, expected_state: ScreenState, *, dry_run: bool = False) -> None:
        # A fresh screenshot prevents taps based on stale animation coordinates.
        detection = self._capture_and_detect()
        if detection.state is not expected_state or detection.bounding_box is None:
            raise BuilderBaseFlowControllerError(f"{label} expected {expected_state.value}; detected {detection.state.value}.")
        if detection.confidence < self.screen_threshold:
            raise BuilderBaseFlowControllerError(f"{label} confidence is below threshold.")
        try:
            point = select_random_point_in_box(detection.bounding_box, detection.screenshot_size)
        except TapPointError as error:
            raise BuilderBaseFlowControllerError(str(error)) from error
        logging.info("%s confidence: %.2f", label, detection.confidence)
        if dry_run:
            logging.info("Dry-run: would tap %s at (%s, %s)", label, *point)
            return
        self._assert_game_ready()
        self.control.checkpoint(label.upper().replace(" ", "_"))
        self.adb_controller.tap(*point)
        logging.info("%s tapped once at (%s, %s)", label, *point)

    def _capture_and_detect(self) -> ScreenDetectionResult:
        self._assert_game_ready()
        self.adb_controller.capture_screenshot(CURRENT_SCREENSHOT_PATH)
        detection = detect_screen(CURRENT_SCREENSHOT_PATH, threshold=self.screen_threshold, debug_directory=DEBUG_DIRECTORY, templates=BUILDER_TEMPLATES)
        self.control.report(gameScreen=detection.state.value, screenConfidence=detection.confidence, screenDetails={"template": detection.matched_template_name, "bestCandidateConfidence": detection.best_candidate_confidence}, screenshotPath=CURRENT_SCREENSHOT_PATH.as_posix(), phase=f"BUILDER_{detection.state.value}")
        return detection

    def _assert_game_ready(self) -> None:
        self.control.checkpoint()
        if self.adb_controller.get_foreground_app() != self.package_name:
            raise BuilderBaseFlowControllerError("Clash of Clans is not in the foreground.")

    def _wait_with_checkpoints(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.control.checkpoint()
            time.sleep(min(0.1, deadline - time.monotonic()))

    @staticmethod
    def _save_failure_screenshot() -> Path:
        DEBUG_DIRECTORY.mkdir(parents=True, exist_ok=True)
        output = DEBUG_DIRECTORY / "builder_base_flow_failure_latest.png"
        if CURRENT_SCREENSHOT_PATH.is_file():
            output.write_bytes(CURRENT_SCREENSHOT_PATH.read_bytes())
        return output
