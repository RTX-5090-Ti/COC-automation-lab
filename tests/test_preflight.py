from __future__ import annotations

from pathlib import Path

from adb_controller import ADBError, DeviceInfo
from api.app import create_app
from runtime.bot_runtime import BotRuntime
from runtime.preflight_service import PreflightService
from tests.conftest import request


class FakeADB:
    adb_path = Path("C:/fake/adb.exe")

    def __init__(self, *, foreground: bool = True) -> None:
        self.foreground = foreground

    def check_adb_available(self) -> bool:
        return True

    def list_devices(self) -> list[DeviceInfo]:
        return [DeviceInfo(serial="emulator-5554", status="device")]

    def select_device(self) -> DeviceInfo:
        return self.list_devices()[0]

    def get_installed_packages(self) -> list[str]:
        return ["com.supercell.clashofclans"]

    def is_app_running(self, _package: str) -> bool:
        return True

    def get_foreground_app(self) -> str | None:
        return "com.supercell.clashofclans" if self.foreground else "com.android.launcher"

    def capture_screenshot(self, output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake-image")
        return output


def _service(config_path: Path, runtime_dir: Path, *, adb_factory=FakeADB, foreground: bool = True, dimensions=(1920, 1080)) -> PreflightService:
    return PreflightService(
        config_path=config_path,
        runtime_data_dir=runtime_dir,
        adb_factory=(lambda: adb_factory(foreground=foreground)) if adb_factory is FakeADB else adb_factory,
        tesseract_resolver=lambda: Path(__file__),
        tesseract_checker=lambda _path: True,
        image_size_reader=lambda _path: dimensions,
        asset_paths=[Path(__file__)],
    )


def test_preflight_not_run_yet(config_path, tmp_path) -> None:
    runtime = BotRuntime(config_path=config_path)
    app = create_app(config_path=config_path, runtime=runtime, preflight_service=_service(config_path, tmp_path))
    assert request(app, "GET", "/api/preflight").json() == {"report": None}
    runtime.close()


def test_preflight_ready_report_and_api_shape(config_path, tmp_path) -> None:
    runtime = BotRuntime(config_path=config_path)
    service = _service(config_path, tmp_path)
    app = create_app(config_path=config_path, runtime=runtime, preflight_service=service)
    response = request(app, "POST", "/api/preflight/run")
    report = response.json()
    assert response.status_code == 200
    assert report["overallStatus"] == "ready"
    assert {"id", "status", "title", "detail", "remediation", "metadata"} <= report["checks"][0].keys()
    assert request(app, "GET", "/api/preflight").json()["report"]["overallStatus"] == "ready"
    assert runtime.preflight_report()["overallStatus"] == "ready"
    runtime.close()


def test_preflight_blocks_missing_adb(config_path, tmp_path) -> None:
    def missing_adb():
        raise ADBError("ADB executable not found")

    report = _service(config_path, tmp_path, adb_factory=missing_adb).run()
    assert report["overallStatus"] == "blocked"
    assert next(check for check in report["checks"] if check["id"] == "adb")["status"] == "fail"


def test_preflight_blocks_invalid_config(config_path, tmp_path) -> None:
    config_path.write_text("{broken", encoding="utf-8")
    report = _service(config_path, tmp_path).run()
    assert report["overallStatus"] == "blocked"
    assert next(check for check in report["checks"] if check["id"] == "configuration")["status"] == "fail"


def test_preflight_warns_when_game_is_not_foreground(config_path, tmp_path) -> None:
    report = _service(config_path, tmp_path, foreground=False).run()
    assert report["overallStatus"] == "warning"
    assert next(check for check in report["checks"] if check["id"] == "clash_of_clans")["status"] == "warning"


def test_preflight_blocks_wrong_screenshot_dimensions(config_path, tmp_path) -> None:
    report = _service(config_path, tmp_path, dimensions=(1280, 720)).run()
    screenshot = next(check for check in report["checks"] if check["id"] == "screenshot")
    assert report["overallStatus"] == "blocked"
    assert screenshot["status"] == "fail"
    assert screenshot["metadata"]["width"] == 1280


def test_blocked_preflight_rejects_session_start(config_path, tmp_path) -> None:
    def missing_adb():
        raise ADBError("ADB executable not found")

    runtime = BotRuntime(config_path=config_path)
    service = _service(config_path, tmp_path, adb_factory=missing_adb)
    app = create_app(config_path=config_path, runtime=runtime, preflight_service=service)
    assert request(app, "POST", "/api/preflight/run").json()["overallStatus"] == "blocked"
    response = request(app, "POST", "/api/session/start")
    assert response.status_code == 409
    assert "Preflight is blocked" in response.json()["detail"]
    runtime.close()
