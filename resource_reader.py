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
RESOURCE_ROIS_DEBUG_PATH = Path("screenshots/debug/resource_rois.png")


class ResourceReaderError(Exception):
    """Raised when resource-reading setup is invalid."""


@dataclass(frozen=True)
class FixedResourceROIConfig:
    """Fixed resource-number ROI for the standard 1920 x 1080 battle layout."""

    name: str
    roi: BoundingBox
    raw_debug_path: Path
    processed_debug_path: Path
    ocr_psm: int
    minimum_digits: int = 1


@dataclass(frozen=True)
class ResourceReading:
    """OCR outcome for one resource."""

    value: int | None
    icon_confidence: float
    icon_bounding_box: BoundingBox | None
    roi_bounding_box: BoundingBox | None
    raw_ocr_text: str
    success: bool
    frame_values: tuple[int | None, ...] = ()
    failure_reason: str | None = None


@dataclass(frozen=True)
class ResourceReadResult:
    """Combined OCR outcome."""

    gold: ResourceReading
    elixir: ResourceReading
    dark_elixir: ResourceReading
    overall_success: bool


REFERENCE_SCREENSHOT_SIZE = (1920, 1080)


RESOURCE_CONFIGS: tuple[FixedResourceROIConfig, ...] = (
    FixedResourceROIConfig(
        name="gold",
        roi=BoundingBox(x=90, y=144, width=190, height=46),
        raw_debug_path=OCR_DEBUG_DIRECTORY / "gold_raw.png",
        processed_debug_path=OCR_DEBUG_DIRECTORY / "gold_processed.png",
        ocr_psm=8,
        minimum_digits=4,
    ),
    FixedResourceROIConfig(
        name="elixir",
        roi=BoundingBox(x=90, y=202, width=190, height=46),
        raw_debug_path=OCR_DEBUG_DIRECTORY / "elixir_raw.png",
        processed_debug_path=OCR_DEBUG_DIRECTORY / "elixir_processed.png",
        # The elixir row's rounded glyphs are recognized more reliably as a single word.
        ocr_psm=10,
        minimum_digits=4,
    ),
    FixedResourceROIConfig(
        name="dark_elixir",
        roi=BoundingBox(x=90, y=264, width=170, height=45),
        raw_debug_path=OCR_DEBUG_DIRECTORY / "dark_elixir_raw.png",
        processed_debug_path=OCR_DEBUG_DIRECTORY / "dark_elixir_processed.png",
        ocr_psm=7,
        minimum_digits=3,
    ),
)


class ResourceReader:
    """Reads fixed enemy-base resource rows using one OCR pass per resource."""

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
        self._validate_screenshot_size(screenshot)
        OCR_DEBUG_DIRECTORY.mkdir(parents=True, exist_ok=True)
        self._save_roi_debug_image(screenshot)

        readings: dict[str, ResourceReading] = {}
        for config in RESOURCE_CONFIGS:
            readings[config.name] = self._read_single_resource(screenshot, config)

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
        config: FixedResourceROIConfig,
    ) -> ResourceReading:
        roi_box = config.roi
        raw_roi = screenshot[roi_box.y : roi_box.y + roi_box.height, roi_box.x : roi_box.x + roi_box.width]
        cv2.imwrite(str(config.raw_debug_path), raw_roi)

        processed_roi = self._crop_to_digits(self._preprocess_roi(raw_roi))
        cv2.imwrite(str(config.processed_debug_path), processed_roi)

        raw_text, parsed_value = self._extract_best_ocr_value(config)
        return ResourceReading(
            value=parsed_value,
            icon_confidence=1.0,
            icon_bounding_box=None,
            roi_bounding_box=roi_box,
            raw_ocr_text=raw_text,
            success=parsed_value is not None,
            frame_values=(parsed_value,),
            failure_reason=None if parsed_value is not None else f"{config.name} OCR failed",
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
    def _validate_screenshot_size(screenshot: cv2.typing.MatLike) -> None:
        height, width = screenshot.shape[:2]
        if (width, height) != REFERENCE_SCREENSHOT_SIZE:
            raise ResourceReaderError(
                "Fixed resource ROIs require a 1920 x 1080 screenshot. "
                f"Received {width} x {height}."
            )

    @staticmethod
    def _preprocess_roi(raw_roi: cv2.typing.MatLike) -> cv2.typing.MatLike:
        # The three loot rows use white digits over colored game artwork.
        hsv = cv2.cvtColor(raw_roi, cv2.COLOR_BGR2HSV)
        digits = cv2.inRange(hsv, (0, 0, 150), (180, 115, 255))
        return cv2.resize(digits, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)

    @staticmethod
    def _crop_to_digits(processed_roi: cv2.typing.MatLike) -> cv2.typing.MatLike:
        digit_pixels = cv2.findNonZero(processed_roi)
        if digit_pixels is None:
            return processed_roi
        x, y, width, height = cv2.boundingRect(digit_pixels)
        return processed_roi[y : y + height, x : x + width]

    @staticmethod
    def _save_roi_debug_image(screenshot: cv2.typing.MatLike) -> None:
        colors = {
            "gold": (0, 215, 255),
            "elixir": (255, 0, 255),
            "dark_elixir": (80, 80, 80),
        }
        annotated = screenshot.copy()
        for config in RESOURCE_CONFIGS:
            roi = config.roi
            color = colors[config.name]
            cv2.rectangle(
                annotated,
                (roi.x, roi.y),
                (roi.x + roi.width, roi.y + roi.height),
                color,
                2,
            )
            cv2.putText(
                annotated,
                config.name,
                (roi.x, max(24, roi.y - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                color,
                2,
                cv2.LINE_AA,
            )
        RESOURCE_ROIS_DEBUG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(RESOURCE_ROIS_DEBUG_PATH), annotated):
            raise ResourceReaderError(f"Could not save resource ROI debug image: {RESOURCE_ROIS_DEBUG_PATH}")

    def _extract_best_ocr_value(self, config: FixedResourceROIConfig) -> tuple[str, int | None]:
        raw_text = self._run_ocr(config.processed_debug_path, psm=config.ocr_psm)
        parsed_value = self._parse_numeric_text(raw_text)
        digit_count = len("".join(re.findall(r"\d+", raw_text)))
        if parsed_value is None or digit_count < config.minimum_digits:
            return raw_text, None
        return raw_text, parsed_value

    def _run_ocr(self, image_path: Path, *, psm: int = 7) -> str:
        command = [
            str(self.tesseract_path),
            str(image_path),
            "stdout",
            "--psm",
            str(psm),
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
