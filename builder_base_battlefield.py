from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import random

import cv2

from decision_engine import BotConfig


@dataclass(frozen=True)
class BuilderDeploymentPoint:
    edge: str
    sequence_number: int
    x: int
    y: int


def builder_battlefield_polygon(screenshot_size: tuple[int, int], config: BotConfig) -> list[tuple[int, int]]:
    """Build the manually configurable A-B-C-D Builder Base boundary."""
    width, height = screenshot_size
    return [
        (round(width * config.builder_battlefield_a_x_ratio), round(height * config.builder_battlefield_a_y_ratio)),
        (round(width * config.builder_battlefield_b_x_ratio), round(height * config.builder_battlefield_b_y_ratio)),
        (round(width * config.builder_battlefield_c_x_ratio), round(height * config.builder_battlefield_c_y_ratio)),
        (round(width * config.builder_battlefield_d_x_ratio), round(height * config.builder_battlefield_d_y_ratio)),
    ]


def builder_deployment_points(screenshot_size: tuple[int, int], config: BotConfig) -> list[BuilderDeploymentPoint]:
    """Spread fixed points just outside the visible Builder Base ROI segments."""
    a, b, c, d = builder_battlefield_polygon(screenshot_size, config)
    e = _interpolate(d, a, config.builder_boundary_de_length_ratio)
    f = _interpolate(b, a, config.builder_boundary_bf_length_ratio)
    g = _interpolate(b, c, config.builder_boundary_bg_length_ratio)
    h = _interpolate(d, c, config.builder_boundary_dh_length_ratio)
    center = (sum(point[0] for point in (a, b, c, d)) / 4, sum(point[1] for point in (a, b, c, d)) / 4)
    edges = (
        ("D-E", d, e, config.builder_deployment_points_de),
        ("B-F", b, f, config.builder_deployment_points_bf),
        ("B-G", b, g, config.builder_deployment_points_bg),
        ("D-H", d, h, config.builder_deployment_points_dh),
    )
    points: list[BuilderDeploymentPoint] = []
    total_point_count = sum(count for _, _, _, count in edges)
    random_count = math.ceil(total_point_count * config.deployment_edge_inset_random_ratio)
    randomized_indices = set(random.sample(range(total_point_count), random_count))
    sequence_number = 1
    for edge, start, end, count in edges:
        for index in range(1, count + 1):
            inset_pixels = (
                random.choice(config.deployment_edge_inset_random_pixels)
                if sequence_number - 1 in randomized_indices
                else config.builder_deployment_edge_inset_pixels
            )
            x, y = _offset_outside(
                _interpolate(start, end, index / (count + 1)),
                center,
                inset_pixels,
            )
            points.append(BuilderDeploymentPoint(edge, sequence_number, x, y))
            sequence_number += 1
    return points


def save_builder_battlefield_roi_debug(screenshot_path: str | Path, output_path: str | Path, config: BotConfig) -> Path:
    """Draw the Builder Base boundary without creating deployment points."""
    source = Path(screenshot_path)
    output = Path(output_path)
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Builder Base screenshot could not be decoded: {source}")

    height, width = image.shape[:2]
    points = builder_battlefield_polygon((width, height), config)
    a, b, c, d = points
    # D-E replaces the full D-A side so the usable Builder Base edge is manually tunable.
    e = (
        round(d[0] + (a[0] - d[0]) * config.builder_boundary_de_length_ratio),
        round(d[1] + (a[1] - d[1]) * config.builder_boundary_de_length_ratio),
    )
    f = (
        round(b[0] + (a[0] - b[0]) * config.builder_boundary_bf_length_ratio),
        round(b[1] + (a[1] - b[1]) * config.builder_boundary_bf_length_ratio),
    )
    g = (
        round(b[0] + (c[0] - b[0]) * config.builder_boundary_bg_length_ratio),
        round(b[1] + (c[1] - b[1]) * config.builder_boundary_bg_length_ratio),
    )
    h = (
        round(d[0] + (c[0] - d[0]) * config.builder_boundary_dh_length_ratio),
        round(d[1] + (c[1] - d[1]) * config.builder_boundary_dh_length_ratio),
    )
    for start, end in ((d, e), (b, f), (b, g), (d, h)):
        cv2.line(image, start, end, (255, 0, 0), 6, cv2.LINE_AA)
    label_x, label_y = points[1]
    cv2.putText(image, "Battlefield ROI", (label_x - 175, max(30, label_y - 15)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 0), 2, cv2.LINE_AA)
    for label, point in (("E", e), ("F", f), ("G", g), ("H", h)):
        cv2.putText(image, label, (point[0] + 10, point[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2, cv2.LINE_AA)
    for point in builder_deployment_points((width, height), config):
        cv2.circle(image, (point.x, point.y), 9, (0, 0, 255), -1)
        cv2.putText(image, str(point.sequence_number), (point.x + 11, point.y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2, cv2.LINE_AA)

    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), image):
        raise ValueError(f"Builder Base battlefield ROI debug image could not be saved: {output}")
    return output


def _interpolate(start: tuple[int, int], end: tuple[int, int], ratio: float) -> tuple[int, int]:
    return (round(start[0] + (end[0] - start[0]) * ratio), round(start[1] + (end[1] - start[1]) * ratio))


def _offset_outside(point: tuple[int, int], center: tuple[float, float], inset_pixels: int) -> tuple[int, int]:
    dx, dy = point[0] - center[0], point[1] - center[1]
    distance = math.hypot(dx, dy)
    if distance == 0 or inset_pixels == 0:
        return point
    return (round(point[0] + dx / distance * inset_pixels), round(point[1] + dy / distance * inset_pixels))
