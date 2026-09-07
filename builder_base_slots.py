from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2


REFERENCE_SCREENSHOT_SIZE = (1920, 1080)
BOTTOM_CROP_RATIO = 0.35
BOTTOM_CROP_PIXELS = 15
FIRST_SLOT_TOP_CROP_RATIO = 0.10
FIRST_SLOT_LEFT_CROP_PIXELS = 15
MIDDLE_SLOT_RIGHT_CROP_PIXELS = 10
TROOP_SLOTS = (
    (126, 882, 260, 1068),
    (298, 882, 432, 1068),
    (449, 882, 584, 1068),
    (601, 882, 736, 1068),
    (752, 882, 887, 1068),
    (904, 882, 1039, 1068),
    (1056, 882, 1191, 1068),
)


@dataclass(frozen=True)
class BuilderSlot:
    """One fixed Builder Base army-bar slot, scaled from the 1920x1080 reference UI."""

    kind: str
    index: int
    left: int
    top: int
    right: int
    bottom: int

    @property
    def center(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)


def builder_army_slots(screenshot_size: tuple[int, int]) -> tuple[BuilderSlot, ...]:
    """Return the seven fixed Builder Base army-bar slots for a screenshot size."""
    width, height = screenshot_size
    reference_width, reference_height = REFERENCE_SCREENSHOT_SIZE

    def scale(box: tuple[int, int, int, int], kind: str, index: int) -> BuilderSlot:
        left, top, right, bottom = box
        slot_height = bottom - top
        if index == 1:
            top += round(slot_height * FIRST_SLOT_TOP_CROP_RATIO)
            left += round(FIRST_SLOT_LEFT_CROP_PIXELS * width / reference_width)
        elif 2 <= index <= 6:
            right -= round(MIDDLE_SLOT_RIGHT_CROP_PIXELS * width / reference_width)
        bottom -= round(slot_height * BOTTOM_CROP_RATIO)
        # Keep the tap area above the lower UI details on every troop card.
        bottom -= round(BOTTOM_CROP_PIXELS * height / reference_height)
        return BuilderSlot(
            kind=kind,
            index=index,
            left=round(left * width / reference_width),
            top=round(top * height / reference_height),
            right=round(right * width / reference_width),
            bottom=round(bottom * height / reference_height),
        )

    return tuple(scale(box, "troop", index) for index, box in enumerate(TROOP_SLOTS, start=1))


def save_builder_army_slots_debug(screenshot_path: str | Path, output_path: str | Path) -> Path:
    """Draw fixed Builder Base slot boundaries without inspecting troop artwork."""
    source = Path(screenshot_path)
    output = Path(output_path)
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Builder Base screenshot could not be decoded: {source}")

    height, width = image.shape[:2]
    for slot in builder_army_slots((width, height)):
        color = (255, 180, 0)
        cv2.rectangle(image, (slot.left, slot.top), (slot.right, slot.bottom), color, 3)
        label = f"TROOP {slot.index}"
        cv2.putText(image, label, (slot.left, max(28, slot.top - 9)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)

    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), image):
        raise ValueError(f"Builder Base slot debug image could not be saved: {output}")
    return output
