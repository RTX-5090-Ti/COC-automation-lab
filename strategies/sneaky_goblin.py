from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import random

import cv2
import numpy as np

from decision_engine import BotConfig
from screen_detector import BoundingBox
from project_paths import asset_path
from strategies.attack_plan import ActionType, AttackAction, AttackPlan


SNEAKY_GOBLIN_TEMPLATE_PATH = asset_path("templates", "battle", "sneaky_goblin_slot.png")


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
    battlefield_polygon: list[tuple[int, int]]
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
        battlefield_polygon = self._build_battlefield_polygon(width, height, config)
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
                battlefield_polygon=battlefield_polygon,
                excluded_regions=excluded_regions,
            )

        actions = self._generate_perimeter_sweep_actions(
            width,
            height,
            battlefield_roi,
            battlefield_polygon,
            excluded_regions,
            config,
        )
        if not actions:
            return StrategyPlanningResult(
                attack_plan=AttackPlan(
                    strategy_name=config.sneaky_goblin_mode,
                    valid=False,
                    actions=[],
                    screenshot_width=width,
                    screenshot_height=height,
                    troop_slot_center=troop_slot_result.center,
                    error_message="No valid deployment points could be generated outside the battlefield boundary.",
                ),
                troop_slot_result=troop_slot_result,
                battlefield_roi=battlefield_roi,
                battlefield_polygon=battlefield_polygon,
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
                battlefield_polygon=battlefield_polygon,
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
            battlefield_polygon=battlefield_polygon,
            excluded_regions=excluded_regions,
        )

    def _detect_troop_slot(self, screenshot: cv2.typing.MatLike, threshold: float) -> TemplateMatchResult:
        return self._detect_template(screenshot, SNEAKY_GOBLIN_TEMPLATE_PATH, threshold, "Sneaky Goblin slot")

    def validate_deployment_action(
        self,
        *,
        screenshot_path: str | Path,
        config: BotConfig,
        action: AttackAction,
    ) -> tuple[BoundingBox, list[BoundingBox]]:
        screenshot = self._load_image(Path(screenshot_path))
        height, width = screenshot.shape[:2]
        battlefield_roi = self._build_battlefield_roi(width, height, config)
        battlefield_polygon = self._build_battlefield_polygon(width, height, config)
        excluded_regions = self._build_excluded_regions(width, height, config)
        if not self._is_valid_point(
            x=action.x,
            y=action.y,
            screenshot_width=width,
            screenshot_height=height,
            battlefield_roi=battlefield_roi,
            battlefield_polygon=battlefield_polygon,
            excluded_regions=excluded_regions,
        ):
            raise SneakyGoblinPlanningError(
                f"Deployment point is not valid on the current screenshot: ({action.x}, {action.y})."
            )
        return battlefield_roi, excluded_regions

    def _detect_template(
        self,
        screenshot: cv2.typing.MatLike,
        template_path: Path,
        threshold: float,
        label: str,
    ) -> TemplateMatchResult:
        template = self._load_image(template_path)
        screenshot_height, screenshot_width = screenshot.shape[:2]
        template_height, template_width = template.shape[:2]

        if template_width > screenshot_width or template_height > screenshot_height:
            raise SneakyGoblinPlanningError(f"{label} template is larger than the screenshot.")

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
        battlefield_polygon: list[tuple[int, int]],
        excluded_regions: list[BoundingBox],
        config: BotConfig,
    ) -> list[AttackAction]:
        selected_points = self._build_edge_deployment_points(
            battlefield_polygon=battlefield_polygon,
            screenshot_width=screenshot_width,
            screenshot_height=screenshot_height,
            excluded_regions=excluded_regions,
            inset_pixels=config.deployment_edge_inset_pixels,
            random_inset_ratio=config.deployment_edge_inset_random_ratio,
            random_inset_pixels=config.deployment_edge_inset_random_pixels,
            edge_point_counts=(
                config.deployment_points_da,
                config.deployment_points_ab,
                config.deployment_points_bc,
                config.deployment_points_cd,
            ),
            guide_ratios=(
                config.debug_boundary_da_end_ratio,
                config.debug_boundary_bh_length_ratio,
                config.debug_boundary_bk_length_ratio,
            ),
        )[: config.planned_deployment_points]

        actions: list[AttackAction] = []
        for edge_name, x, y in selected_points:
            if x < 0 or y < 0 or x >= screenshot_width or y >= screenshot_height:
                continue
            if edge_name == "C-D" and not self._is_valid_point(
                x=x,
                y=y,
                screenshot_width=screenshot_width,
                screenshot_height=screenshot_height,
                battlefield_roi=battlefield_roi,
                battlefield_polygon=battlefield_polygon,
                excluded_regions=excluded_regions,
            ):
                continue

            index = len(actions) + 1
            actions.append(
                AttackAction(
                    sequence_number=index,
                    action_type=ActionType.DEPLOY_GROUP,
                    x=x,
                    y=y,
                    amount=config.goblins_per_point,
                    delay_after_seconds=config.delay_between_groups_seconds,
                    description=f"Deploy Sneaky Goblin group {index} near edge {edge_name}.",
                )
            )

        return actions

    @staticmethod
    def _build_edge_deployment_points(
        *,
        battlefield_polygon: list[tuple[int, int]],
        screenshot_width: int,
        screenshot_height: int,
        excluded_regions: list[BoundingBox],
        inset_pixels: int,
        random_inset_ratio: float,
        random_inset_pixels: tuple[int, ...],
        edge_point_counts: tuple[int, int, int, int],
        guide_ratios: tuple[float, float, float],
    ) -> list[tuple[str, int, int]]:
        top, right, bottom, left = battlefield_polygon
        da_ratio, bh_ratio, bk_ratio = guide_ratios
        point_e = _interpolate_point(left, top, da_ratio)
        point_h = _interpolate_point(right, top, bh_ratio)
        point_k = _interpolate_point(right, bottom, bk_ratio)
        top_limit, bottom_limit = _deployment_vertical_limits(
            screenshot_width, screenshot_height, excluded_regions
        )
        clipped_cd = _clip_edge_to_vertical_band(bottom, left, top_limit, bottom_limit)
        edges = (
            ("D-E", left, point_e, edge_point_counts[0]),
            ("B-H", right, point_h, edge_point_counts[1]),
            ("B-K", right, point_k, edge_point_counts[2]),
        )
        if clipped_cd is not None:
            edges += (("C-D", clipped_cd[0], clipped_cd[1], edge_point_counts[3]),)

        center_x = sum(point[0] for point in battlefield_polygon) / len(battlefield_polygon)
        center_y = sum(point[1] for point in battlefield_polygon) / len(battlefield_polygon)
        points: list[tuple[str, int, int]] = []
        total_point_count = sum(point_count for _, _, _, point_count in edges)
        random_point_count = min(total_point_count, math.ceil(total_point_count * random_inset_ratio))
        randomized_point_indices = set(random.sample(range(total_point_count), random_point_count))
        for edge_name, start, end, point_count in edges:
            for point_index in range(1, point_count + 1):
                fraction = point_index / (point_count + 1)
                edge_x = start[0] + (end[0] - start[0]) * fraction
                edge_y = start[1] + (end[1] - start[1]) * fraction
                direction_x = edge_x - center_x
                direction_y = edge_y - center_y
                direction_length = max((direction_x**2 + direction_y**2) ** 0.5, 1.0)
                point_inset_pixels = (
                    random.choice(random_inset_pixels)
                    if len(points) in randomized_point_indices
                    else inset_pixels
                )
                x = round(edge_x + direction_x / direction_length * point_inset_pixels)
                y = round(edge_y + direction_y / direction_length * point_inset_pixels)
                points.append((edge_name, x, y))
        return points

    @staticmethod
    def _load_image(path: Path) -> cv2.typing.MatLike:
        if not path.is_file():
            raise SneakyGoblinPlanningError(f"Required image file does not exist: {path}")
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise SneakyGoblinPlanningError(f"Required image file could not be decoded: {path}")
        return image

    @staticmethod
    def _is_valid_point(
        *,
        x: int,
        y: int,
        screenshot_width: int,
        screenshot_height: int,
        battlefield_roi: BoundingBox,
        battlefield_polygon: list[tuple[int, int]],
        excluded_regions: list[BoundingBox],
    ) -> bool:
        if x < 0 or y < 0 or x >= screenshot_width or y >= screenshot_height:
            return False
        if not _point_in_box(x, y, battlefield_roi):
            return False
        if cv2.pointPolygonTest(np.array(battlefield_polygon, dtype=np.int32), (x, y), False) >= 0:
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
    def _build_battlefield_polygon(
        screenshot_width: int,
        screenshot_height: int,
        config: BotConfig,
    ) -> list[tuple[int, int]]:
        return [
            (
                int(screenshot_width * config.battlefield_diamond_top_x_ratio),
                int(screenshot_height * config.battlefield_diamond_top_y_ratio),
            ),
            (
                int(screenshot_width * config.battlefield_diamond_right_x_ratio),
                int(screenshot_height * config.battlefield_diamond_right_y_ratio),
            ),
            (
                int(screenshot_width * config.battlefield_diamond_bottom_x_ratio),
                int(screenshot_height * config.battlefield_diamond_bottom_y_ratio),
            ),
            (
                int(screenshot_width * config.battlefield_diamond_left_x_ratio),
                int(screenshot_height * config.battlefield_diamond_left_y_ratio),
            ),
        ]

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


