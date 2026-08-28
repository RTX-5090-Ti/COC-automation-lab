from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import cv2
import numpy as np

from screen_detector import BoundingBox


class ActionType(str, Enum):
    SELECT_TROOP = "SELECT_TROOP"
    DEPLOY_GROUP = "DEPLOY_GROUP"


@dataclass(frozen=True)
class AttackAction:
    sequence_number: int
    action_type: ActionType
    x: int
    y: int
    amount: int
    delay_after_seconds: float
    description: str


@dataclass(frozen=True)
class AttackPlan:
    strategy_name: str
    valid: bool
    actions: list[AttackAction]
    screenshot_width: int
    screenshot_height: int
    troop_slot_center: tuple[int, int] | None
    error_message: str | None


def save_attack_plan_debug_image(
    *,
    screenshot_path: str | Path,
    output_path: str | Path,
    battlefield_roi: BoundingBox,
    battlefield_polygon: list[tuple[int, int]],
    excluded_regions: list[BoundingBox],
    troop_slot_box: BoundingBox | None,
    attack_plan: AttackPlan,
    debug_boundary_da_end_ratio: float = 1.0,
    debug_boundary_bh_length_ratio: float = 1.0,
    debug_boundary_bk_length_ratio: float = 1.0,
) -> Path:
    image = cv2.imread(str(screenshot_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Screenshot file could not be decoded: {screenshot_path}")

    annotated = image.copy()
    _draw_polygon(
        annotated,
        battlefield_polygon,
        excluded_regions,
        (255, 0, 0),
        "Battlefield ROI",
        debug_boundary_da_end_ratio,
        debug_boundary_bh_length_ratio,
        debug_boundary_bk_length_ratio,
    )

    for index, region in enumerate(excluded_regions, start=1):
        _draw_box(annotated, region, (0, 165, 255), f"Excluded {index}")

    if troop_slot_box is not None:
        _draw_box(annotated, troop_slot_box, (255, 255, 0), "Sneaky Goblin Slot")

    for action in attack_plan.actions:
        point = (action.x, action.y)
        cv2.circle(annotated, point, 10, (0, 0, 255), -1)
        cv2.putText(
            annotated,
            f"{action.sequence_number}:{action.amount}",
            (action.x + 12, action.y - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), annotated):
        raise ValueError(f"Debug image could not be saved: {output}")
    return output


def _draw_box(image: cv2.typing.MatLike, box: BoundingBox, color: tuple[int, int, int], label: str) -> None:
    top_left = (box.x, box.y)
    bottom_right = (box.x + box.width, box.y + box.height)
    cv2.rectangle(image, top_left, bottom_right, color, 2)
    cv2.putText(
        image,
        label,
        (box.x, max(20, box.y - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        color,
        2,
        cv2.LINE_AA,
    )


def _draw_polygon(
    image: cv2.typing.MatLike,
    points: list[tuple[int, int]],
    excluded_regions: list[BoundingBox],
    color: tuple[int, int, int],
    label: str,
    debug_boundary_da_end_ratio: float,
    debug_boundary_bh_length_ratio: float,
    debug_boundary_bk_length_ratio: float,
) -> None:
    line_layer = np.zeros_like(image)
    line_mask = np.zeros(image.shape[:2], dtype=np.uint8)
    # C-D remains the only boundary edge masked by the UI exclusions.
    cv2.line(line_layer, points[2], points[3], color, 6, cv2.LINE_AA)
    cv2.line(line_mask, points[2], points[3], 255, 6, cv2.LINE_AA)

    # Draw only D-E for the DA boundary. E is a visual point on the original DA edge.
    da_start = points[3]
    da_end = points[0]
    e_x = round(da_start[0] + (da_end[0] - da_start[0]) * debug_boundary_da_end_ratio)
    e_y = round(da_start[1] + (da_end[1] - da_start[1]) * debug_boundary_da_end_ratio)

    # Do not show the battlefield boundary inside UI exclusion zones.
    for region in excluded_regions:
        cv2.rectangle(
            line_mask,
            (region.x, region.y),
            (region.x + region.width, region.y + region.height),
            0,
            -1,
        )

    cv2.copyTo(line_layer, line_mask, image)

    # D-E is a configurable preview guide, so keep it visible over Excluded 2.
    cv2.line(image, da_start, (e_x, e_y), color, 6, cv2.LINE_AA)

    b_point = points[1]
    a_point = points[0]
    h_x = round(b_point[0] + (a_point[0] - b_point[0]) * debug_boundary_bh_length_ratio)
    h_y = round(b_point[1] + (a_point[1] - b_point[1]) * debug_boundary_bh_length_ratio)
    cv2.line(image, b_point, (h_x, h_y), color, 6, cv2.LINE_AA)

    c_point = points[2]
    k_x = round(b_point[0] + (c_point[0] - b_point[0]) * debug_boundary_bk_length_ratio)
    k_y = round(b_point[1] + (c_point[1] - b_point[1]) * debug_boundary_bk_length_ratio)
    cv2.line(image, b_point, (k_x, k_y), color, 6, cv2.LINE_AA)

    x, y = points[1]
    cv2.putText(image, label, (x + 12, y - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)
