from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2

from screen_detector import BoundingBox


DEFAULT_TESSERACT_PATH = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
OCR_DEBUG_DIRECTORY = Path("screenshots/debug/ocr")


class ResourceReaderError(Exception):
    """Raised when resource-reading setup is invalid."""


@dataclass(frozen=True)
class ResourceROIConfig:
    """ROI offsets and size relative to one detected icon."""

    offset_x: int
    offset_y: int
    width: int
    height: int


@dataclass(frozen=True)
class ResourceIconConfig:
    """Template and ROI configuration for one resource."""

    name: str
    template_path: Path
    roi: ResourceROIConfig
    raw_debug_path: Path
    processed_debug_path: Path


@dataclass(frozen=True)
class ResourceReading:
    """OCR outcome for one resource."""

    value: int | None
    icon_confidence: float
    icon_bounding_box: BoundingBox | None
    roi_bounding_box: BoundingBox | None
    raw_ocr_text: str
    success: bool


@dataclass(frozen=True)
class ResourceReadResult:
    """Combined resource-reading outcome."""

    gold: ResourceReading
    elixir: ResourceReading
    dark_elixir: ResourceReading
    overall_success: bool


RESOURCE_CONFIGS: tuple[ResourceIconConfig, ...] = (
    ResourceIconConfig(
        name="gold",
        template_path=Path("templates/enemy_base/gold_icon.png"),
        roi=ResourceROIConfig(offset_x=30, offset_y=-6, width=220, height=44),
        raw_debug_path=OCR_DEBUG_DIRECTORY / "gold_raw.png",
        processed_debug_path=OCR_DEBUG_DIRECTORY / "gold_processed.png",
    ),
    ResourceIconConfig(
        name="elixir",
        template_path=Path("templates/enemy_base/elixir_icon.png"),
        roi=ResourceROIConfig(offset_x=30, offset_y=-6, width=220, height=44),
        raw_debug_path=OCR_DEBUG_DIRECTORY / "elixir_raw.png",
        processed_debug_path=OCR_DEBUG_DIRECTORY / "elixir_processed.png",
    ),
    ResourceIconConfig(
        name="dark_elixir",
        template_path=Path("templates/enemy_base/dark_elixir_icon.png"),
        roi=ResourceROIConfig(offset_x=30, offset_y=-6, width=180, height=40),
        raw_debug_path=OCR_DEBUG_DIRECTORY / "dark_elixir_raw.png",
        processed_debug_path=OCR_DEBUG_DIRECTORY / "dark_elixir_processed.png",
    ),
)


