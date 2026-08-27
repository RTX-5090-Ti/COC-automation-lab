from __future__ import annotations

import secrets

from screen_detector import BoundingBox


class TapPointError(Exception):
    """Raised when a safe tap point cannot be selected from a UI bounding box."""


def select_random_point_in_box(
    box: BoundingBox,
    screenshot_size: tuple[int, int],
    *,
    edge_padding_pixels: int = 10,
) -> tuple[int, int]:
    """Return a random tap coordinate inside a detected UI element."""
    screenshot_width, screenshot_height = screenshot_size
    if screenshot_width <= 0 or screenshot_height <= 0:
        raise TapPointError("Screenshot dimensions must be positive.")
    if edge_padding_pixels < 0:
        raise TapPointError("Tap edge padding must be non-negative.")
    if box.x < 0 or box.y < 0 or box.x + box.width > screenshot_width or box.y + box.height > screenshot_height:
        raise TapPointError("Template bounding box is outside the screenshot.")
    if box.width <= 0 or box.height <= 0:
        raise TapPointError("Template bounding box dimensions must be positive.")

    horizontal_padding = min(edge_padding_pixels, (box.width - 1) // 4)
    vertical_padding = min(edge_padding_pixels, (box.height - 1) // 4)
    min_x = box.x + horizontal_padding
    max_x = box.x + box.width - 1 - horizontal_padding
    min_y = box.y + vertical_padding
    max_y = box.y + box.height - 1 - vertical_padding
    if min_x > max_x or min_y > max_y:
        raise TapPointError("No safe tap area remains inside the template bounding box.")

    return (
        min_x + secrets.randbelow(max_x - min_x + 1),
        min_y + secrets.randbelow(max_y - min_y + 1),
    )
