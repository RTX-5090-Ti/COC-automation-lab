from __future__ import annotations

import logging
import random
import time
from enum import Enum
from pathlib import Path

from adb_controller import ADBController
from screen_detector import TemplateDetectionResult, detect_template
from runtime.runtime_control import NULL_RUNTIME_CONTROL, RuntimeControl
from tap_utils import TapPointError, select_random_point_in_box


CURRENT_SCREENSHOT_PATH = Path("screenshots/current/current.png")
DEBUG_DIRECTORY = Path("screenshots/debug")
END_BATTLE_BUTTON_TEMPLATE_PATH = Path("templates/battle/end_battle_button.png")
SURRENDER_BUTTON_TEMPLATE_PATH = Path("templates/battle/surrender_button.png")
END_BATTLE_CONFIRM_DIALOG_TEMPLATE_PATH = Path("templates/battle/end_battle_confirm_dialog.png")
END_BATTLE_CONFIRM_OK_TEMPLATE_PATH = Path("templates/battle/end_battle_confirm_ok.png")
RETURN_HOME_BUTTON_TEMPLATE_PATH = Path("templates/battle/return_home_button.png")
HOME_ATTACK_BUTTON_TEMPLATE_PATH = Path("templates/home/attack_button.png")


class BattleEndControllerError(Exception):
    """Raised when the battle-end transition cannot complete safely."""


class EndBattleResult(str, Enum):
    HOME = "HOME"
    CONFIRMATION = "CONFIRMATION"


