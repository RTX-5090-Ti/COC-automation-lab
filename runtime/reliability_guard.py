"""Reusable read-before-action safety checks for emulator automation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from adb_controller import ADBController, ADBError
from project_paths import CURRENT_SCREENSHOT_PATH, DEBUG_DIRECTORY
from runtime.runtime_control import RuntimeControl
from screen_detector import ScreenDetectionResult, ScreenState, detect_screen


class FailureCode:
    ADB_DISCONNECTED = "ADB_DISCONNECTED"
    ADB_COMMAND_FAILED = "ADB_COMMAND_FAILED"
    GAME_NOT_FOREGROUND = "GAME_NOT_FOREGROUND"
    UNEXPECTED_SCREEN_STATE = "UNEXPECTED_SCREEN_STATE"
    UNKNOWN_SCREEN_EXHAUSTED = "UNKNOWN_SCREEN_EXHAUSTED"
    SCREEN_TIMEOUT = "SCREEN_TIMEOUT"
    SCREENSHOT_FAILED = "SCREENSHOT_FAILED"


class ReliabilityError(RuntimeError):
    def __init__(self, code: str, message: str, *, expected: Iterable[ScreenState] = (), observed: ScreenState | None = None, diagnostic_path: Path | None = None) -> None:
        super().__init__(message)
        self.failure_code = code
        self.failure_message = message
        self.expected_states = [state.value for state in expected]
        self.observed_state = observed.value if observed else None
        self.diagnostic_screenshot_path = str(diagnostic_path) if diagnostic_path else None


class ReliabilityGuard:
    """Fresh screenshot, foreground, and expected-state checks without recovery taps."""

    def __init__(self, adb: ADBController, package_name: str, threshold: float, unknown_retries: int, control: RuntimeControl) -> None:
        self.adb = adb
        self.package_name = package_name
        self.threshold = threshold
        self.unknown_retries = unknown_retries
        self.control = control

    def require_expected_state(self, expected: Iterable[ScreenState], phase: str) -> ScreenDetectionResult:
        expected = tuple(expected)
        for attempt in range(self.unknown_retries + 1):
            self.control.checkpoint(phase)
            self._ensure_connected_and_foreground()
            try:
                self.adb.capture_screenshot(CURRENT_SCREENSHOT_PATH)
            except ADBError as error:
                raise ReliabilityError(FailureCode.SCREENSHOT_FAILED, str(error), expected=expected) from error
            detection = detect_screen(CURRENT_SCREENSHOT_PATH, threshold=self.threshold, debug_directory=DEBUG_DIRECTORY)
            self.control.report(gameScreen=detection.state.value, screenConfidence=detection.confidence, screenDetails={"template": detection.matched_template_name}, screenshotPath=str(CURRENT_SCREENSHOT_PATH))
            if detection.state in expected:
                return detection
            if detection.state is not ScreenState.UNKNOWN:
                raise self._failure(FailureCode.UNEXPECTED_SCREEN_STATE, expected, detection.state)
            if attempt == self.unknown_retries:
                raise self._failure(FailureCode.UNKNOWN_SCREEN_EXHAUSTED, expected, detection.state)
        raise self._failure(FailureCode.SCREEN_TIMEOUT, expected, None)

    def verify_device_and_foreground(self, phase: str) -> None:
        self.control.checkpoint(phase)
        self._ensure_connected_and_foreground()

    def _ensure_connected_and_foreground(self) -> None:
        try:
            self.adb.select_device(self.adb.device_id)
        except ADBError as error:
            raise ReliabilityError(FailureCode.ADB_DISCONNECTED, str(error)) from error
        try:
            foreground = self.adb.get_foreground_app()
        except ADBError as error:
            raise ReliabilityError(FailureCode.ADB_COMMAND_FAILED, str(error)) from error
        if foreground != self.package_name:
            raise ReliabilityError(FailureCode.GAME_NOT_FOREGROUND, f"Clash of Clans is not foreground: {foreground or 'unknown'}")

    def _failure(self, code: str, expected: tuple[ScreenState, ...], observed: ScreenState | None) -> ReliabilityError:
        diagnostic = self._save_diagnostic()
        message = f"Expected {', '.join(state.value for state in expected)}; observed {(observed.value if observed else 'none')}."
        self.control.report(failureCode=code, failureMessage=message, expectedStates=[state.value for state in expected], observedState=observed.value if observed else None, diagnosticScreenshotPath=str(diagnostic) if diagnostic else None)
        self.control.log(logging.ERROR, f"{code}: {message}")
        return ReliabilityError(code, message, expected=expected, observed=observed, diagnostic_path=diagnostic)

    @staticmethod
    def _save_diagnostic() -> Path | None:
        if not CURRENT_SCREENSHOT_PATH.is_file():
            return None
        DEBUG_DIRECTORY.mkdir(parents=True, exist_ok=True)
        path = DEBUG_DIRECTORY / f"reliability_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.png"
        path.write_bytes(CURRENT_SCREENSHOT_PATH.read_bytes())
        return path
