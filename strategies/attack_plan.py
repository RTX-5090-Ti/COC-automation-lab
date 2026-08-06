from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import cv2

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
    excluded_regions: list[BoundingBox],
    troop_slot_box: BoundingBox | None,
    attack_plan: AttackPlan,
) -> Path:
    image = cv2.imread(str(screenshot_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Screenshot file could not be decoded: {screenshot_path}")

    annotated = image.copy()
    _draw_box(annotated, battlefield_roi, (0, 255, 0), "Battlefield ROI")

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