class ResourceReader:
    """Reads enemy-base resources from one screenshot using icon matching + OCR."""

    def __init__(self, tesseract_path: str | Path | None = None) -> None:
        self.tesseract_path = self._resolve_tesseract_path(tesseract_path)

    @classmethod
    def _resolve_tesseract_path(cls, tesseract_path: str | Path | None = None) -> Path:
        if tesseract_path:
            candidate = Path(tesseract_path).expanduser()
            if candidate.is_file():
                return candidate
            raise ResourceReaderError(f"Tesseract executable not found at configured path: {candidate}")

        env_path = os.getenv("TESSERACT_PATH")
        if env_path:
            candidate = Path(env_path).expanduser()
            if candidate.is_file():
                return candidate

        detected = shutil.which("tesseract")
        if detected:
            return Path(detected)

        if DEFAULT_TESSERACT_PATH.is_file():
            return DEFAULT_TESSERACT_PATH

        raise ResourceReaderError(
            "Tesseract executable not found. Install Tesseract, add it to PATH, "
            "or set the TESSERACT_PATH environment variable."
        )

    def read_resources(self, screenshot_path: str | Path, *, threshold: float = 0.85) -> ResourceReadResult:
        screenshot_file = Path(screenshot_path)
        screenshot = self._load_image(screenshot_file, "screenshot")
        OCR_DEBUG_DIRECTORY.mkdir(parents=True, exist_ok=True)

        readings: dict[str, ResourceReading] = {}
        for config in RESOURCE_CONFIGS:
            readings[config.name] = self._read_single_resource(screenshot, config, threshold)

        overall_success = all(reading.success for reading in readings.values())
        return ResourceReadResult(
            gold=readings["gold"],
            elixir=readings["elixir"],
            dark_elixir=readings["dark_elixir"],
            overall_success=overall_success,
        )

    def _read_single_resource(
        self,
        screenshot: cv2.typing.MatLike,
        config: ResourceIconConfig,
        threshold: float,
    ) -> ResourceReading:
        template = self._load_image(config.template_path, f"{config.name} template")
        icon_confidence, icon_box = self._match_icon(screenshot, template)

        if icon_confidence < threshold:
            return ResourceReading(
                value=None,
                icon_confidence=icon_confidence,
                icon_bounding_box=icon_box,
                roi_bounding_box=None,
                raw_ocr_text="",
                success=False,
            )

        roi_box = self._compute_roi(icon_box, config.roi, screenshot.shape[1], screenshot.shape[0], config.name)
        raw_roi = screenshot[roi_box.y : roi_box.y + roi_box.height, roi_box.x : roi_box.x + roi_box.width]
        cv2.imwrite(str(config.raw_debug_path), raw_roi)

        processed_roi = self._preprocess_roi(raw_roi)
        cv2.imwrite(str(config.processed_debug_path), processed_roi)

        raw_text = self._run_ocr(config.processed_debug_path)
        parsed_value = self._parse_numeric_text(raw_text)
        return ResourceReading(
            value=parsed_value,
            icon_confidence=icon_confidence,
            icon_bounding_box=icon_box,
            roi_bounding_box=roi_box,
            raw_ocr_text=raw_text,
            success=parsed_value is not None,
        )

    @staticmethod
    def _load_image(path: Path, label: str) -> cv2.typing.MatLike:
        if not path.is_file():
            raise ResourceReaderError(f"{label.capitalize()} file does not exist: {path}")

        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise ResourceReaderError(f"{label.capitalize()} file could not be decoded: {path}")
        return image

    @staticmethod
    def _match_icon(screenshot: cv2.typing.MatLike, template: cv2.typing.MatLike) -> tuple[float, BoundingBox]:
        result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
        _, max_confidence, _, max_location = cv2.minMaxLoc(result)
        x, y = max_location
        height, width = template.shape[:2]
        return float(max_confidence), BoundingBox(x=x, y=y, width=width, height=height)

    @staticmethod
    def _compute_roi(
        icon_box: BoundingBox,
        roi: ResourceROIConfig,
        screenshot_width: int,
        screenshot_height: int,
        resource_name: str,
    ) -> BoundingBox:
        x = icon_box.x + roi.offset_x
        y = icon_box.y + roi.offset_y

        if x < 0 or y < 0:
            raise ResourceReaderError(f"{resource_name} ROI starts outside the screenshot.")
        if x + roi.width > screenshot_width or y + roi.height > screenshot_height:
            raise ResourceReaderError(f"{resource_name} ROI exceeds screenshot bounds.")

        return BoundingBox(x=x, y=y, width=roi.width, height=roi.height)

    @staticmethod
    def _preprocess_roi(raw_roi: cv2.typing.MatLike) -> cv2.typing.MatLike:
        grayscale = cv2.cvtColor(raw_roi, cv2.COLOR_BGR2GRAY)
        enlarged = cv2.resize(grayscale, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        blurred = cv2.GaussianBlur(enlarged, (3, 3), 0)
        _, thresholded = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return thresholded

    def _run_ocr(self, processed_image_path: Path) -> str:
        command = [
            str(self.tesseract_path),
            str(processed_image_path),
            "stdout",
            "--psm",
            "7",
            "-c",
            "tessedit_char_whitelist=0123456789",
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except FileNotFoundError as error:
            raise ResourceReaderError(f"Tesseract executable could not be launched: {self.tesseract_path}") from error
        except subprocess.TimeoutExpired as error:
            raise ResourceReaderError("Tesseract OCR command timed out.") from error

        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise ResourceReaderError(f"Tesseract OCR command failed: {stderr or 'unknown error'}")

        return result.stdout.strip()

    @staticmethod
    def _parse_numeric_text(raw_text: str) -> int | None:
        digits_only = "".join(re.findall(r"\d+", raw_text))
        if not digits_only:
            return None
        return int(digits_only)
