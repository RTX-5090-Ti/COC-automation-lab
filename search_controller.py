from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from adb_controller import ADBController, ADBError
from battlefield_fingerprint import (
    BattlefieldFingerprint,
    build_battlefield_fingerprint,
    compare_fingerprints,
)
from decision_engine import BotConfig, Decision, DecisionResult, evaluate_resources
from resource_reader import ResourceReadResult, ResourceReader
from screen_detector import ScreenDetectionResult, ScreenState, detect_screen
from strategies.attack_plan import save_attack_plan_debug_image
from strategies.sneaky_goblin import SneakyGoblinPlanner, SneakyGoblinPlanningError


CURRENT_SCREENSHOT_PATH = Path("screenshots/current/current.png")
DEBUG_DIRECTORY = Path("screenshots/debug")
DEFAULT_BATTLEFIELD_DIFF_THRESHOLD = 0.05
ATTACK_PLAN_DEBUG_PATH = DEBUG_DIRECTORY / "attack_plan_sneaky_goblin.png"


class SearchControllerError(Exception):
    """Raised when the bounded enemy-base search must stop safely."""


@dataclass
class SearchCounters:
    bases_checked: int = 1
    next_taps: int = 0
    unreadable_bases: int = 0
    ocr_attempts_for_current_base: int = 0
    unknown_state_retries: int = 0


