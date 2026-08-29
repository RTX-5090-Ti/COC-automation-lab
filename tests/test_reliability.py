from __future__ import annotations

from pathlib import Path

import pytest

from adb_controller import ADBController
from runtime.reliability_guard import FailureCode, ReliabilityError, ReliabilityGuard
from runtime.runtime_control import RuntimeControl
from screen_detector import ScreenDetectionResult, ScreenState


def _detection(state: ScreenState) -> ScreenDetectionResult:
    return ScreenDetectionResult(state, 0.9, "fake.png", None, None, (1920, 1080))


class FakeADB:
    device_id = "emulator-5554"
    def select_device(self, _preferred=None): return object()
    def get_foreground_app(self): return "com.supercell.clashofclans"
    def capture_screenshot(self, path): Path(path).parent.mkdir(parents=True, exist_ok=True); Path(path).write_bytes(b"fake")


def test_dry_run_suppresses_low_level_tap_and_swipe() -> None:
    adb = object.__new__(ADBController)
    adb.gameplay_input_allowed = False
    adb.suppressed_gameplay_actions = []
    assert adb.tap(10, 20).returncode == 0
    assert adb.swipe(1, 2, 3, 4).returncode == 0
    assert adb.suppressed_gameplay_actions == ["tap (10, 20)", "swipe (1, 2) -> (3, 4)"]


def test_guard_rejects_unexpected_and_unknown_before_action(monkeypatch, tmp_path) -> None:
    import runtime.reliability_guard as module
    monkeypatch.setattr(module, "CURRENT_SCREENSHOT_PATH", tmp_path / "current.png")
    monkeypatch.setattr(module, "DEBUG_DIRECTORY", tmp_path / "debug")
    guard = ReliabilityGuard(FakeADB(), "com.supercell.clashofclans", 0.85, 1, RuntimeControl())
    monkeypatch.setattr(module, "detect_screen", lambda *_args, **_kwargs: _detection(ScreenState.HOME))
    with pytest.raises(ReliabilityError) as wrong:
        guard.require_expected_state((ScreenState.ENEMY_BASE,), "TEST")
    assert wrong.value.failure_code == FailureCode.UNEXPECTED_SCREEN_STATE
    monkeypatch.setattr(module, "detect_screen", lambda *_args, **_kwargs: _detection(ScreenState.UNKNOWN))
    with pytest.raises(ReliabilityError) as unknown:
        guard.require_expected_state((ScreenState.HOME,), "TEST")
    assert unknown.value.failure_code == FailureCode.UNKNOWN_SCREEN_EXHAUSTED


def test_guard_reports_foreground_loss() -> None:
    adb = FakeADB()
    adb.get_foreground_app = lambda: "com.android.launcher"
    guard = ReliabilityGuard(adb, "com.supercell.clashofclans", 0.85, 0, RuntimeControl())
    with pytest.raises(ReliabilityError) as failure:
        guard.verify_device_and_foreground("TEST")
    assert failure.value.failure_code == FailureCode.GAME_NOT_FOREGROUND