def _interpolate_point(
    start: tuple[int, int], end: tuple[int, int], fraction: float
) -> tuple[int, int]:
    return (
        round(start[0] + (end[0] - start[0]) * fraction),
        round(start[1] + (end[1] - start[1]) * fraction),
    )


def _point_in_box(x: int, y: int, box: BoundingBox) -> bool:
    return box.x <= x < box.x + box.width and box.y <= y < box.y + box.height


def _deployment_vertical_limits(
    screenshot_width: int,
    screenshot_height: int,
    excluded_regions: list[BoundingBox],
) -> tuple[int, int]:
    top_limit = 0
    bottom_limit = screenshot_height - 1
    for region in excluded_regions:
        if region.x != 0 or region.width < screenshot_width:
            continue
        if region.y <= screenshot_height // 2:
            top_limit = max(top_limit, region.y + region.height)
        else:
            bottom_limit = min(bottom_limit, region.y - 1)
    return top_limit, bottom_limit


def _clip_edge_to_vertical_band(
    start: tuple[int, int],
    end: tuple[int, int],
    minimum_y: int,
    maximum_y: int,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    start_x, start_y = start
    end_x, end_y = end
    delta_x = end_x - start_x
    delta_y = end_y - start_y
    if delta_y == 0:
        if minimum_y <= start_y <= maximum_y:
            return (float(start_x), float(start_y)), (float(end_x), float(end_y))
        return None

    first_t = (minimum_y - start_y) / delta_y
    last_t = (maximum_y - start_y) / delta_y
    lower_t = max(0.0, min(first_t, last_t))
    upper_t = min(1.0, max(first_t, last_t))
    if lower_t >= upper_t:
        return None
    return (
        (start_x + delta_x * lower_t, start_y + delta_y * lower_t),
        (start_x + delta_x * upper_t, start_y + delta_y * upper_t),
    )