class SearchController:
    """Runs one bounded enemy-base search session."""

    def __init__(
        self,
        *,
        adb_controller: ADBController,
        resource_reader: ResourceReader,
        bot_config: BotConfig,
        package_name: str,
        screen_threshold: float,
        debug: bool = False,
        live_override: bool = False,
        battlefield_diff_threshold: float = DEFAULT_BATTLEFIELD_DIFF_THRESHOLD,
    ) -> None:
        self.adb_controller = adb_controller
        self.resource_reader = resource_reader
        self.bot_config = bot_config
        self.package_name = package_name
        self.screen_threshold = screen_threshold
        self.debug = debug
        self.dry_run = False if live_override else bot_config.dry_run
        self.battlefield_diff_threshold = battlefield_diff_threshold
        self.sneaky_goblin_planner = SneakyGoblinPlanner()

    def run(self) -> int:
        start_time = time.monotonic()
        counters = SearchCounters()

        self._ensure_runtime_available(start_time)
        detection_result = self._capture_and_detect_screen()
        if detection_result.state is ScreenState.UNKNOWN:
            detection_result = self._retry_unknown_state(start_time=start_time, counters=counters)
        self._log_detection_result(detection_result)

        if detection_result.state is not ScreenState.ENEMY_BASE:
            logging.info("This milestone expects ENEMY_BASE. Current screen is %s.", detection_result.state.value)
            logging.info("No gameplay action was performed")
            return 0

        while True:
            self._ensure_runtime_available(start_time)
            self._log_progress(counters)
            result = self._analyze_current_base(
                start_time=start_time,
                counters=counters,
            )

            if result.decision_result.decision is Decision.ATTACK:
                return self._handle_attack_found(start_time=start_time, counters=counters)

            if result.decision_result.decision is Decision.UNDECIDED:
                counters.unreadable_bases += 1
                logging.warning(
                    "Resource reading remained UNDECIDED after %s attempts",
                    counters.ocr_attempts_for_current_base,
                )
                for resource_name in self._unavailable_resources(result.decision_result):
                    logging.error("%s reading is unavailable", resource_name)
                logging.info("Skipping unreadable base")
            else:
                logging.info("Decision: SKIP")

            if counters.bases_checked >= self.bot_config.max_bases_to_check:
                logging.info(
                    "Reached base-check limit: %s / %s",
                    counters.bases_checked,
                    self.bot_config.max_bases_to_check,
                )
                self._log_counter_summary(counters)
                logging.info("No gameplay action was performed")
                return 0

            if counters.next_taps >= self.bot_config.max_next_taps:
                self._stop_with_debug(
                    "Reached Next-tap limit before another transition could begin.",
                    counters,
                    last_state=ScreenState.ENEMY_BASE,
                )

            fresh_detection = self._confirm_enemy_base_ready_for_next(start_time, counters)
            next_x, next_y = self._validate_next_button(fresh_detection)

            if self.dry_run:
                if result.decision_result.decision is Decision.UNDECIDED:
                    logging.info("Dry-run: would skip this unreadable base using Next")
                else:
                    logging.info("Dry-run: would tap Next at (%s, %s)", next_x, next_y)
                logging.info("Search loop paused because dry-run is enabled")
                logging.info("No gameplay action was performed")
                return 0

            old_fingerprint = build_battlefield_fingerprint(CURRENT_SCREENSHOT_PATH)
            self.adb_controller.tap(next_x, next_y)
            counters.next_taps += 1
            logging.info("Next button tapped once")

            new_detection = self._wait_for_new_enemy_base(
                start_time=start_time,
                counters=counters,
                old_fingerprint=old_fingerprint,
            )
            counters.bases_checked += 1
            counters.ocr_attempts_for_current_base = 0
            counters.unknown_state_retries = 0
            logging.info("Transition successful: ENEMY_BASE -> NEW_ENEMY_BASE")
            logging.debug("New base confirmed via %s", new_detection.matched_template_name)

    def _analyze_current_base(
        self,
        *,
        start_time: float,
        counters: SearchCounters,
    ) -> _BaseAnalysis:
        last_resource_result: ResourceReadResult | None = None
        last_decision_result: DecisionResult | None = None

        for attempt in range(1, self.bot_config.max_ocr_attempts_per_base + 1):
            self._ensure_runtime_available(start_time)
            counters.ocr_attempts_for_current_base = attempt
            detection_result = self._confirm_enemy_base_for_analysis(start_time, counters)

            resource_result = self.resource_reader.read_resources(
                CURRENT_SCREENSHOT_PATH,
                threshold=self.screen_threshold,
            )
            decision_result = evaluate_resources(resource_result, self.bot_config)
            last_resource_result = resource_result
            last_decision_result = decision_result

            self._log_resource_result(resource_result)
            self._log_decision_result(decision_result)

            if decision_result.decision is not Decision.UNDECIDED:
                return _BaseAnalysis(
                    detection_result=detection_result,
                    resource_result=resource_result,
                    decision_result=decision_result,
                )

            if attempt < self.bot_config.max_ocr_attempts_per_base:
                logging.warning(
                    "Decision remained UNDECIDED on OCR attempt %s / %s. Retrying the same base.",
                    attempt,
                    self.bot_config.max_ocr_attempts_per_base,
                )

        if last_resource_result is None or last_decision_result is None:
            raise SearchControllerError("OCR analysis could not start for the current base.")

        return _BaseAnalysis(
            detection_result=detection_result,
            resource_result=last_resource_result,
            decision_result=last_decision_result,
        )

    def _confirm_enemy_base_for_analysis(
        self,
        start_time: float,
        counters: SearchCounters,
    ) -> ScreenDetectionResult:
        detection_result = self._capture_and_detect_screen()
        if detection_result.state is ScreenState.ENEMY_BASE:
            counters.unknown_state_retries = 0
            return detection_result

        if detection_result.state is not ScreenState.UNKNOWN:
            self._stop_with_debug(
                f"Expected ENEMY_BASE while analyzing resources, but detected {detection_result.state.value}.",
                counters,
                last_state=detection_result.state,
            )

        return self._retry_unknown_state(start_time=start_time, counters=counters)

    def _confirm_enemy_base_ready_for_next(
        self,
        start_time: float,
        counters: SearchCounters,
    ) -> ScreenDetectionResult:
        detection_result = self._capture_and_detect_screen()
        if detection_result.state is ScreenState.ENEMY_BASE:
            counters.unknown_state_retries = 0
            return detection_result

        if detection_result.state is not ScreenState.UNKNOWN:
            self._stop_with_debug(
                f"Expected ENEMY_BASE before tapping Next, but detected {detection_result.state.value}.",
                counters,
                last_state=detection_result.state,
            )

        return self._retry_unknown_state(start_time=start_time, counters=counters)

    def _retry_unknown_state(
        self,
        *,
        start_time: float,
        counters: SearchCounters,
    ) -> ScreenDetectionResult:
        for attempt in range(1, self.bot_config.max_unknown_state_retries + 1):
            self._ensure_runtime_available(start_time)
            counters.unknown_state_retries = attempt
            logging.warning(
                "Detected screen: UNKNOWN. Retrying %s / %s after %.1fs.",
                attempt,
                self.bot_config.max_unknown_state_retries,
                self.bot_config.unknown_retry_delay_seconds,
            )
            time.sleep(self.bot_config.unknown_retry_delay_seconds)
            detection_result = self._capture_and_detect_screen()

            if detection_result.state is ScreenState.ENEMY_BASE:
                counters.unknown_state_retries = 0
                return detection_result

            if detection_result.state is not ScreenState.UNKNOWN:
                self._stop_with_debug(
                    f"Expected ENEMY_BASE during UNKNOWN recovery, but detected {detection_result.state.value}.",
                    counters,
                    last_state=detection_result.state,
                )

        self._stop_with_debug(
            f"Screen remained UNKNOWN after {self.bot_config.max_unknown_state_retries} retries.",
            counters,
            last_state=ScreenState.UNKNOWN,
        )

    def _wait_for_new_enemy_base(
        self,
        *,
        start_time: float,
        counters: SearchCounters,
        old_fingerprint: BattlefieldFingerprint,
    ) -> ScreenDetectionResult:
        transition_start = time.monotonic()
        last_state = ScreenState.UNKNOWN

        while time.monotonic() - transition_start < self.bot_config.new_base_timeout_seconds:
            self._ensure_runtime_available(start_time)
            time.sleep(1.0)
            self._assert_game_ready()

            detection_result = self._capture_and_detect_screen()
            last_state = detection_result.state

            if detection_result.state is ScreenState.UNKNOWN:
                counters.unknown_state_retries += 1
                if counters.unknown_state_retries >= self.bot_config.max_unknown_state_retries:
                    self._stop_with_debug(
                        f"Screen remained UNKNOWN after {self.bot_config.max_unknown_state_retries} retries.",
                        counters,
                        last_state=ScreenState.UNKNOWN,
                    )
                continue

            counters.unknown_state_retries = 0
            if detection_result.state is not ScreenState.ENEMY_BASE:
                continue

            new_fingerprint = build_battlefield_fingerprint(CURRENT_SCREENSHOT_PATH)
            difference_score = compare_fingerprints(old_fingerprint, new_fingerprint)
            logging.debug("Battlefield difference score: %.4f", difference_score)
            if difference_score >= self.battlefield_diff_threshold:
                return detection_result

        self._stop_with_debug(
            "A new enemy base could not be confirmed before timeout.",
            counters,
            last_state=last_state,
        )

    def _handle_attack_found(self, *, start_time: float, counters: SearchCounters) -> int:
        self._ensure_runtime_available(start_time)
        self._confirm_enemy_base_for_analysis(start_time, counters)
        try:
            planning_result = self.sneaky_goblin_planner.plan_attack(
                screenshot_path=CURRENT_SCREENSHOT_PATH,
                config=self.bot_config,
            )
        except SneakyGoblinPlanningError as error:
            debug_path = self._save_timestamped_debug_copy(CURRENT_SCREENSHOT_PATH, "attack_plan_failure")
            logging.error(str(error))
            logging.error("Saved latest screenshot to %s", debug_path.as_posix())
            logging.info("No troop was deployed")
            return 1
        troop_slot = planning_result.troop_slot_result

        logging.info("Suitable base found")
        logging.info("Decision: ATTACK")
        logging.info("Bases checked: %s / %s", counters.bases_checked, self.bot_config.max_bases_to_check)
        logging.info("Sneaky Goblin slot confidence: %.2f", troop_slot.confidence)
        if troop_slot.center is not None:
            logging.info("Sneaky Goblin slot center: (%s, %s)", troop_slot.center[0], troop_slot.center[1])

        if not planning_result.attack_plan.valid:
            debug_path = self._save_timestamped_debug_copy(CURRENT_SCREENSHOT_PATH, "attack_plan_failure")
            logging.error(planning_result.attack_plan.error_message or "Attack plan is invalid.")
            logging.error("Saved latest screenshot to %s", debug_path.as_posix())
            logging.info("No troop was deployed")
            return 1

        debug_path = save_attack_plan_debug_image(
            screenshot_path=CURRENT_SCREENSHOT_PATH,
            output_path=ATTACK_PLAN_DEBUG_PATH,
            battlefield_roi=planning_result.battlefield_roi,
            excluded_regions=planning_result.excluded_regions,
            troop_slot_box=troop_slot.bounding_box,
            attack_plan=planning_result.attack_plan,
        )
        total_goblins = sum(action.amount for action in planning_result.attack_plan.actions)
        logging.info("Strategy selected: %s", planning_result.attack_plan.strategy_name)
        logging.info("Planned deployment groups: %s", len(planning_result.attack_plan.actions))
        logging.info("Goblins per group: %s", self.bot_config.goblins_per_point)
        logging.info("Total planned Goblins: %s", total_goblins)
        logging.info("Attack plan debug image saved to %s", debug_path.as_posix())
        if self.dry_run:
            logging.info("Attack plan generated in dry-run mode")
        else:
            logging.info("Attack plan execution is not implemented yet")
        logging.info("No troop was deployed")
        return 0

    def _capture_and_detect_screen(self) -> ScreenDetectionResult:
        self._assert_game_ready()
        screenshot_path = self.adb_controller.capture_screenshot(CURRENT_SCREENSHOT_PATH)
        logging.info("Screenshot captured")
        logging.info("Screenshot saved to %s", screenshot_path.as_posix())
        return detect_screen(
            screenshot_path,
            threshold=self.screen_threshold,
            debug_directory=DEBUG_DIRECTORY,
        )

    def _validate_next_button(self, detection_result: ScreenDetectionResult) -> tuple[int, int]:
        if detection_result.confidence < self.screen_threshold:
            raise SearchControllerError(
                f"Next button confidence is below the threshold: {detection_result.confidence:.2f}"
            )

        if detection_result.center is None:
            raise SearchControllerError("Next button coordinates are unavailable.")

        x, y = detection_result.center
        width, height = detection_result.screenshot_size
        if x < 0 or y < 0 or x >= width or y >= height:
            raise SearchControllerError(f"Next button coordinates are outside the screenshot bounds: ({x}, {y})")
        return x, y

    def _assert_game_ready(self) -> None:
        foreground_app = self.adb_controller.get_foreground_app()
        if foreground_app != self.package_name:
            raise SearchControllerError(
                f"Clash of Clans left the foreground. Current foreground app: {foreground_app or 'unknown'}"
            )

    def _ensure_runtime_available(self, start_time: float) -> None:
        elapsed = time.monotonic() - start_time
        if elapsed > self.bot_config.max_runtime_seconds:
            raise SearchControllerError(
                f"Reached max runtime limit of {self.bot_config.max_runtime_seconds:.1f} seconds."
            )

    def _stop_with_debug(
        self,
        message: str,
        counters: SearchCounters,
        *,
        last_state: ScreenState,
    ) -> None:
        debug_path = self._save_timestamped_debug_copy(CURRENT_SCREENSHOT_PATH, "search_stop")
        logging.error(message)
        logging.error("Last detected screen: %s", last_state.value)
        logging.error("Saved latest screenshot to %s", debug_path.as_posix())
        self._log_counter_summary(counters)
        raise SearchControllerError(message)

    def _save_timestamped_debug_copy(self, source_path: Path, prefix: str) -> Path:
        DEBUG_DIRECTORY.mkdir(parents=True, exist_ok=True)
        output_path = DEBUG_DIRECTORY / f"{prefix}_latest.png"
        output_path.write_bytes(source_path.read_bytes())
        return output_path

    @staticmethod
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

    @staticmethod
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

    @staticmethod
    def _log_decision_result(decision_result: DecisionResult) -> None:
        SearchController._log_single_decision_line(
            "Gold",
            decision_result.detected_gold,
            decision_result.minimum_gold,
            decision_result.gold_passed,
        )
        SearchController._log_single_decision_line(
            "Elixir",
            decision_result.detected_elixir,
            decision_result.minimum_elixir,
            decision_result.elixir_passed,
        )
        SearchController._log_single_decision_line(
            "Dark Elixir",
            decision_result.detected_dark_elixir,
            decision_result.minimum_dark_elixir,
            decision_result.dark_elixir_passed,
        )

        if decision_result.decision is Decision.UNDECIDED:
            logging.warning("Decision: UNDECIDED")
        else:
            logging.info("Decision: %s", decision_result.decision.value)

        for reason in decision_result.reasons:
            if decision_result.decision is Decision.UNDECIDED and "unavailable" in reason.lower():
                logging.error(reason)
            else:
                logging.info("Reason: %s", reason)

    @staticmethod
    def _log_single_decision_line(label: str, detected_value: int | None, minimum_value: int, passed: bool | None) -> None:
        if detected_value is None:
            logging.error("%s: unavailable / required %s -> FAIL", label, minimum_value)
            return

        status = "PASS" if passed else "FAIL"
        logging.info("%s: %s / required %s -> %s", label, detected_value, minimum_value, status)

    @staticmethod
    def _unavailable_resources(decision_result: DecisionResult) -> list[str]:
        resources: list[str] = []
        if decision_result.detected_gold is None:
            resources.append("Gold")
        if decision_result.detected_elixir is None:
            resources.append("Elixir")
        if decision_result.detected_dark_elixir is None:
            resources.append("Dark Elixir")
        return resources

    def _log_progress(self, counters: SearchCounters) -> None:
        logging.info("Checking base %s / %s", counters.bases_checked, self.bot_config.max_bases_to_check)
        logging.info("Next taps used: %s / %s", counters.next_taps, self.bot_config.max_next_taps)
        logging.info("Unreadable bases: %s", counters.unreadable_bases)

    def _log_counter_summary(self, counters: SearchCounters) -> None:
        logging.info("Bases checked: %s / %s", counters.bases_checked, self.bot_config.max_bases_to_check)
        logging.info("Next taps used: %s / %s", counters.next_taps, self.bot_config.max_next_taps)
        logging.info("Unreadable bases: %s", counters.unreadable_bases)


@dataclass(frozen=True)
class _BaseAnalysis:
    detection_result: ScreenDetectionResult
    resource_result: ResourceReadResult
    decision_result: DecisionResult
