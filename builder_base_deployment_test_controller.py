from __future__ import annotations

import logging
import random
import time
from pathlib import Path

import cv2

from adb_controller import ADBController
from builder_base_battlefield import builder_deployment_points
from builder_base_end_controller import BuilderBaseEndController
from builder_base_flow_controller import BuilderBaseFlowController, BUILDER_TEMPLATES
from builder_base_slots import builder_army_slots
from decision_engine import BotConfig
from project_paths import CURRENT_SCREENSHOT_PATH, DEBUG_DIRECTORY
from runtime.runtime_control import NULL_RUNTIME_CONTROL, RuntimeControl
from screen_detector import BoundingBox, ScreenState, detect_screen
from tap_utils import TapPointError, select_random_point_in_box


class BuilderBaseDeploymentTestControllerError(Exception):
    """Raised when the bounded Builder Base one-troop test cannot complete safely."""


class BuilderBaseDeploymentTestController:
    """Runs Builder Base navigation, one random troop deployment at a random point, then returns Home."""

    def __init__(self, *, adb_controller: ADBController, bot_config: BotConfig, package_name: str, screen_threshold: float, dry_run: bool, control: RuntimeControl = NULL_RUNTIME_CONTROL) -> None:
        self.adb_controller = adb_controller
        self.bot_config = bot_config
        self.package_name = package_name
        self.screen_threshold = screen_threshold
        self.dry_run = dry_run
        self.control = control
        self.adb_controller.set_gameplay_input_allowed(not dry_run)

    def run(self) -> int:
        flow = BuilderBaseFlowController(
            adb_controller=self.adb_controller,
            bot_config=self.bot_config,
            package_name=self.package_name,
            screen_threshold=self.screen_threshold,
            dry_run=self.dry_run,
            control=self.control,
        )
        flow.navigate_to_enemy_base()
        if self.dry_run:
            logging.info("Dry-run stops before selecting a Builder Base troop.")
            return 0

        screenshot_size = self._verify_enemy_base()
        available_troops = builder_army_slots(screenshot_size)[: self.bot_config.builder_troop_slot_count]
        troop = random.choice(available_troops)
        troop_tap = self._tap_box(
            BoundingBox(troop.left, troop.top, troop.right - troop.left, troop.bottom - troop.top),
            screenshot_size,
            f"TROOP {troop.index}",
        )
        self._wait_with_checkpoints(random.choice(self.bot_config.troop_selection_delay_seconds_options))

        self._verify_enemy_base()
        deployment_points = builder_deployment_points(screenshot_size, self.bot_config)
        point = random.choice(deployment_points)
        debug_path = self._save_deployment_taps_debug(troop.index, troop_tap, point.sequence_number, (point.x, point.y))
        self.control.checkpoint(f"BUILDER_DEPLOY_POINT_{point.sequence_number}")
        self.adb_controller.tap(point.x, point.y)
        self.control.report(
            attackPlan={"strategy": "builder_base_test", "plannedActionCount": 1, "deploymentPointCount": len(deployment_points), "availableTroopSlots": self.bot_config.builder_troop_slot_count, "selectedTroopSlot": troop.index, "selectedPoint": point.sequence_number},
            debugArtifactPaths=[debug_path.as_posix()] if debug_path else [],
        )
        logging.info("TROOP %s deployed once at Builder Base point %s: (%s, %s)", troop.index, point.sequence_number, point.x, point.y)

        wait_seconds = random.choice(self.bot_config.builder_post_deployment_wait_seconds_options)
        logging.info("Waiting %.1f seconds after Builder Base deployment", wait_seconds)
        self._wait_with_checkpoints(wait_seconds)
        return BuilderBaseEndController(
            adb_controller=self.adb_controller,
            package_name=self.package_name,
            screen_threshold=self.screen_threshold,
            poll_seconds_options=self.bot_config.screen_transition_poll_seconds_options,
            dry_run=False,
            control=self.control,
        ).run()

    def _verify_enemy_base(self) -> tuple[int, int]:
        self._assert_game_ready()
        self.adb_controller.capture_screenshot(CURRENT_SCREENSHOT_PATH)
        detection = detect_screen(CURRENT_SCREENSHOT_PATH, threshold=self.screen_threshold, debug_directory=DEBUG_DIRECTORY, templates=BUILDER_TEMPLATES)
        if detection.state is not ScreenState.BUILDER_ENEMY_BASE:
            raise BuilderBaseDeploymentTestControllerError(
                f"Expected BUILDER_ENEMY_BASE before deployment; detected {detection.state.value}."
            )
        return detection.screenshot_size

    def _tap_box(self, box: BoundingBox, screenshot_size: tuple[int, int], label: str) -> tuple[int, int]:
        try:
            point = select_random_point_in_box(box, screenshot_size)
        except TapPointError as error:
            raise BuilderBaseDeploymentTestControllerError(str(error)) from error
        self.control.checkpoint(label.replace(" ", "_"))
        self.adb_controller.tap(*point)
        logging.info("%s selected at (%s, %s)", label, *point)
        return point

    @staticmethod
    def _save_deployment_taps_debug(
        troop_index: int,
        troop_tap: tuple[int, int],
        point_sequence: int,
        deployment_tap: tuple[int, int],
    ) -> Path | None:
        """Save the exact random input coordinates on the current Builder Base screenshot."""
        image = cv2.imread(str(CURRENT_SCREENSHOT_PATH), cv2.IMREAD_COLOR)
        if image is None:
            logging.warning("Could not save Builder Base deployment tap debug image: screenshot is unreadable.")
            return None

        for coordinate, color, label in (
            (troop_tap, (0, 215, 255), f"TROOP {troop_index} TAP"),
            (deployment_tap, (0, 0, 255), f"POINT {point_sequence} DEPLOY"),
        ):
            cv2.drawMarker(image, coordinate, color, markerType=cv2.MARKER_CROSS, markerSize=30, thickness=3)
            cv2.circle(image, coordinate, 16, color, 2)
            cv2.putText(image, label, (coordinate[0] + 18, coordinate[1] - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)

        output_path = DEBUG_DIRECTORY / "builder_base_deployment_taps.png"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(output_path), image):
            logging.warning("Could not write Builder Base deployment tap debug image: %s", output_path)
            return None
        logging.info("Builder Base deployment tap debug image saved to %s", output_path)
        return output_path

    def _assert_game_ready(self) -> None:
        self.control.checkpoint()
        if self.adb_controller.get_foreground_app() != self.package_name:
            raise BuilderBaseDeploymentTestControllerError("Clash of Clans is not in the foreground.")

    def _wait_with_checkpoints(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.control.checkpoint()
            time.sleep(min(0.1, deadline - time.monotonic()))
