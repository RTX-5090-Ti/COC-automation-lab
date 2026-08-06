from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2

from screen_detector import BoundingBox


class BattlefieldFingerprintError(Exception):
    """Raised when a screenshot cannot be fingerprinted."""


@dataclass(frozen=True)
class BattlefieldFingerprint:
    """Small normalized battlefield snapshot used for change detection."""

    crop_box: BoundingBox
    image: cv2.typing.MatLike


def build_battlefield_fingerprint(
    screenshot_path: str | Path,
    *,
    left_ratio: float = 0.08,
    top_ratio: float = 0.18,
    right_ratio: float = 0.82,
    bottom_ratio: float = 0.88,
    size: tuple[int, int] = (64, 64),
) -> BattlefieldFingerprint:
    screenshot_file = Path(screenshot_path)
    if not screenshot_file.is_file():
        raise BattlefieldFingerprintError(f"Screenshot file does not exist: {screenshot_file}")

    image = cv2.imread(str(screenshot_file), cv2.IMREAD_COLOR)
    if image is None:
        raise BattlefieldFingerprintError(f"Screenshot file could not be decoded: {screenshot_file}")

    height, width = image.shape[:2]
    x1 = int(width * left_ratio)
    y1 = int(height * top_ratio)
    x2 = int(width * right_ratio)
    y2 = int(height * bottom_ratio)

    if x1 < 0 or y1 < 0 or x2 <= x1 or y2 <= y1 or x2 > width or y2 > height:
        raise BattlefieldFingerprintError("Battlefield crop ratios produced invalid bounds.")

    crop = image[y1:y2, x1:x2]
    grayscale = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    normalized = cv2.resize(grayscale, size, interpolation=cv2.INTER_AREA)
    return BattlefieldFingerprint(
        crop_box=BoundingBox(x=x1, y=y1, width=x2 - x1, height=y2 - y1),
        image=normalized,
    )


def compare_fingerprints(first: BattlefieldFingerprint, second: BattlefieldFingerprint) -> float:
    """Return mean absolute grayscale difference normalized to 0..1."""

    difference = cv2.absdiff(first.image, second.image)
    return float(difference.mean() / 255.0)
