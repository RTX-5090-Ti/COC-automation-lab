"""Read the remaining troop count displayed above a detected troop slot."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2

from resource_reader import ResourceReader
from screen_detector import BoundingBox


@dataclass(frozen=True)
class TroopCountResult:
    value: int | None
    raw_text: str
    roi_path: Path
    processed_path: Path


class TroopCountReader:
    """OCRs only the count band above the troop art; the level badge is excluded."""

    def __init__(self) -> None:
        self._resource_reader = ResourceReader()

    def read(self, screenshot_path: str | Path, slot_box: BoundingBox, debug_directory: str | Path) -> TroopCountResult:
        screenshot = cv2.imread(str(screenshot_path), cv2.IMREAD_COLOR)
        if screenshot is None:
            raise RuntimeError("Troop-count screenshot could not be decoded.")
        height, width = screenshot.shape[:2]
        # The count sits in the upper-right band. Exclude the troop artwork and level badge.
        x1 = max(0, slot_box.x + slot_box.width // 3)
        x2 = min(width, slot_box.x + slot_box.width - 2)
        y1 = max(0, slot_box.y - 30)
        y2 = min(height, slot_box.y + 8)
        raw = screenshot[y1:y2, x1:x2]
        directory = Path(debug_directory) / "troop_count"
        directory.mkdir(parents=True, exist_ok=True)
        roi_path = directory / "sneaky_goblin_count_raw.png"
        processed_path = directory / "sneaky_goblin_count_processed.png"
        cv2.imwrite(str(roi_path), raw)
        hsv = cv2.cvtColor(raw, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (0, 0, 145), (180, 125, 255))
        processed = cv2.resize(mask, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        cv2.imwrite(str(processed_path), processed)
        raw_text = self._resource_reader._run_ocr(processed_path, psm=7)
        digits = "".join(re.findall(r"\d+", raw_text))
        return TroopCountResult(int(digits) if digits else None, raw_text, roi_path, processed_path)
