"""Exercise Start with the generated portable defaults and real input guards."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from adb_controller import ADBController, DeviceInfo
from api.app import create_app
from runtime.bot_runtime import BotRuntime
from runtime.history_store import HistoryStore
from screen_detector import BoundingBox, ScreenDetectionResult, ScreenState
from tests.conftest import request, wait_for


def test_portable_start_sends_no_gameplay_input(tmp_path, monkeypatch):
    generated = Path(__file__).resolve().parents[1] / "build/portable-config/bot_config.json"
    if not generated.exists():
        pytest.skip("Run npm run backend:bundle before portable integration tests")
    data = json.loads(generated.read_text(encoding="utf-8"))
    assert data["dryRun"] is True
    profile = tmp_path / "AppData" / "CoC Field Console"
    profile.mkdir(parents=True)
    config_path = profile / "bot_config.json"
    config_path.write_bytes(generated.read_bytes())

    adb = object.__new__(ADBController)
    adb.gameplay_input_allowed = True
    adb.suppressed_gameplay_actions = []
    adb.check_adb_available = Mock()
    adb.select_device = Mock(return_value=DeviceInfo("test-device", "device"))
    adb.get_installed_packages = Mock(return_value=["com.supercell.clashofclans"])
    adb.get_foreground_app = Mock(return_value="com.supercell.clashofclans")
    adb.is_app_running = Mock(return_value=True)
    adb.capture_screenshot = Mock()
    monkeypatch.setattr("runtime.bot_runtime.ADBController", lambda: adb)
    detection = ScreenDetectionResult(ScreenState.BUILDER_HOME, 1.0, "fake", BoundingBox(10, 10, 100, 100), (60, 60), (1920, 1080))
    monkeypatch.setattr("builder_base_flow_controller.detect_screen", lambda *a, **k: detection)
    # Any command escaping the real ADB input guard fails this test.
    monkeypatch.setattr("adb_controller.subprocess.run", Mock(side_effect=AssertionError("Unexpected ADB subprocess")))
    runtime = BotRuntime(config_path=config_path, history_store=HistoryStore(profile))
    app = create_app(runtime=runtime, config_path=config_path)
    try:
        assert request(app, "POST", "/api/session/start").status_code == 200
        wait_for(lambda: runtime.telemetry()["terminalResult"] is not None)
        assert runtime.telemetry()["terminalResult"] == "completed"
        assert runtime.telemetry()["dryRun"] is True
        assert adb.gameplay_input_allowed is False
        adb.tap(10, 20)
        adb.swipe(1, 2, 3, 4)
        adb.run_command(["shell", "input", "keyevent", "4"])
        assert len(adb.suppressed_gameplay_actions) == 3
    finally:
        runtime.close()