class BattleEndController:
    """Runs one verified ENEMY_BASE -> HOME transition through either game flow."""

    def __init__(
        self,
        *,
        adb_controller: ADBController,
        package_name: str,
        threshold: float,
        dry_run: bool,
        return_home_timeout_seconds: float,
        screen_transition_poll_seconds_options: tuple[float, ...],
        control: RuntimeControl = NULL_RUNTIME_CONTROL,
    ) -> None:
        self.adb_controller = adb_controller
        self.package_name = package_name
        self.threshold = threshold
        self.dry_run = dry_run
        self.return_home_timeout_seconds = return_home_timeout_seconds
        self.screen_transition_poll_seconds_options = screen_transition_poll_seconds_options
        self.control = control

    def run(self) -> int:
        self.control.checkpoint("BATTLE_EXIT")
        self.control.log(logging.INFO, "Battle exit controller started")
        self._capture_screenshot()
        exit_button = self._detect_battle_exit_button()
        if exit_button is None:
            logging.error(
                "Neither End Battle nor Surrender button was detected above the threshold."
            )
            logging.info("No gameplay action was performed")
            return 1
        button_label, button_detection = exit_button
        try:
            tap_point = select_random_point_in_box(
                button_detection.bounding_box,
                button_detection.screenshot_size,
            )
        except TapPointError as error:
            raise BattleEndControllerError(str(error)) from error
        logging.info("%s confidence: %.2f", button_label, button_detection.confidence)
        logging.info("%s random tap point: (%s, %s)", button_label, *tap_point)
        logging.info("Battle screen accepted by %s", button_detection.template_name)

        if self.dry_run:
            logging.info("Dry-run: would tap %s once at (%s, %s)", button_label, *tap_point)
            logging.info("No gameplay action was performed")
            return 0

        self._assert_game_ready()
        self.control.checkpoint("END_BATTLE")
        self.adb_controller.tap(*tap_point)
        logging.info("%s tapped once", button_label)
        result = self._wait_after_end_battle()
        if result is EndBattleResult.CONFIRMATION:
            logging.info("End Battle confirmation dialog detected")
            self._tap_current_template(END_BATTLE_CONFIRM_OK_TEMPLATE_PATH, "Confirm OK button")
            logging.info("Confirm OK button tapped once")
            self._wait_for_return_home_button()
            self._tap_current_template(RETURN_HOME_BUTTON_TEMPLATE_PATH, "Return Home button")
            logging.info("Return Home button tapped once")
            self._wait_for_home()

        logging.info("Transition successful: ENEMY_BASE -> HOME")
        logging.info("HOME verified by attack_button.png")
        logging.info("Program stopped; no further gameplay actions were performed")
        return 0

    def _detect_battle_exit_button(self) -> tuple[str, TemplateDetectionResult] | None:
        candidates = (
            (
                "End Battle button",
                detect_template(
                    CURRENT_SCREENSHOT_PATH,
                    END_BATTLE_BUTTON_TEMPLATE_PATH,
                    threshold=self.threshold,
                ),
            ),
            (
                "Surrender button",
                detect_template(
                    CURRENT_SCREENSHOT_PATH,
                    SURRENDER_BUTTON_TEMPLATE_PATH,
                    threshold=self.threshold,
                ),
            ),
        )
        valid_candidates = [candidate for candidate in candidates if candidate[1].found]
        if valid_candidates:
            return max(valid_candidates, key=lambda candidate: candidate[1].confidence)

        logging.error("End Battle button confidence: %.2f", candidates[0][1].confidence)
        logging.error("Surrender button confidence: %.2f", candidates[1][1].confidence)
        return None

    def _wait_after_end_battle(self) -> EndBattleResult:
        deadline = time.monotonic() + self.return_home_timeout_seconds
        last_home_confidence = 0.0
        last_dialog_confidence = 0.0
        logging.info("Waiting for HOME screen or confirmation dialog...")
        while time.monotonic() < deadline:
            self.control.checkpoint("WAIT_BATTLE_EXIT")
            self._capture_screenshot_after_delay()
            home_button = detect_template(
                CURRENT_SCREENSHOT_PATH,
                HOME_ATTACK_BUTTON_TEMPLATE_PATH,
                threshold=self.threshold,
            )
            last_home_confidence = home_button.confidence
            if home_button.found:
                return EndBattleResult.HOME
            dialog = detect_template(
                CURRENT_SCREENSHOT_PATH,
                END_BATTLE_CONFIRM_DIALOG_TEMPLATE_PATH,
                threshold=self.threshold,
            )
            last_dialog_confidence = dialog.confidence
            if dialog.found:
                return EndBattleResult.CONFIRMATION

        debug_path = self._save_failure_screenshot()
        raise BattleEndControllerError(
            "Neither HOME nor the End Battle confirmation dialog was detected after tapping End Battle. "
            f"Last HOME confidence: {last_home_confidence:.2f}. "
            f"Last dialog confidence: {last_dialog_confidence:.2f}. "
            f"Latest screenshot saved to {debug_path.as_posix()}."
        )

    def _wait_for_return_home_button(self) -> None:
        deadline = time.monotonic() + self.return_home_timeout_seconds
        last_confidence = 0.0
        logging.info("Waiting for Return Home button...")
        while time.monotonic() < deadline:
            self.control.checkpoint("WAIT_RETURN_HOME")
            self._capture_screenshot_after_delay()
            return_home = detect_template(
                CURRENT_SCREENSHOT_PATH,
                RETURN_HOME_BUTTON_TEMPLATE_PATH,
                threshold=self.threshold,
            )
            last_confidence = return_home.confidence
            if return_home.found:
                return

        self._raise_template_timeout("Return Home button", last_confidence)

    def _wait_for_home(self) -> None:
        deadline = time.monotonic() + self.return_home_timeout_seconds
        last_confidence = 0.0
        logging.info("Waiting for HOME screen...")
        while time.monotonic() < deadline:
            self.control.checkpoint("WAIT_HOME")
            self._capture_screenshot_after_delay()
            home_button = detect_template(
                CURRENT_SCREENSHOT_PATH,
                HOME_ATTACK_BUTTON_TEMPLATE_PATH,
                threshold=self.threshold,
            )
            last_confidence = home_button.confidence
            if home_button.found:
                return

        self._raise_template_timeout("HOME screen", last_confidence)

    def _tap_current_template(self, template_path: Path, label: str) -> None:
        detection = detect_template(
            CURRENT_SCREENSHOT_PATH,
            template_path,
            threshold=self.threshold,
        )
        if not detection.found:
            raise BattleEndControllerError(
                f"{label} confidence is below threshold: {detection.confidence:.2f}."
            )
        try:
            tap_point = select_random_point_in_box(detection.bounding_box, detection.screenshot_size)
        except TapPointError as error:
            raise BattleEndControllerError(str(error)) from error
        self._assert_game_ready()
        self.control.checkpoint(label.upper().replace(" ", "_"))
        self.adb_controller.tap(*tap_point)
        logging.info("%s confidence: %.2f", label, detection.confidence)
        logging.info("%s random tap point: (%s, %s)", label, *tap_point)

    def _capture_screenshot_after_delay(self) -> None:
        self._wait_with_checkpoints(random.choice(self.screen_transition_poll_seconds_options), "WAIT_SCREEN")
        self._assert_game_ready()
        self.adb_controller.capture_screenshot(CURRENT_SCREENSHOT_PATH)
        self.control.report(screenshotPath=CURRENT_SCREENSHOT_PATH.as_posix())
        logging.info("Screenshot captured")
        logging.info("Screenshot saved to %s", CURRENT_SCREENSHOT_PATH.as_posix())

    def _raise_template_timeout(self, label: str, last_confidence: float) -> None:
        debug_path = self._save_failure_screenshot()
        raise BattleEndControllerError(
            f"{label} was not detected before timeout. "
            f"Last confidence: {last_confidence:.2f}. "
            f"Latest screenshot saved to {debug_path.as_posix()}."
        )

    def _capture_screenshot(self) -> None:
        self._assert_game_ready()
        self.adb_controller.capture_screenshot(CURRENT_SCREENSHOT_PATH)
        self.control.report(screenshotPath=CURRENT_SCREENSHOT_PATH.as_posix())
        logging.info("Screenshot captured")
        logging.info("Screenshot saved to %s", CURRENT_SCREENSHOT_PATH.as_posix())

    def _assert_game_ready(self) -> None:
        self.control.checkpoint()
        foreground_app = self.adb_controller.get_foreground_app()
        if foreground_app != self.package_name:
            raise BattleEndControllerError(
                f"Clash of Clans left the foreground. Current foreground app: {foreground_app or 'unknown'}"
            )

    def _wait_with_checkpoints(self, delay_seconds: float, phase: str) -> None:
        deadline = time.monotonic() + delay_seconds
        while time.monotonic() < deadline:
            self.control.checkpoint(phase)
            time.sleep(min(0.1, deadline - time.monotonic()))

    @staticmethod
    def _save_failure_screenshot() -> Path:
        DEBUG_DIRECTORY.mkdir(parents=True, exist_ok=True)
        output_path = DEBUG_DIRECTORY / "end_battle_failure_latest.png"
        output_path.write_bytes(CURRENT_SCREENSHOT_PATH.read_bytes())
        return output_path
