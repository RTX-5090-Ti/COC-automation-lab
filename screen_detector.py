from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import cv2


class ScreenDetectionError(Exception):
    """Raised when screen detection inputs or outputs are invalid."""


class ScreenState(str, Enum):
    """Known game screens supported in the current milestone."""

    HOME = "HOME"
    ATTACK_MENU = "ATTACK_MENU"
    ARMY_CONFIRMATION = "ARMY_CONFIRMATION"
    ENEMY_BASE = "ENEMY_BASE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class BoundingBox:
    """Bounding box for a matched template."""

    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class ScreenTemplate:
    """Template registration for one known screen."""

    state: ScreenState
    template_path: Path
    template_name: str
    action_template_path: Path | None = None
    action_template_name: str | None = None


@dataclass(frozen=True)
class ScreenDetectionResult:
    """Best detection result for the current screenshot."""

    state: ScreenState
    confidence: float
    matched_template_name: str | None
    bounding_box: BoundingBox | None
    center: tuple[int, int] | None
    screenshot_size: tuple[int, int]
    action_template_name: str | None = None
    action_confidence: float | None = None
    action_bounding_box: BoundingBox | None = None
    action_center: tuple[int, int] | None = None
    debug_image_path: Path | None = None
    best_candidate_confidence: float | None = None


REGISTERED_TEMPLATES: tuple[ScreenTemplate, ...] = (
    ScreenTemplate(
        state=ScreenState.HOME,
        template_path=Path("templates/home/attack_button.png"),
        template_name="attack_button.png",
    ),
    ScreenTemplate(
        state=ScreenState.ATTACK_MENU,
        template_path=Path("templates/attack_menu/find_match_button.png"),
        template_name="find_match_button.png",
    ),
    ScreenTemplate(
        state=ScreenState.ARMY_CONFIRMATION,
        template_path=Path("templates/army_confirmation/army_panel_anchor.png"),
        template_name="army_panel_anchor.png",
        action_template_path=Path("templates/army_confirmation/confirm_attack_button.png"),
        action_template_name="confirm_attack_button.png",
    ),
    ScreenTemplate(
        state=ScreenState.ENEMY_BASE,
        template_path=Path("templates/enemy_base/next_button.png"),
        template_name="next_button.png",
    ),
)


def detect_screen(
    screenshot_path: str | Path,
    *,
    threshold: float = 0.85,
    debug_directory: str | Path = Path("screenshots/debug"),
) -> ScreenDetectionResult:
    screenshot_file = Path(screenshot_path)
    screenshot = _load_image(screenshot_file, "screenshot")
    screenshot_height, screenshot_width = screenshot.shape[:2]
    screenshot_size = (screenshot_width, screenshot_height)

    best_valid_match: ScreenDetectionResult | None = None
    best_candidate: ScreenDetectionResult | None = None

    for screen_template in REGISTERED_TEMPLATES:
        template = _load_image(screen_template.template_path, f"template {screen_template.template_name}")
        result = _match_template(screenshot, template, screen_template)

        if best_candidate is None or result.confidence > best_candidate.confidence:
            best_candidate = result

        if result.confidence >= threshold and (
            best_valid_match is None or result.confidence > best_valid_match.confidence
        ):
            best_valid_match = result

    if best_candidate is None:
        raise ScreenDetectionError("No screen templates are registered.")

    if best_valid_match is None:
        unknown_path = _save_unknown_screenshot(screenshot_file, Path(debug_directory))
        return ScreenDetectionResult(
            state=ScreenState.UNKNOWN,
            confidence=0.0,
            matched_template_name=None,
            bounding_box=None,
            center=None,
            screenshot_size=screenshot_size,
            debug_image_path=unknown_path,
            best_candidate_confidence=best_candidate.confidence,
        )

    debug_path = _save_match_debug_image(screenshot, best_valid_match, Path(debug_directory))
    return ScreenDetectionResult(
        state=best_valid_match.state,
        confidence=best_valid_match.confidence,
        matched_template_name=best_valid_match.matched_template_name,
        bounding_box=best_valid_match.bounding_box,
        center=best_valid_match.center,
        screenshot_size=screenshot_size,
        action_template_name=best_valid_match.action_template_name,
        action_confidence=best_valid_match.action_confidence,
        action_bounding_box=best_valid_match.action_bounding_box,
        action_center=best_valid_match.action_center,
        debug_image_path=debug_path,
        best_candidate_confidence=best_candidate.confidence,
    )


