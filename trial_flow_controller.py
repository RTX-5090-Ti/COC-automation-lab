from __future__ import annotations

import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from adb_controller import ADBController
from battle_end_controller import BattleEndController
from battlefield_fingerprint import build_battlefield_fingerprint, compare_fingerprints
from decision_engine import BotConfig, Decision, DecisionResult, evaluate_resources
from resource_reader import ResourceReadResult, ResourceReader
from project_paths import CURRENT_SCREENSHOT_PATH, DEBUG_DIRECTORY, asset_path
from runtime.runtime_control import NULL_RUNTIME_CONTROL, RuntimeControl
from runtime.reliability_guard import ReliabilityGuard
from screen_detector import ScreenDetectionResult, ScreenState, detect_screen, detect_template
from strategies.attack_plan import save_attack_plan_debug_image
from strategies.sneaky_goblin import SneakyGoblinPlanner, SneakyGoblinPlanningError
from tap_utils import TapPointError, select_random_point_in_box
from troop_count_reader import TroopCountReader


STATE_TRANSITION_TIMEOUT_SECONDS = 15.0
GOBLINS_PER_TEST_POINT = 2
POST_DEPLOYMENT_WAIT_SECONDS = 5.0
ATTACK_PLAN_DEBUG_PATH = DEBUG_DIRECTORY / "attack_plan_sneaky_goblin.png"
SUPER_WALL_BREAKER_TEMPLATE_PATH = asset_path("templates", "battle", "super_wall_breaker_slot.png")


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
        deployment_point_test_indices: tuple[int, ...] = (),
        super_wall_breaker_test_point_1: bool = False,
        setup_1_test: bool = False,
        setup_2_test: bool = False,
        setup_3_test: bool = False,
        setup_4_test: bool = False,
        setup_5_test: bool = False,
        setup_6_test: bool = False,
        setup_7_test: bool = False,
        setup_8_test: bool = False,
        random_setup_test: bool = False,
        setup_history: list[str] | None = None,
        control: RuntimeControl = NULL_RUNTIME_CONTROL,
    ) -> None:
        self.adb_controller = adb_controller
        self.resource_reader = resource_reader
        self.bot_config = bot_config
        self.package_name = package_name
        self.screen_threshold = screen_threshold
        self.battlefield_diff_threshold = battlefield_diff_threshold
        self.dry_run = dry_run
        self.adb_controller.set_gameplay_input_allowed(not self.dry_run)
        self.two_point_deployment_test = two_point_deployment_test
        self.deployment_point_test_indices = deployment_point_test_indices
        self.super_wall_breaker_test_point_1 = super_wall_breaker_test_point_1
        self.setup_1_test = setup_1_test
        self.setup_2_test = setup_2_test
        self.setup_3_test = setup_3_test
        self.setup_4_test = setup_4_test
        self.setup_5_test = setup_5_test
        self.setup_6_test = setup_6_test
        self.setup_7_test = setup_7_test
        self.setup_8_test = setup_8_test
        self.random_setup_test = random_setup_test
        self.setup_history = setup_history if setup_history is not None else []
        self.selected_setup: str | None = None
        self.control = control
        self.reliability_guard = ReliabilityGuard(
            self.adb_controller, self.package_name, self.screen_threshold,
            self.bot_config.max_unknown_state_retries, self.control,
        )

    def run(self) -> int:
        self.control.checkpoint("HOME_CHECK")
        self.control.log(logging.INFO, "Full-flow controller started")
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
        enemy_base = self._wait_for_enemy_base_to_settle()
        bases_checked = 1
        next_taps = 0
        self.control.report(basesChecked=bases_checked, maxBases=self.bot_config.max_bases_to_check)
        while True:
            self.control.checkpoint("RESOURCE_SEARCH")
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
            self.control.report(basesChecked=bases_checked, maxBases=self.bot_config.max_bases_to_check)
            enemy_base = self._wait_for_different_enemy_base(old_fingerprint)
            enemy_base = self._wait_for_enemy_base_to_settle()

        if self.deployment_point_test_indices:
            return self._deploy_points_then_end_battle(self.deployment_point_test_indices)
        if self.super_wall_breaker_test_point_1:
            return self._deploy_one_super_wall_breaker_then_end_battle()
        if self.setup_1_test:
            return self._deploy_setup_1_then_end_battle()
        if self.setup_2_test:
            return self._deploy_setup_2_then_end_battle()
        if self.setup_3_test:
            return self._deploy_setup_3_then_end_battle()
        if self.setup_4_test:
            return self._deploy_setup_4_then_end_battle()
        if self.setup_5_test:
            return self._deploy_setup_5_then_end_battle()
        if self.setup_6_test:
            return self._deploy_setup_6_then_end_battle()
        if self.setup_7_test:
            return self._deploy_setup_7_then_end_battle()
        if self.setup_8_test:
            return self._deploy_setup_8_then_end_battle()
        if self.random_setup_test:
            setup = self._select_random_setup()
            logging.info("Random deployment setup selected: %s", setup)
            self.control.report(attackPlan={"selectedSetup": setup})
            if setup == "setup_1":
                return self._deploy_setup_1_then_end_battle()
            if setup == "setup_2":
                return self._deploy_setup_2_then_end_battle()
            if setup == "setup_3":
                return self._deploy_setup_3_then_end_battle()
            if setup == "setup_4":
                return self._deploy_setup_4_then_end_battle()
            if setup == "setup_5":
                return self._deploy_setup_5_then_end_battle()
            if setup == "setup_6":
                return self._deploy_setup_6_then_end_battle()
            if setup == "setup_7":
                return self._deploy_setup_7_then_end_battle()
            return self._deploy_setup_8_then_end_battle()
        if self.two_point_deployment_test:
            return self._deploy_points_then_end_battle((1, 2))

        logging.info("Suitable base found; ending the battle without deploying troops")
        return BattleEndController(
            adb_controller=self.adb_controller,
            package_name=self.package_name,
            threshold=self.screen_threshold,
            dry_run=False,
            return_home_timeout_seconds=self.bot_config.new_base_timeout_seconds,
            screen_transition_poll_seconds_options=self.bot_config.screen_transition_poll_seconds_options,
            control=self.control,
        ).run()

    def _deploy_points_then_end_battle(self, point_indices: tuple[int, ...]) -> int:
        try:
            planning_result = SneakyGoblinPlanner().plan_attack(
                screenshot_path=CURRENT_SCREENSHOT_PATH,
                config=self.bot_config,
            )
        except SneakyGoblinPlanningError as error:
            raise TrialFlowControllerError(str(error)) from error

        plan = planning_result.attack_plan
        slot = planning_result.troop_slot_result
        actions_by_number = {action.sequence_number: action for action in plan.actions}
        actions = [actions_by_number[index] for index in point_indices if index in actions_by_number]
        if not plan.valid or slot.bounding_box is None or len(actions) != len(point_indices):
            raise TrialFlowControllerError(plan.error_message or "Requested deployment points could not be generated.")

        # OCR runs while the main thread writes the visual plan; neither operation sends input.
        with ThreadPoolExecutor(max_workers=1) as executor:
            troop_count_future = executor.submit(
                TroopCountReader().read, CURRENT_SCREENSHOT_PATH, slot.bounding_box, DEBUG_DIRECTORY
            )
            save_attack_plan_debug_image(
                screenshot_path=CURRENT_SCREENSHOT_PATH,
                output_path=ATTACK_PLAN_DEBUG_PATH,
                battlefield_roi=planning_result.battlefield_roi,
                battlefield_polygon=planning_result.battlefield_polygon,
                excluded_regions=planning_result.excluded_regions,
                troop_slot_box=slot.bounding_box,
                attack_plan=plan,
                debug_boundary_da_end_ratio=self.bot_config.debug_boundary_da_end_ratio,
                debug_boundary_bh_length_ratio=self.bot_config.debug_boundary_bh_length_ratio,
                debug_boundary_bk_length_ratio=self.bot_config.debug_boundary_bk_length_ratio,
            )
            troop_count = troop_count_future.result()
        required_goblins = len(actions) * GOBLINS_PER_TEST_POINT
        self.control.report(
            troopCount=troop_count.value,
            troopCountRawText=troop_count.raw_text,
            debugArtifactPaths=[ATTACK_PLAN_DEBUG_PATH.as_posix(), troop_count.roi_path.as_posix(), troop_count.processed_path.as_posix()],
        )
        if troop_count.value is None:
            return self._return_home_without_deployment(
                "TROOP_COUNT_OCR_FAILED: Sneaky Goblin count could not be read; deployment was not started."
            )
        if troop_count.value < required_goblins:
            return self._return_home_without_deployment(
                f"INSUFFICIENT_TROOPS: need {required_goblins} Sneaky Goblins but OCR detected {troop_count.value}; deployment was not started."
            )
        logging.info("Sneaky Goblin count verified: %s available, %s required", troop_count.value, required_goblins)
        logging.info("Attack plan debug image saved to %s", ATTACK_PLAN_DEBUG_PATH.as_posix())
        self.control.report(
            attackPlan={
                "strategy": plan.strategy_name,
                "plannedActionCount": len(plan.actions),
                "deploymentPointCount": len(actions),
            },
            debugArtifactPaths=[
                ATTACK_PLAN_DEBUG_PATH.as_posix(),
                troop_count.roi_path.as_posix(),
                troop_count.processed_path.as_posix(),
            ],
        )
        logging.info(
            "Deployment-point full-flow test: points %s",
            ", ".join(str(action.sequence_number) for action in actions),
        )
        slot_tap_point = self._select_slot_tap_point(slot.bounding_box, plan)

        self._assert_game_ready()
        self.control.checkpoint("SELECT_TROOPS")
        self.adb_controller.tap(*slot_tap_point)
        logging.info("Sneaky Goblin slot tapped once at (%s, %s)", *slot_tap_point)
        self._wait_after_troop_selection()

        for action in actions:
            self._validate_deployment_point(action.x, action.y, plan.screenshot_width, plan.screenshot_height)
            for tap_index in range(GOBLINS_PER_TEST_POINT):
                self.control.checkpoint("DEPLOY_TROOPS")
                self.adb_controller.tap(action.x, action.y)
                if tap_index < GOBLINS_PER_TEST_POINT - 1:
                    delay_seconds = random.choice(self.bot_config.delay_between_taps_seconds_options)
                    logging.info("Waiting %.1fs before the second Goblin at point %s", delay_seconds, action.sequence_number)
                    self._wait_with_checkpoints(delay_seconds, "DEPLOYMENT_TAP_DELAY")
            logging.info(
                "Deployed %s Sneaky Goblins at point %s (%s, %s)",
                GOBLINS_PER_TEST_POINT,
                action.sequence_number,
                action.x,
                action.y,
            )
            if action is not actions[-1]:
                delay_seconds = random.choice(self.bot_config.delay_between_groups_seconds_options)
                logging.info("Waiting %.1fs before the next deployment point", delay_seconds)
                self._wait_with_checkpoints(
                    delay_seconds,
                    "DEPLOYMENT_GROUP_DELAY",
                )

        self._wait_after_deployment()
        return BattleEndController(
            adb_controller=self.adb_controller,
            package_name=self.package_name,
            threshold=self.screen_threshold,
            dry_run=False,
            return_home_timeout_seconds=self.bot_config.new_base_timeout_seconds,
            screen_transition_poll_seconds_options=self.bot_config.screen_transition_poll_seconds_options,
            control=self.control,
        ).run()

    def _deploy_one_super_wall_breaker_then_end_battle(self) -> int:
        planning_result = SneakyGoblinPlanner().plan_attack(
            screenshot_path=CURRENT_SCREENSHOT_PATH, config=self.bot_config
        )
        if not planning_result.attack_plan.valid or not planning_result.attack_plan.actions:
            raise TrialFlowControllerError("Point 1 could not be generated for the Super Wall Breaker test.")
        point = planning_result.attack_plan.actions[0]
        slot = detect_template(CURRENT_SCREENSHOT_PATH, SUPER_WALL_BREAKER_TEMPLATE_PATH, threshold=self.screen_threshold)
        if not slot.found:
            raise TrialFlowControllerError(
                f"Super Wall Breaker slot was not detected reliably (confidence {slot.confidence:.2f})."
            )
        try:
            slot_point = select_random_point_in_box(slot.bounding_box, slot.screenshot_size)
        except TapPointError as error:
            raise TrialFlowControllerError(str(error)) from error
        self._assert_game_ready()
        self.control.checkpoint("SELECT_SUPER_WALL_BREAKER")
        self.adb_controller.tap(*slot_point)
        self._wait_after_troop_selection()
        self._validate_deployment_point(point.x, point.y, planning_result.attack_plan.screenshot_width, planning_result.attack_plan.screenshot_height)
        self.control.checkpoint("DEPLOY_SUPER_WALL_BREAKER")
        self.adb_controller.tap(point.x, point.y)
        logging.info("Deployed exactly one Super Wall Breaker at point 1 (%s, %s)", point.x, point.y)
        self._wait_after_deployment()
        return BattleEndController(
            adb_controller=self.adb_controller,
            package_name=self.package_name,
            threshold=self.screen_threshold,
            dry_run=False,
            return_home_timeout_seconds=self.bot_config.new_base_timeout_seconds,
            screen_transition_poll_seconds_options=self.bot_config.screen_transition_poll_seconds_options,
            control=self.control,
        ).run()

    def _deploy_setup_1_then_end_battle(self) -> int:
        planning = SneakyGoblinPlanner().plan_attack(screenshot_path=CURRENT_SCREENSHOT_PATH, config=self.bot_config)
        plan, goblin_slot = planning.attack_plan, planning.troop_slot_result
        clockwise = tuple(range(1, 11)) + tuple(range(20, 10, -1)) + tuple(range(21, 30)) + tuple(range(30, 36))
        actions_by_number = {action.sequence_number: action for action in plan.actions}
        actions = [actions_by_number[index] for index in clockwise if index in actions_by_number]
        if not plan.valid or goblin_slot.bounding_box is None or len(actions) != 35:
            raise TrialFlowControllerError("Setup 1 requires all 35 clockwise Goblin points and a detected Goblin slot.")
        self._save_attack_plan_debug_image(planning)
        super_slot = detect_template(CURRENT_SCREENSHOT_PATH, SUPER_WALL_BREAKER_TEMPLATE_PATH, threshold=self.screen_threshold)
        if not super_slot.found:
            raise TrialFlowControllerError(f"Setup 1 could not detect the Super Wall Breaker slot ({super_slot.confidence:.2f}).")
        required_goblins = len(actions) * 2
        troop_count = TroopCountReader().read(CURRENT_SCREENSHOT_PATH, goblin_slot.bounding_box, DEBUG_DIRECTORY)
        self.control.report(troopCount=troop_count.value, troopCountRawText=troop_count.raw_text, attackPlan={"setup": "setup_1", "goblinRounds": 2, "goblinsPerRound": len(actions), "superWallBreakers": 4})
        if troop_count.value is None or troop_count.value < required_goblins:
            reason = "TROOP_COUNT_OCR_FAILED" if troop_count.value is None else f"INSUFFICIENT_TROOPS: need {required_goblins}, detected {troop_count.value}"
            return self._return_home_without_deployment(reason)
        self._tap_slot(goblin_slot.bounding_box, plan.screenshot_width, plan.screenshot_height, "SELECT_GOBLINS")
        self._deploy_action_round(actions, "SETUP_1_GOBLIN_ROUND_1")
        self._tap_slot(super_slot.bounding_box, *super_slot.screenshot_size, "SELECT_SUPER_WALL_BREAKERS")
        for edge_name, numbers in (("DA", range(1, 11)), ("AB", range(11, 21)), ("BC", range(21, 30)), ("CD", range(30, 36))):
            action = actions_by_number[random.choice(tuple(numbers))]
            self.adb_controller.tap(action.x, action.y)
            logging.info("Setup 1: deployed one Super Wall Breaker on %s at point %s", edge_name, action.sequence_number)
            self._wait_with_checkpoints(random.choice(self.bot_config.delay_between_groups_seconds_options), "SETUP_1_SUPER_DELAY")
        self._tap_slot(goblin_slot.bounding_box, plan.screenshot_width, plan.screenshot_height, "RESELECT_GOBLINS")
        self._deploy_action_round(actions, "SETUP_1_GOBLIN_ROUND_2")
        self._wait_after_deployment()
        return BattleEndController(adb_controller=self.adb_controller, package_name=self.package_name, threshold=self.screen_threshold, dry_run=False, return_home_timeout_seconds=self.bot_config.new_base_timeout_seconds, screen_transition_poll_seconds_options=self.bot_config.screen_transition_poll_seconds_options, control=self.control).run()

    def _deploy_setup_2_then_end_battle(self) -> int:
        planning = SneakyGoblinPlanner().plan_attack(screenshot_path=CURRENT_SCREENSHOT_PATH, config=self.bot_config)
        plan, goblin_slot = planning.attack_plan, planning.troop_slot_result
        if not plan.valid or goblin_slot.bounding_box is None:
            raise TrialFlowControllerError("Setup 2 requires a valid Goblin plan and detected Goblin slot.")
        actions = {action.sequence_number: action for action in plan.actions}
        edges = (("DA", tuple(range(1, 11))), ("AB", tuple(range(20, 10, -1))), ("BC", tuple(range(21, 30))), ("CD", tuple(range(30, 36))))
        if any(number not in actions for _, numbers in edges for number in numbers):
            raise TrialFlowControllerError("Setup 2 requires all 35 deployment points.")
        self._save_attack_plan_debug_image(planning)
        super_slot = detect_template(CURRENT_SCREENSHOT_PATH, SUPER_WALL_BREAKER_TEMPLATE_PATH, threshold=self.screen_threshold)
        if not super_slot.found:
            raise TrialFlowControllerError(f"Setup 2 could not detect the Super Wall Breaker slot ({super_slot.confidence:.2f}).")
        count = TroopCountReader().read(CURRENT_SCREENSHOT_PATH, goblin_slot.bounding_box, DEBUG_DIRECTORY)
        if count.value is None or count.value < 70:
            reason = "TROOP_COUNT_OCR_FAILED" if count.value is None else f"INSUFFICIENT_TROOPS: need 70, detected {count.value}"
            return self._return_home_without_deployment(reason)
        self.control.report(troopCount=count.value, troopCountRawText=count.raw_text, attackPlan={"setup": "setup_2", "goblinRounds": 2, "superWallBreakers": 4})
        for edge_name, numbers in edges:
            edge_actions = [actions[number] for number in numbers]
            self._tap_slot(goblin_slot.bounding_box, plan.screenshot_width, plan.screenshot_height, f"SETUP_2_SELECT_GOBLINS_{edge_name}")
            self._deploy_action_round(edge_actions, f"SETUP_2_{edge_name}_GOBLIN_ROUND_1")
            super_action = actions[random.choice(numbers)]
            self._tap_slot(super_slot.bounding_box, *super_slot.screenshot_size, f"SETUP_2_SELECT_SUPER_{edge_name}")
            self.adb_controller.tap(super_action.x, super_action.y)
            logging.info("Setup 2: deployed one Super Wall Breaker on %s at point %s", edge_name, super_action.sequence_number)
            self._wait_with_checkpoints(random.choice(self.bot_config.delay_between_groups_seconds_options), "SETUP_2_SUPER_DELAY")
            self._tap_slot(goblin_slot.bounding_box, plan.screenshot_width, plan.screenshot_height, f"SETUP_2_RESELECT_GOBLINS_{edge_name}")
            self._deploy_action_round(edge_actions, f"SETUP_2_{edge_name}_GOBLIN_ROUND_2")
        self._wait_after_deployment()
        return BattleEndController(adb_controller=self.adb_controller, package_name=self.package_name, threshold=self.screen_threshold, dry_run=False, return_home_timeout_seconds=self.bot_config.new_base_timeout_seconds, screen_transition_poll_seconds_options=self.bot_config.screen_transition_poll_seconds_options, control=self.control).run()

    def _deploy_setup_3_then_end_battle(self) -> int:
        planning = SneakyGoblinPlanner().plan_attack(screenshot_path=CURRENT_SCREENSHOT_PATH, config=self.bot_config)
        plan, slot = planning.attack_plan, planning.troop_slot_result
        order = tuple(range(10, 0, -1)) + tuple(range(35, 29, -1)) + tuple(range(29, 20, -1)) + tuple(range(11, 21))
        actions_by_number = {action.sequence_number: action for action in plan.actions}
        actions = [actions_by_number[number] for number in order if number in actions_by_number]
        if not plan.valid or slot.bounding_box is None or len(actions) != 35:
            raise TrialFlowControllerError("Setup 3 requires all 35 deployment points and a detected Goblin slot.")
        self._save_attack_plan_debug_image(planning)
        count = TroopCountReader().read(CURRENT_SCREENSHOT_PATH, slot.bounding_box, DEBUG_DIRECTORY)
        if count.value is None or count.value < 70:
            reason = "TROOP_COUNT_OCR_FAILED" if count.value is None else f"INSUFFICIENT_TROOPS: need 70, detected {count.value}"
            return self._return_home_without_deployment(reason)
        self.control.report(troopCount=count.value, troopCountRawText=count.raw_text, attackPlan={"setup": "setup_3", "direction": "counter_clockwise", "startPoint": 10, "goblinRounds": 2, "superWallBreakers": 0})
        self._tap_slot(slot.bounding_box, plan.screenshot_width, plan.screenshot_height, "SETUP_3_SELECT_GOBLINS")
        self._deploy_action_round(actions, "SETUP_3_GOBLIN_ROUND_1")
        self._deploy_action_round(actions, "SETUP_3_GOBLIN_ROUND_2")
        self._wait_after_deployment()
        return BattleEndController(adb_controller=self.adb_controller, package_name=self.package_name, threshold=self.screen_threshold, dry_run=False, return_home_timeout_seconds=self.bot_config.new_base_timeout_seconds, screen_transition_poll_seconds_options=self.bot_config.screen_transition_poll_seconds_options, control=self.control).run()

    def _deploy_setup_4_then_end_battle(self) -> int:
        planning = SneakyGoblinPlanner().plan_attack(screenshot_path=CURRENT_SCREENSHOT_PATH, config=self.bot_config)
        plan, goblin_slot = planning.attack_plan, planning.troop_slot_result
        actions_by_number = {action.sequence_number: action for action in plan.actions}
        ab_da_order = tuple(range(11, 21)) + tuple(range(10, 0, -1))
        bc_cd_order = tuple(range(21, 30)) + tuple(range(30, 36))
        if (
            not plan.valid
            or goblin_slot.bounding_box is None
            or any(number not in actions_by_number for number in ab_da_order + bc_cd_order)
        ):
            raise TrialFlowControllerError("Setup 4 requires all 35 deployment points and a detected Goblin slot.")
        self._save_attack_plan_debug_image(planning)
        super_slot = detect_template(CURRENT_SCREENSHOT_PATH, SUPER_WALL_BREAKER_TEMPLATE_PATH, threshold=self.screen_threshold)
        if not super_slot.found:
            raise TrialFlowControllerError(f"Setup 4 could not detect the Super Wall Breaker slot ({super_slot.confidence:.2f}).")

        count = TroopCountReader().read(CURRENT_SCREENSHOT_PATH, goblin_slot.bounding_box, DEBUG_DIRECTORY)
        if count.value is None or count.value < 70:
            reason = "TROOP_COUNT_OCR_FAILED" if count.value is None else f"INSUFFICIENT_TROOPS: need 70, detected {count.value}"
            return self._return_home_without_deployment(reason)

        ab_da_actions = [actions_by_number[number] for number in ab_da_order]
        bc_cd_actions = [actions_by_number[number] for number in bc_cd_order]
        self.control.report(
            troopCount=count.value,
            troopCountRawText=count.raw_text,
            attackPlan={
                "setup": "setup_4",
                "abDaGoblinsPerPoint": 2,
                "bcCdGoblinsPerPoint": 1,
                "bcCdGoblinRounds": 2,
                "superWallBreakers": 2,
            },
        )

        self._tap_slot(goblin_slot.bounding_box, plan.screenshot_width, plan.screenshot_height, "SETUP_4_SELECT_GOBLINS_AB_DA")
        self._deploy_action_round(ab_da_actions, "SETUP_4_AB_DA_GOBLINS", taps_per_point=2)
        self._tap_slot(goblin_slot.bounding_box, plan.screenshot_width, plan.screenshot_height, "SETUP_4_SELECT_GOBLINS_BC_CD")
        self._deploy_action_round(bc_cd_actions, "SETUP_4_BC_CD_GOBLIN_ROUND_1")

        self._tap_slot(super_slot.bounding_box, *super_slot.screenshot_size, "SETUP_4_SELECT_SUPER_WALL_BREAKERS")
        for edge_name, point_numbers in (("BC", tuple(range(21, 30))), ("CD", tuple(range(30, 36)))):
            action = actions_by_number[random.choice(point_numbers)]
            self.control.checkpoint(f"SETUP_4_DEPLOY_SUPER_{edge_name}")
            self.adb_controller.tap(action.x, action.y)
            logging.info("Setup 4: deployed one Super Wall Breaker on %s at point %s", edge_name, action.sequence_number)
            self._wait_with_checkpoints(random.choice(self.bot_config.delay_between_groups_seconds_options), "SETUP_4_SUPER_DELAY")

        self._tap_slot(goblin_slot.bounding_box, plan.screenshot_width, plan.screenshot_height, "SETUP_4_RESELECT_GOBLINS_BC_CD")
        self._deploy_action_round(bc_cd_actions, "SETUP_4_BC_CD_GOBLIN_ROUND_2")
        self._wait_after_deployment()
        return BattleEndController(adb_controller=self.adb_controller, package_name=self.package_name, threshold=self.screen_threshold, dry_run=False, return_home_timeout_seconds=self.bot_config.new_base_timeout_seconds, screen_transition_poll_seconds_options=self.bot_config.screen_transition_poll_seconds_options, control=self.control).run()

    def _deploy_setup_5_then_end_battle(self) -> int:
        planning = SneakyGoblinPlanner().plan_attack(screenshot_path=CURRENT_SCREENSHOT_PATH, config=self.bot_config)
        plan, goblin_slot = planning.attack_plan, planning.troop_slot_result
        actions_by_number = {action.sequence_number: action for action in plan.actions}
        edges = (
            ("DA", tuple(range(1, 11))),
            ("AB", tuple(range(11, 21))),
            ("BC", tuple(range(21, 30))),
            ("CD", tuple(range(30, 36))),
        )
        if (
            not plan.valid
            or goblin_slot.bounding_box is None
            or any(number not in actions_by_number for _, numbers in edges for number in numbers)
        ):
            raise TrialFlowControllerError("Setup 5 requires all 35 deployment points and a detected Goblin slot.")
        self._save_attack_plan_debug_image(planning)
        super_slot = detect_template(CURRENT_SCREENSHOT_PATH, SUPER_WALL_BREAKER_TEMPLATE_PATH, threshold=self.screen_threshold)
        if not super_slot.found:
            raise TrialFlowControllerError(f"Setup 5 could not detect the Super Wall Breaker slot ({super_slot.confidence:.2f}).")

        count = TroopCountReader().read(CURRENT_SCREENSHOT_PATH, goblin_slot.bounding_box, DEBUG_DIRECTORY)
        required_goblins = 48
        if count.value is None or count.value < required_goblins:
            reason = "TROOP_COUNT_OCR_FAILED" if count.value is None else f"INSUFFICIENT_TROOPS: need {required_goblins}, detected {count.value}"
            return self._return_home_without_deployment(reason)

        selected_numbers_by_edge = {
            edge_name: tuple(sorted(random.sample(point_numbers, k=min(6, len(point_numbers)))))
            for edge_name, point_numbers in edges
        }
        self.control.report(
            troopCount=count.value,
            troopCountRawText=count.raw_text,
            attackPlan={
                "setup": "setup_5",
                "superWallBreakers": 4,
                "goblinsPerPoint": 2,
                "selectedPointsByEdge": selected_numbers_by_edge,
                "plannedGoblinCount": required_goblins,
            },
        )

        self._tap_slot(super_slot.bounding_box, *super_slot.screenshot_size, "SETUP_5_SELECT_SUPER_WALL_BREAKERS")
        for edge_name, point_numbers in edges:
            action = actions_by_number[random.choice(point_numbers)]
            self.control.checkpoint(f"SETUP_5_DEPLOY_SUPER_{edge_name}")
            self.adb_controller.tap(action.x, action.y)
            logging.info("Setup 5: deployed one Super Wall Breaker on %s at point %s", edge_name, action.sequence_number)
            self._wait_with_checkpoints(random.choice(self.bot_config.delay_between_groups_seconds_options), "SETUP_5_SUPER_DELAY")

        self._tap_slot(goblin_slot.bounding_box, plan.screenshot_width, plan.screenshot_height, "SETUP_5_SELECT_GOBLINS")
        for edge_name, _ in edges:
            actions = [actions_by_number[number] for number in selected_numbers_by_edge[edge_name]]
            logging.info("Setup 5: selected Goblin points on %s: %s", edge_name, selected_numbers_by_edge[edge_name])
            self._deploy_action_round(actions, f"SETUP_5_{edge_name}_GOBLINS", taps_per_point=2)

        self._wait_after_deployment()
        return BattleEndController(adb_controller=self.adb_controller, package_name=self.package_name, threshold=self.screen_threshold, dry_run=False, return_home_timeout_seconds=self.bot_config.new_base_timeout_seconds, screen_transition_poll_seconds_options=self.bot_config.screen_transition_poll_seconds_options, control=self.control).run()

    def _deploy_setup_6_then_end_battle(self) -> int:
        planning = SneakyGoblinPlanner().plan_attack(screenshot_path=CURRENT_SCREENSHOT_PATH, config=self.bot_config)
        plan, goblin_slot = planning.attack_plan, planning.troop_slot_result
        actions_by_number = {action.sequence_number: action for action in plan.actions}
        edge_numbers = {
            "DA": tuple(range(1, 11)),
            "AB": tuple(range(11, 21)),
            "BC": tuple(range(21, 30)),
            "CD": tuple(range(30, 36)),
        }
        if (
            not plan.valid
            or goblin_slot.bounding_box is None
            or any(number not in actions_by_number for numbers in edge_numbers.values() for number in numbers)
        ):
            raise TrialFlowControllerError("Setup 6 requires all 35 deployment points and a detected Goblin slot.")
        self._save_attack_plan_debug_image(planning)
        super_slot = detect_template(CURRENT_SCREENSHOT_PATH, SUPER_WALL_BREAKER_TEMPLATE_PATH, threshold=self.screen_threshold)
        if not super_slot.found:
            raise TrialFlowControllerError(f"Setup 6 could not detect the Super Wall Breaker slot ({super_slot.confidence:.2f}).")

        required_goblins = 64
        count = TroopCountReader().read(CURRENT_SCREENSHOT_PATH, goblin_slot.bounding_box, DEBUG_DIRECTORY)
        if count.value is None or count.value < required_goblins:
            reason = "TROOP_COUNT_OCR_FAILED" if count.value is None else f"INSUFFICIENT_TROOPS: need {required_goblins}, detected {count.value}"
            return self._return_home_without_deployment(reason)

        selected_numbers_by_edge = {
            "DA": tuple(sorted(random.sample(edge_numbers["DA"], k=7))),
            "BC": tuple(sorted(random.sample(edge_numbers["BC"], k=7))),
            "AB": tuple(sorted(random.sample(edge_numbers["AB"], k=6))),
            "CD": tuple(sorted(random.sample(edge_numbers["CD"], k=6))),
        }
        self.control.report(
            troopCount=count.value,
            troopCountRawText=count.raw_text,
            attackPlan={
                "setup": "setup_6",
                "superWallBreakers": 4,
                "selectedPointsByEdge": selected_numbers_by_edge,
                "plannedGoblinCount": required_goblins,
            },
        )

        self._tap_slot(super_slot.bounding_box, *super_slot.screenshot_size, "SETUP_6_SELECT_SUPER_WALL_BREAKERS")
        for edge_name in ("DA", "AB", "BC", "CD"):
            action = actions_by_number[random.choice(edge_numbers[edge_name])]
            self.control.checkpoint(f"SETUP_6_DEPLOY_SUPER_{edge_name}")
            self.adb_controller.tap(action.x, action.y)
            logging.info("Setup 6: deployed one Super Wall Breaker on %s at point %s", edge_name, action.sequence_number)
            self._wait_with_checkpoints(random.choice(self.bot_config.delay_between_groups_seconds_options), "SETUP_6_SUPER_DELAY")

        for edge_name, taps_per_point in (("BC", 2), ("DA", 2), ("AB", 3), ("CD", 3)):
            actions = [actions_by_number[number] for number in selected_numbers_by_edge[edge_name]]
            self._tap_slot(
                goblin_slot.bounding_box,
                plan.screenshot_width,
                plan.screenshot_height,
                f"SETUP_6_SELECT_GOBLINS_{edge_name}",
            )
            logging.info("Setup 6: selected Goblin points on %s: %s", edge_name, selected_numbers_by_edge[edge_name])
            self._deploy_action_round(actions, f"SETUP_6_{edge_name}_GOBLINS", taps_per_point=taps_per_point)

        self._wait_after_deployment()
        return BattleEndController(adb_controller=self.adb_controller, package_name=self.package_name, threshold=self.screen_threshold, dry_run=False, return_home_timeout_seconds=self.bot_config.new_base_timeout_seconds, screen_transition_poll_seconds_options=self.bot_config.screen_transition_poll_seconds_options, control=self.control).run()

    def _deploy_setup_7_then_end_battle(self) -> int:
        planning = SneakyGoblinPlanner().plan_attack(screenshot_path=CURRENT_SCREENSHOT_PATH, config=self.bot_config)
        plan, goblin_slot = planning.attack_plan, planning.troop_slot_result
        actions_by_number = {action.sequence_number: action for action in plan.actions}
        edge_numbers = {
            "DA": tuple(range(1, 11)),
            "AB": tuple(range(11, 21)),
            "BC": tuple(range(21, 30)),
            "CD": tuple(range(30, 36)),
        }
        if (
            not plan.valid
            or goblin_slot.bounding_box is None
            or any(number not in actions_by_number for numbers in edge_numbers.values() for number in numbers)
        ):
            raise TrialFlowControllerError("Setup 7 requires all 35 deployment points and a detected Goblin slot.")
        self._save_attack_plan_debug_image(planning)

        required_goblins = 62
        count = TroopCountReader().read(CURRENT_SCREENSHOT_PATH, goblin_slot.bounding_box, DEBUG_DIRECTORY)
        if count.value is None or count.value < required_goblins:
            reason = "TROOP_COUNT_OCR_FAILED" if count.value is None else f"INSUFFICIENT_TROOPS: need {required_goblins}, detected {count.value}"
            return self._return_home_without_deployment(reason)

        selected_numbers_by_edge = {
            "DA": tuple(sorted(random.sample(edge_numbers["DA"], k=4))),
            "AB": tuple(sorted(random.sample(edge_numbers["AB"], k=4))),
            "BC": tuple(sorted(random.sample(edge_numbers["BC"], k=5))),
            "CD": tuple(sorted(random.sample(edge_numbers["CD"], k=5))),
        }
        self.control.report(
            troopCount=count.value,
            troopCountRawText=count.raw_text,
            attackPlan={
                "setup": "setup_7",
                "superWallBreakers": 0,
                "selectedPointsByEdge": selected_numbers_by_edge,
                "plannedGoblinCount": required_goblins,
            },
        )

        for edge_name, taps_per_point in (("DA", 4), ("AB", 4), ("CD", 3), ("BC", 3)):
            actions = [actions_by_number[number] for number in selected_numbers_by_edge[edge_name]]
            self._tap_slot(
                goblin_slot.bounding_box,
                plan.screenshot_width,
                plan.screenshot_height,
                f"SETUP_7_SELECT_GOBLINS_{edge_name}",
            )
            logging.info("Setup 7: selected Goblin points on %s: %s", edge_name, selected_numbers_by_edge[edge_name])
            self._deploy_action_round(actions, f"SETUP_7_{edge_name}_GOBLINS", taps_per_point=taps_per_point)

        self._wait_after_deployment()
        return BattleEndController(adb_controller=self.adb_controller, package_name=self.package_name, threshold=self.screen_threshold, dry_run=False, return_home_timeout_seconds=self.bot_config.new_base_timeout_seconds, screen_transition_poll_seconds_options=self.bot_config.screen_transition_poll_seconds_options, control=self.control).run()

    def _deploy_setup_8_then_end_battle(self) -> int:
        planning = SneakyGoblinPlanner().plan_attack(screenshot_path=CURRENT_SCREENSHOT_PATH, config=self.bot_config)
        plan, goblin_slot = planning.attack_plan, planning.troop_slot_result
        actions_by_number = {action.sequence_number: action for action in plan.actions}
        clockwise_cycle = tuple(range(1, 11)) + tuple(range(20, 10, -1)) + tuple(range(21, 30)) + tuple(range(30, 36))
        counter_clockwise_cycle = tuple(reversed(clockwise_cycle))
        clockwise_order = self._rotate_point_order(clockwise_cycle, start_point=22)
        counter_clockwise_order = self._rotate_point_order(counter_clockwise_cycle, start_point=12)
        if (
            not plan.valid
            or goblin_slot.bounding_box is None
            or any(number not in actions_by_number for number in clockwise_order)
            or any(number not in actions_by_number for number in counter_clockwise_order)
        ):
            raise TrialFlowControllerError("Setup 8 requires all 35 deployment points and a detected Goblin slot.")
        self._save_attack_plan_debug_image(planning)
        super_slot = detect_template(CURRENT_SCREENSHOT_PATH, SUPER_WALL_BREAKER_TEMPLATE_PATH, threshold=self.screen_threshold)
        if not super_slot.found:
            raise TrialFlowControllerError(f"Setup 8 could not detect the Super Wall Breaker slot ({super_slot.confidence:.2f}).")

        required_goblins = 70
        count = TroopCountReader().read(CURRENT_SCREENSHOT_PATH, goblin_slot.bounding_box, DEBUG_DIRECTORY)
        if count.value is None or count.value < required_goblins:
            reason = "TROOP_COUNT_OCR_FAILED" if count.value is None else f"INSUFFICIENT_TROOPS: need {required_goblins}, detected {count.value}"
            return self._return_home_without_deployment(reason)

        clockwise_actions = [actions_by_number[number] for number in clockwise_order]
        counter_clockwise_actions = [actions_by_number[number] for number in counter_clockwise_order]
        self.control.report(
            troopCount=count.value,
            troopCountRawText=count.raw_text,
            attackPlan={
                "setup": "setup_8",
                "clockwiseStartPoint": 22,
                "counterClockwiseStartPoint": 12,
                "goblinRounds": 2,
                "goblinsPerPoint": 1,
                "superWallBreakers": 4,
            },
        )

        self._tap_slot(goblin_slot.bounding_box, plan.screenshot_width, plan.screenshot_height, "SETUP_8_SELECT_GOBLINS_CLOCKWISE")
        self._deploy_action_round(clockwise_actions, "SETUP_8_GOBLIN_CLOCKWISE")
        self._tap_slot(super_slot.bounding_box, *super_slot.screenshot_size, "SETUP_8_SELECT_SUPER_WALL_BREAKERS")
        for edge_name, point_numbers in (("DA", tuple(range(1, 11))), ("AB", tuple(range(11, 21))), ("BC", tuple(range(21, 30))), ("CD", tuple(range(30, 36)))):
            action = actions_by_number[random.choice(point_numbers)]
            self.control.checkpoint(f"SETUP_8_DEPLOY_SUPER_{edge_name}")
            self.adb_controller.tap(action.x, action.y)
            logging.info("Setup 8: deployed one Super Wall Breaker on %s at point %s", edge_name, action.sequence_number)
            self._wait_with_checkpoints(random.choice(self.bot_config.delay_between_groups_seconds_options), "SETUP_8_SUPER_DELAY")
        self._tap_slot(goblin_slot.bounding_box, plan.screenshot_width, plan.screenshot_height, "SETUP_8_RESELECT_GOBLINS_COUNTER_CLOCKWISE")
        self._deploy_action_round(counter_clockwise_actions, "SETUP_8_GOBLIN_COUNTER_CLOCKWISE")

        self._wait_after_deployment()
        return BattleEndController(adb_controller=self.adb_controller, package_name=self.package_name, threshold=self.screen_threshold, dry_run=False, return_home_timeout_seconds=self.bot_config.new_base_timeout_seconds, screen_transition_poll_seconds_options=self.bot_config.screen_transition_poll_seconds_options, control=self.control).run()

    @staticmethod
    def _rotate_point_order(point_cycle: tuple[int, ...], *, start_point: int) -> tuple[int, ...]:
        start_index = point_cycle.index(start_point)
        return point_cycle[start_index:] + point_cycle[:start_index]

    def _select_random_setup(self) -> str:
        setup_names = ("setup_1", "setup_2", "setup_3", "setup_4", "setup_5", "setup_6", "setup_7", "setup_8")
        candidates = setup_names
        if len(self.setup_history) >= 2 and self.setup_history[-1] == self.setup_history[-2]:
            candidates = tuple(name for name in setup_names if name != self.setup_history[-1])
            logging.info("Setup %s was used twice consecutively; excluding it for this battle", self.setup_history[-1])
        setup = random.choice(candidates)
        self.setup_history.append(setup)
        self.selected_setup = setup
        return setup

    def _save_attack_plan_debug_image(self, planning) -> None:
        """Persist the current base plan so it never shows a previous battle."""
        save_attack_plan_debug_image(
            screenshot_path=CURRENT_SCREENSHOT_PATH,
            output_path=ATTACK_PLAN_DEBUG_PATH,
            battlefield_roi=planning.battlefield_roi,
            battlefield_polygon=planning.battlefield_polygon,
            excluded_regions=planning.excluded_regions,
            troop_slot_box=planning.troop_slot_result.bounding_box,
            attack_plan=planning.attack_plan,
            debug_boundary_da_end_ratio=self.bot_config.debug_boundary_da_end_ratio,
            debug_boundary_bh_length_ratio=self.bot_config.debug_boundary_bh_length_ratio,
            debug_boundary_bk_length_ratio=self.bot_config.debug_boundary_bk_length_ratio,
        )
        logging.info("Attack plan debug image saved to %s", ATTACK_PLAN_DEBUG_PATH.as_posix())
        self.control.report(debugArtifactPaths=[ATTACK_PLAN_DEBUG_PATH.as_posix()])

    def _tap_slot(self, box, width: int, height: int, phase: str) -> None:
        point = select_random_point_in_box(box, (width, height))
        self.control.checkpoint(phase)
        self.adb_controller.tap(*point)
        self._wait_after_troop_selection()

    def _wait_after_troop_selection(self) -> None:
        delay_seconds = random.choice(self.bot_config.troop_selection_delay_seconds_options)
        logging.info("Waiting %.2fs after selecting troops", delay_seconds)
        self._wait_with_checkpoints(delay_seconds, "TROOP_SELECTION_DELAY")

    def _deploy_action_round(self, actions, phase: str, taps_per_point: int = 1) -> None:
        for index, action in enumerate(actions):
            for tap_index in range(taps_per_point):
                self.control.checkpoint(phase)
                self.adb_controller.tap(action.x, action.y)
                if tap_index < taps_per_point - 1:
                    self._wait_with_checkpoints(
                        random.choice(self.bot_config.delay_between_taps_seconds_options),
                        "DEPLOYMENT_TAP_DELAY",
                    )
            if index < len(actions) - 1:
                self._wait_with_checkpoints(random.choice(self.bot_config.delay_between_groups_seconds_options), "DEPLOYMENT_GROUP_DELAY")

    def _wait_after_deployment(self) -> None:
        delay_seconds = random.choice(self.bot_config.post_deployment_wait_seconds_options)
        logging.info("Waiting %.1f seconds before surrendering", delay_seconds)
        self._wait_with_checkpoints(delay_seconds, "POST_DEPLOYMENT_WAIT")

    def _return_home_without_deployment(self, reason: str) -> int:
        logging.error(reason)
        self.control.report(failureCode=reason.split(":", 1)[0], failureMessage=reason)
        logging.info("Returning Home without deployment because the troop count was not safe to use")
        return BattleEndController(
            adb_controller=self.adb_controller,
            package_name=self.package_name,
            threshold=self.screen_threshold,
            dry_run=False,
            return_home_timeout_seconds=self.bot_config.new_base_timeout_seconds,
            screen_transition_poll_seconds_options=self.bot_config.screen_transition_poll_seconds_options,
            control=self.control,
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
            self.control.checkpoint(f"WAIT_{expected_state.value}")
            time.sleep(random.choice(self.bot_config.screen_transition_poll_seconds_options))
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
            self.control.checkpoint("WAIT_NEW_ENEMY_BASE")
            time.sleep(random.choice(self.bot_config.screen_transition_poll_seconds_options))
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

    def _wait_for_enemy_base_to_settle(self) -> ScreenDetectionResult:
        delay_seconds = random.choice(self.bot_config.enemy_base_settle_seconds_options)
        if delay_seconds > 0:
            logging.info("Waiting %.1f seconds for the enemy base to finish loading", delay_seconds)
            self._wait_with_checkpoints(delay_seconds, "WAIT_ENEMY_BASE_LOAD")

        detection = self._capture_and_detect()
        if detection.state is not ScreenState.ENEMY_BASE:
            raise TrialFlowControllerError(
                f"Expected ENEMY_BASE after the loading delay; detected {detection.state.value}."
            )
        return detection

    def _tap_detection(
        self,
        detection: ScreenDetectionResult,
        label: str,
        *,
        use_action_template: bool = False,
        dry_run: bool = False,
    ) -> None:
        expected_by_label = {
            "Attack button": ScreenState.HOME,
            "Find a Match button": ScreenState.ATTACK_MENU,
            "Confirm Attack button": ScreenState.ARMY_CONFIRMATION,
            "Next button": ScreenState.ENEMY_BASE,
        }
        expected = expected_by_label.get(label)
        if expected:
            # Fresh detection prevents using coordinates from an earlier screenshot.
            detection = self.reliability_guard.require_expected_state((expected,), label.upper().replace(" ", "_"))
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
        self.control.checkpoint(label.upper().replace(" ", "_"))
        self.adb_controller.tap(*tap_point)
        logging.info("%s tapped once at (%s, %s)", label, *tap_point)

    def _capture_and_detect(self) -> ScreenDetectionResult:
        self._assert_game_ready()
        self.adb_controller.capture_screenshot(CURRENT_SCREENSHOT_PATH)
        logging.info("Screenshot captured")
        logging.info("Screenshot saved to %s", CURRENT_SCREENSHOT_PATH.as_posix())
        detection = detect_screen(
            CURRENT_SCREENSHOT_PATH,
            threshold=self.screen_threshold,
            debug_directory=DEBUG_DIRECTORY,
        )
        self.control.report(
            gameScreen=detection.state.value,
            screenConfidence=detection.confidence,
            screenDetails={"template": detection.matched_template_name, "bestCandidateConfidence": detection.best_candidate_confidence},
            screenshotPath=CURRENT_SCREENSHOT_PATH.as_posix(),
        )
        return detection

    def _assert_game_ready(self) -> None:
        self.control.checkpoint()
        foreground_app = self.adb_controller.get_foreground_app()
        if foreground_app != self.package_name:
            raise TrialFlowControllerError(
                f"Clash of Clans left the foreground. Current foreground app: {foreground_app or 'unknown'}"
            )

    def _log_resource_result(self, result: ResourceReadResult) -> None:
        self.control.report(gold=result.gold.value, elixir=result.elixir.value, darkElixir=result.dark_elixir.value)
        for name, reading in (
            ("Gold", result.gold),
            ("Elixir", result.elixir),
            ("Dark Elixir", result.dark_elixir),
        ):
            if reading.value is None:
                logging.error("%s reading failed. Raw OCR text: %r", name, reading.raw_ocr_text)
            else:
                logging.info("%s parsed value: %s", name, reading.value)

    def _log_decision(self, result: DecisionResult) -> None:
        self.control.report(decision=result.decision.value, decisionReasons=list(result.reasons))
        for reason in result.reasons:
            logging.info("Reason: %s", reason)
        logging.info("Decision: %s", result.decision.value)

    @staticmethod
    def _save_failure_screenshot() -> Path:
        DEBUG_DIRECTORY.mkdir(parents=True, exist_ok=True)
        output = DEBUG_DIRECTORY / "full_flow_failure_latest.png"
        output.write_bytes(CURRENT_SCREENSHOT_PATH.read_bytes())
        return output

    def _wait_with_checkpoints(self, delay_seconds: float, phase: str) -> None:
        deadline = time.monotonic() + delay_seconds
        while time.monotonic() < deadline:
            self.control.checkpoint(phase)
            time.sleep(min(0.1, deadline - time.monotonic()))
