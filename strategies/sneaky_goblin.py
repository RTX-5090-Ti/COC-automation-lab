from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2

from decision_engine import BotConfig
from screen_detector import BoundingBox
from strategies.attack_plan import ActionType, AttackAction, AttackPlan


SNEAKY_GOBLIN_TEMPLATE_PATH = Path("templates/battle/sneaky_goblin_slot.png")


class SneakyGoblinPlanningError(Exception):
    """Raised when Sneaky Goblin planning cannot proceed safely."""


@dataclass(frozen=True)
class TemplateMatchResult:
    found: bool
    confidence: float
    bounding_box: BoundingBox | None
    center: tuple[int, int] | None


@dataclass(frozen=True)
class StrategyPlanningResult:
    attack_plan: AttackPlan
    troop_slot_result: TemplateMatchResult
    battlefield_roi: BoundingBox
    excluded_regions: list[BoundingBox]


class SneakyGoblinPlanner:
    """Dry-run Sneaky Goblin attack planner for the perimeter_sweep strategy."""

    def plan_attack(
        self,
        *,
        screenshot_path: str | Path,
        config: BotConfig,
    ) -> StrategyPlanningResult:
        screenshot = self._load_image(Path(screenshot_path))
        height, width = screenshot.shape[:2]
        if width <= 0 or height <= 0:
            raise SneakyGoblinPlanningError("Screenshot dimensions are invalid.")

        troop_slot_result = self._detect_troop_slot(screenshot, config.sneaky_goblin_slot_threshold)
        battlefield_roi = self._build_battlefield_roi(width, height, config)
        excluded_regions = self._build_excluded_regions(width, height, config)

        if not troop_slot_result.found or troop_slot_result.bounding_box is None or troop_slot_result.center is None:
            return StrategyPlanningResult(
                attack_plan=AttackPlan(
                    strategy_name=config.sneaky_goblin_mode,
                    valid=False,
                    actions=[],
                    screenshot_width=width,
                    screenshot_height=height,
                    troop_slot_center=None,
                    error_message="Sneaky Goblin troop slot could not be detected reliably.",
                ),
                troop_slot_result=troop_slot_result,
                battlefield_roi=battlefield_roi,
                excluded_regions=excluded_regions,
            )

        actions = self._generate_perimeter_sweep_actions(width, height, battlefield_roi, excluded_regions, config)
        if not actions:
            return StrategyPlanningResult(
                attack_plan=AttackPlan(
                    strategy_name=config.sneaky_goblin_mode,
                    valid=False,
                    actions=[],
                    screenshot_width=width,
                    screenshot_height=height,
                    troop_slot_center=troop_slot_result.center,
                    error_message="No valid deployment points could be generated inside the battlefield ROI.",
                ),
                troop_slot_result=troop_slot_result,
                battlefield_roi=battlefield_roi,
                excluded_regions=excluded_regions,
            )

        if len(actions) > config.maximum_planned_actions:
            return StrategyPlanningResult(
                attack_plan=AttackPlan(
                    strategy_name=config.sneaky_goblin_mode,
                    valid=False,
                    actions=[],
                    screenshot_width=width,
                    screenshot_height=height,
                    troop_slot_center=troop_slot_result.center,
                    error_message="Generated plan exceeds maximumPlannedActions.",
                ),
                troop_slot_result=troop_slot_result,
                battlefield_roi=battlefield_roi,
                excluded_regions=excluded_regions,
            )

        return StrategyPlanningResult(
            attack_plan=AttackPlan(
                strategy_name=config.sneaky_goblin_mode,
                valid=True,
                actions=actions,
                screenshot_width=width,
                screenshot_height=height,
                troop_slot_center=troop_slot_result.center,
                error_message=None,
            ),
            troop_slot_result=troop_slot_result,
            battlefield_roi=battlefield_roi,
            excluded_regions=excluded_regions,
        )

    def _detect_troop_slot(self, screenshot: cv2.typing.MatLike, threshold: float) -> TemplateMatchResult:
        template = self._load_image(SNEAKY_GOBLIN_TEMPLATE_PATH)
        screenshot_height, screenshot_width = screenshot.shape[:2]
        template_height, template_width = template.shape[:2]

        if template_width > screenshot_width or template_height > screenshot_height:
            raise SneakyGoblinPlanningError("Sneaky Goblin slot template is larger than the screenshot.")

        result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
        _, max_confidence, _, max_location = cv2.minMaxLoc(result)
        x, y = max_location
        bounding_box = BoundingBox(x=x, y=y, width=template_width, height=template_height)
        center = (x + template_width // 2, y + template_height // 2)
        return TemplateMatchResult(
            found=float(max_confidence) >= threshold,
            confidence=float(max_confidence),
            bounding_box=bounding_box,
            center=center,
        )

    def _generate_perimeter_sweep_actions(
        self,
        screenshot_width: int,
        screenshot_height: int,
        battlefield_roi: BoundingBox,
        excluded_regions: list[BoundingBox],
        config: BotConfig,
    ) -> list[AttackAction]:
        candidate_points = [
            (0.10, 0.12),
            (0.50, 0.08),
            (0.90, 0.12),
            (0.08, 0.42),
            (0.92, 0.42),
            (0.12, 0.84),
            (0.50, 0.88),
            (0.88, 0.84),
            (0.22, 0.18),
            (0.78, 0.18),
            (0.22, 0.78),
            (0.78, 0.78),
        ]
        selected_points = candidate_points[: config.planned_deployment_points]

        actions: list[AttackAction] = []
        for index, (norm_x, norm_y) in enumerate(selected_points, start=1):
            x, y = self._normalized_to_pixel(norm_x, norm_y, battlefield_roi)
            if not self._is_valid_point(
                x=x,
                y=y,
                screenshot_width=screenshot_width,
                screenshot_height=screenshot_height,
                battlefield_roi=battlefield_roi,
                excluded_regions=excluded_regions,
            ):
                raise SneakyGoblinPlanningError(
                    f"Generated deployment point {index} is invalid: ({x}, {y})."
                )

            actions.append(
                AttackAction(
                    sequence_number=index,
                    action_type=ActionType.DEPLOY_GROUP,
                    x=x,
                    y=y,
                    amount=config.goblins_per_point,
                    delay_after_seconds=config.delay_between_groups_seconds,
                    description=f"Deploy Sneaky Goblin group {index} on the battlefield perimeter.",
                )
            )

        return actions

    @staticmethod
    def _load_image(path: Path) -> cv2.typing.MatLike:
        if not path.is_file():
            raise SneakyGoblinPlanningError(f"Required image file does not exist: {path}")
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise SneakyGoblinPlanningError(f"Required image file could not be decoded: {path}")
        return image

    @staticmethod
    def _normalized_to_pixel(norm_x: float, norm_y: float, battlefield_roi: BoundingBox) -> tuple[int, int]:
        x = battlefield_roi.x + int(norm_x * battlefield_roi.width)
        y = battlefield_roi.y + int(norm_y * battlefield_roi.height)
        return x, y

    @staticmethod
    def _is_valid_point(
        *,
        x: int,
        y: int,
        screenshot_width: int,
        screenshot_height: int,
        battlefield_roi: BoundingBox,
        excluded_regions: list[BoundingBox],
    ) -> bool:
        if x < 0 or y < 0 or x >= screenshot_width or y >= screenshot_height:
            return False
        if not _point_in_box(x, y, battlefield_roi):
            return False
        return not any(_point_in_box(x, y, region) for region in excluded_regions)

    @staticmethod
    def _build_battlefield_roi(screenshot_width: int, screenshot_height: int, config: BotConfig) -> BoundingBox:
        x1 = int(screenshot_width * config.battlefield_left_ratio)
        x2 = int(screenshot_width * config.battlefield_right_ratio)
        y1 = int(screenshot_height * config.battlefield_top_ratio)
        y2 = int(screenshot_height * config.battlefield_bottom_ratio)
        if x2 <= x1 or y2 <= y1:
            raise SneakyGoblinPlanningError("Battlefield ROI configuration is invalid.")
        return BoundingBox(x=x1, y=y1, width=x2 - x1, height=y2 - y1)

    @staticmethod
    def _build_excluded_regions(screenshot_width: int, screenshot_height: int, config: BotConfig) -> list[BoundingBox]:
        return [
            BoundingBox(
                x=int(screenshot_width * config.next_button_exclude_left_ratio),
                y=int(screenshot_height * config.next_button_exclude_top_ratio),
                width=int(screenshot_width * (config.next_button_exclude_right_ratio - config.next_button_exclude_left_ratio)),
                height=int(screenshot_height * (config.next_button_exclude_bottom_ratio - config.next_button_exclude_top_ratio)),
            ),
            BoundingBox(
                x=0,
                y=int(screenshot_height * config.top_ui_exclude_top_ratio),
                width=screenshot_width,
                height=int(screenshot_height * (config.top_ui_exclude_bottom_ratio - config.top_ui_exclude_top_ratio)),
            ),
            BoundingBox(
                x=0,
                y=int(screenshot_height * config.bottom_ui_exclude_top_ratio),
                width=screenshot_width,
                height=screenshot_height - int(screenshot_height * config.bottom_ui_exclude_top_ratio),
            ),
        ]


def _point_in_box(x: int, y: int, box: BoundingBox) -> bool:
    return box.x <= x < box.x + box.width and box.y <= y < box.y + box.height