def _match_template(
    screenshot: cv2.typing.MatLike,
    template: cv2.typing.MatLike,
    screen_template: ScreenTemplate,
) -> ScreenDetectionResult:
    screenshot_height, screenshot_width = screenshot.shape[:2]
    template_height, template_width = template.shape[:2]

    if template_width > screenshot_width or template_height > screenshot_height:
        raise ScreenDetectionError(f"Template is larger than the screenshot: {screen_template.template_name}")

    match_result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
    _, max_confidence, _, max_location = cv2.minMaxLoc(match_result)

    x, y = max_location
    bounding_box = BoundingBox(x=x, y=y, width=template_width, height=template_height)
    center = (x + template_width // 2, y + template_height // 2)

    action_bounding_box: BoundingBox | None = None
    action_center: tuple[int, int] | None = None
    action_confidence: float | None = None

    if screen_template.action_template_path and screen_template.action_template_name:
        action_template = _load_image(
            screen_template.action_template_path,
            f"template {screen_template.action_template_name}",
        )
        action_confidence, action_bounding_box, action_center = _match_action_template(
            screenshot,
            action_template,
            screen_template,
        )

    return ScreenDetectionResult(
        state=screen_template.state,
        confidence=float(max_confidence),
        matched_template_name=screen_template.template_name,
        bounding_box=bounding_box,
        center=center,
        screenshot_size=(screenshot_width, screenshot_height),
        action_template_name=screen_template.action_template_name,
        action_confidence=action_confidence,
        action_bounding_box=action_bounding_box,
        action_center=action_center,
    )


def _match_action_template(
    screenshot: cv2.typing.MatLike,
    template: cv2.typing.MatLike,
    screen_template: ScreenTemplate,
) -> tuple[float, BoundingBox, tuple[int, int]]:
    screenshot_height, screenshot_width = screenshot.shape[:2]
    template_height, template_width = template.shape[:2]

    if template_width > screenshot_width or template_height > screenshot_height:
        raise ScreenDetectionError(f"Template is larger than the screenshot: {screen_template.action_template_name}")

    match_result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
    _, max_confidence, _, max_location = cv2.minMaxLoc(match_result)

    x, y = max_location
    bounding_box = BoundingBox(x=x, y=y, width=template_width, height=template_height)
    center = (x + template_width // 2, y + template_height // 2)
    return float(max_confidence), bounding_box, center


def _load_image(path: Path, label: str) -> cv2.typing.MatLike:
    if not path.is_file():
        raise ScreenDetectionError(f"{label.capitalize()} file does not exist: {path}")

    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ScreenDetectionError(f"{label.capitalize()} file could not be decoded: {path}")

    return image


def _save_match_debug_image(
    screenshot: cv2.typing.MatLike,
    detection: ScreenDetectionResult,
    debug_directory: Path,
) -> Path:
    if detection.bounding_box is None:
        raise ScreenDetectionError("Cannot save match debug image without a bounding box.")

    debug_directory.mkdir(parents=True, exist_ok=True)
    output_path = debug_directory / f"{detection.state.value.lower()}_detection.png"

    annotated = screenshot.copy()
    matched_box = detection.bounding_box
    top_left = (matched_box.x, matched_box.y)
    bottom_right = (matched_box.x + matched_box.width, matched_box.y + matched_box.height)
    cv2.rectangle(annotated, top_left, bottom_right, (0, 255, 0), 2)

    label = f"{detection.state.value} {detection.confidence:.2f}"
    cv2.putText(
        annotated,
        label,
        (matched_box.x, max(25, matched_box.y - 10)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )

    if detection.action_bounding_box is not None:
        action_box = detection.action_bounding_box
        action_top_left = (action_box.x, action_box.y)
        action_bottom_right = (action_box.x + action_box.width, action_box.y + action_box.height)
        cv2.rectangle(annotated, action_top_left, action_bottom_right, (255, 255, 0), 2)

    if not cv2.imwrite(str(output_path), annotated):
        raise ScreenDetectionError(f"Debug image could not be saved: {output_path}")

    return output_path


def _save_unknown_screenshot(screenshot_path: Path, debug_directory: Path) -> Path:
    debug_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = debug_directory / f"unknown_{timestamp}.png"
    output_path.write_bytes(screenshot_path.read_bytes())
    return output_path
