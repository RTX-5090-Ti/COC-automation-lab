from __future__ import annotations

import logging
import random
import time
from pathlib import Path

from adb_controller import ADBController
from builder_base_flow_controller import BUILDER_TEMPLATES
from project_paths import CURRENT_SCREENSHOT_PATH, DEBUG_DIRECTORY, asset_path
from runtime.runtime_control import NULL_RUNTIME_CONTROL, RuntimeControl
from screen_detector import ScreenState, TemplateDetectionResult, detect_screen, detect_template
from tap_utils import TapPointError, select_random_point_in_box


END_BATTLE_BUTTON_PATH = asset_path("templates", "builder_base", "builder_end_battle_button.png")
END_BATTLE_CONFIRM_OK_PATH = asset_path("templates", "builder_base", "builder_end_battle_confirm_ok.png")
RETURN_HOME_BUTTON_PATH = asset_path("templates", "builder_base", "builder_return_home_button.png")


def required_template_paths() -> tuple[Path, ...]:
    """All templates needed to enter, deploy in, and leave Builder Base."""
    return tuple(path for _state, path, _name in BUILDER_TEMPLATES) + (
        END_BATTLE_BUTTON_PATH, END_BATTLE_CONFIRM_OK_PATH, RETURN_HOME_BUTTON_PATH,
    )


class BuilderBaseEndControllerError(Exception):
    """Raised when Builder Base cannot be ended and verified safely."""


class BuilderBaseEndController:
    """Runs one verified Builder Base enemy-base to Home Village transition."""

    def __init__(self, *, adb_controller: ADBController, package_name: str, screen_threshold: float, poll_seconds_options: tuple[float, ...], dry_run: bool, control: RuntimeControl = NULL_RUNTIME_CONTROL) -> None:
        self.adb_controller = adb_controller
        self.package_name = package_name
        self.screen_threshold = screen_threshold
        self.poll_seconds_options = poll_seconds_options
        self.dry_run = dry_run
        self.control = control
        self.adb_controller.set_gameplay_input_allowed(not dry_run)

    def run(self) -> int:
        initial = self._capture_template(END_BATTLE_BUTTON_PATH)
        if not initial.found:
            raise BuilderBaseEndControllerError(
                f"Builder end test expects the Surrender button; confidence was {initial.confidence:.2f}."
            )
        self._tap_result(initial, "Surrender button")
        if self.dry_run:
            logging.info("Dry-run stops before changing the screen")
            return 0
        self._wait_and_tap_template(END_BATTLE_CONFIRM_OK_PATH, "Surrender confirmation OK")
        self._wait_and_tap_template(RETURN_HOME_BUTTON_PATH, "Return Home button")
        self._wait_for_builder_home()
        self.control.report(gameScreen=ScreenState.BUILDER_HOME.value, phase="BUILDER_RETURNED_HOME", decision="NO_ACTION")
        logging.info("Builder Base transition successful: ENEMY_BASE -> BUILDER_HOME")
        return 0

    def _wait_and_tap_template(self, template_path, label: str) -> None:
        deadline = time.monotonic() + 15.0
        last_confidence = 0.0
        while time.monotonic() < deadline:
            self.control.checkpoint(f"WAIT_{label.upper().replace(' ', '_')}")
            self._wait_with_checkpoints(random.choice(self.poll_seconds_options))
            result = self._capture_template(template_path)
            last_confidence = result.confidence
            if result.found:
                self._tap_result(result, label)
                return
        raise BuilderBaseEndControllerError(f"Timed out waiting for {label}; last confidence was {last_confidence:.2f}.")

    def _wait_for_builder_home(self) -> None:
        deadline = time.monotonic() + 15.0
        last_state = ScreenState.UNKNOWN
        while time.monotonic() < deadline:
            self.control.checkpoint("WAIT_BUILDER_HOME")
            self._wait_with_checkpoints(random.choice(self.poll_seconds_options))
            self._assert_game_ready()
            self.adb_controller.capture_screenshot(CURRENT_SCREENSHOT_PATH)
            result = detect_screen(
                CURRENT_SCREENSHOT_PATH,
                threshold=self.screen_threshold,
                debug_directory=DEBUG_DIRECTORY,
                templates=BUILDER_TEMPLATES,
            )
            last_state = result.state
            if result.state is ScreenState.BUILDER_HOME:
                return
        raise BuilderBaseEndControllerError(
            f"Timed out waiting for BUILDER_HOME; last detected state was {last_state.value}."
        )

    def _tap_template(self, template_path, label: str) -> None:
        result = self._capture_template(template_path)
        if not result.found:
            raise BuilderBaseEndControllerError(f"{label} was not detected; confidence was {result.confidence:.2f}.")
        self._tap_result(result, label)

    def _tap_result(self, result: TemplateDetectionResult, label: str) -> None:
        try:
            point = select_random_point_in_box(result.bounding_box, result.screenshot_size)
        except TapPointError as error:
            raise BuilderBaseEndControllerError(str(error)) from error
        logging.info("%s confidence: %.2f", label, result.confidence)
        if self.dry_run:
            logging.info("Dry-run: would tap %s at (%s, %s)", label, *point)
            return
        self._assert_game_ready()
        self.control.checkpoint(label.upper().replace(" ", "_"))
        self.adb_controller.tap(*point)
        logging.info("%s tapped once at (%s, %s)", label, *point)

    def _capture_template(self, template_path) -> TemplateDetectionResult:
        self._assert_game_ready()
        self.adb_controller.capture_screenshot(CURRENT_SCREENSHOT_PATH)
        return detect_template(CURRENT_SCREENSHOT_PATH, template_path, threshold=self.screen_threshold)

    def _assert_game_ready(self) -> None:
        self.control.checkpoint()
        if self.adb_controller.get_foreground_app() != self.package_name:
            raise BuilderBaseEndControllerError("Clash of Clans is not in the foreground.")

    def _wait_with_checkpoints(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.control.checkpoint()
            time.sleep(min(0.1, deadline - time.monotonic()))
