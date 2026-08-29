"""Read-only environment checks for the local automation runtime."""

from __future__ import annotations

import logging
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from adb_controller import ADBController, ADBError, DeviceInfo
from decision_engine import DecisionEngineError, load_bot_config
from project_paths import DEBUG_DIRECTORY, RUNTIME_DATA_DIR, asset_path, dashboard_dist_path
from resource_reader import ResourceReader, ResourceReaderError


PACKAGE_NAME = "com.supercell.clashofclans"
EXPECTED_SCREENSHOT_SIZE = (1920, 1080)


class PreflightService:
    """Runs read-only checks and retains the latest result behind a lock."""

    def __init__(
        self,
        *,
        config_path: str | Path,
        runtime_data_dir: str | Path = RUNTIME_DATA_DIR,
        adb_factory: Callable[[], ADBController] = ADBController,
        tesseract_resolver: Callable[[], Path] | None = None,
        tesseract_checker: Callable[[Path], bool] | None = None,
        image_size_reader: Callable[[Path], tuple[int, int]] | None = None,
        asset_paths: list[Path] | None = None,
        log: Callable[[int, str], None] | None = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.runtime_data_dir = Path(runtime_data_dir)
        self.adb_factory = adb_factory
        self.tesseract_resolver = tesseract_resolver or ResourceReader._resolve_tesseract_path
        self.tesseract_checker = tesseract_checker or self._check_tesseract_executable
        self.image_size_reader = image_size_reader or self._read_image_size
        self.asset_paths = asset_paths
        self.log = log or logging.log
        self._lock = threading.RLock()
        self._latest: dict[str, Any] | None = None

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return None if self._latest is None else _copy_report(self._latest)

    def run(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        checks.append(self._check_runtime_data())
        checks.append(self._check_config())
        checks.append(self._check_assets())

        adb: ADBController | None = None
        device: DeviceInfo | None = None
        adb_check, adb, device = self._check_adb()
        checks.append(adb_check)
        if adb is not None and device is not None:
            checks.extend(self._check_game(adb, device))
        else:
            checks.append(_check("clash_of_clans", "warning", "Clash of Clans", "Skipped because no usable Android device is available.", "Connect and authorize one LDPlayer device, then run checks again."))

        checks.append(self._check_tesseract())
        if adb is not None and device is not None:
            checks.append(self._check_screenshot(adb))
        else:
            checks.append(_check("screenshot", "warning", "Screenshot capability", "Skipped because no usable Android device is available.", "Connect and authorize one LDPlayer device, then run checks again."))

        overall = "blocked" if any(item["status"] == "fail" for item in checks) else "warning" if any(item["status"] == "warning" for item in checks) else "ready"
        report = {"overallStatus": overall, "checkedAt": _timestamp(), "checks": checks}
        with self._lock:
            self._latest = report
        for check in checks:
            if check["status"] != "pass":
                level = logging.ERROR if check["status"] == "fail" else logging.WARNING
                self.log(level, f"Preflight {check['id']}: {check['detail']}")
        self.log(logging.INFO, f"Preflight completed with status: {overall}")
        return _copy_report(report)

    def _check_runtime_data(self) -> dict[str, Any]:
        try:
            current = self.runtime_data_dir / "screenshots" / "current"
            debug = self.runtime_data_dir / "screenshots" / "debug"
            current.mkdir(parents=True, exist_ok=True)
            debug.mkdir(parents=True, exist_ok=True)
            probe = self.runtime_data_dir / ".preflight-write-probe"
            probe.write_text("ok", encoding="ascii")
            probe.unlink()
            return _check("runtime_data", "pass", "Writable runtime data", "Runtime directories are writable.", "", {"runtimeDataDir": str(self.runtime_data_dir), "configPath": str(self.config_path)})
        except OSError as error:
            return _check("runtime_data", "fail", "Writable runtime data", f"Cannot write runtime data: {error}", "Check permissions for the CoC Field Console app-data folder.", {"runtimeDataDir": str(self.runtime_data_dir)})

    def _check_config(self) -> dict[str, Any]:
        if not self.config_path.is_file():
            return _check("configuration", "fail", "Configuration", "The runtime config file is missing.", "Restore bot_config.json from a backup or reinstall the desktop app; it will not be reset automatically.", {"configPath": str(self.config_path)})
        try:
            load_bot_config(self.config_path)
            with self.config_path.open("r+", encoding="utf-8"):
                pass
            return _check("configuration", "pass", "Configuration", "Configuration is valid and writable.", "", {"configPath": str(self.config_path)})
        except (DecisionEngineError, OSError) as error:
            return _check("configuration", "fail", "Configuration", f"Configuration is invalid or unreadable: {error}", "Fix the config values in the dashboard or restore a valid bot_config.json.", {"configPath": str(self.config_path)})

    def _check_assets(self) -> dict[str, Any]:
        required = self.asset_paths if self.asset_paths is not None else [
            asset_path("templates", "home", "attack_button.png"),
            asset_path("templates", "attack_menu", "find_match_button.png"),
            asset_path("templates", "army_confirmation", "army_panel_anchor.png"),
            asset_path("templates", "army_confirmation", "confirm_attack_button.png"),
            asset_path("templates", "enemy_base", "next_button.png"),
            asset_path("templates", "battle", "end_battle_button.png"),
            asset_path("templates", "battle", "surrender_button.png"),
            asset_path("templates", "battle", "end_battle_confirm_dialog.png"),
            asset_path("templates", "battle", "end_battle_confirm_ok.png"),
            asset_path("templates", "battle", "return_home_button.png"),
            asset_path("templates", "battle", "sneaky_goblin_slot.png"),
            dashboard_dist_path() / "index.html",
        ]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            return _check("bundled_assets", "fail", "Bundled assets", "Required templates or dashboard files are missing.", "Rebuild or reinstall the desktop package.", {"missingPaths": missing})
        return _check("bundled_assets", "pass", "Bundled assets", "Templates and dashboard assets are available.", "")

    def _check_adb(self) -> tuple[dict[str, Any], ADBController | None, DeviceInfo | None]:
        try:
            adb = self.adb_factory()
            adb.check_adb_available()
            devices = adb.list_devices()
        except ADBError as error:
            return _check("adb", "fail", "ADB and emulator", str(error), "Install LDPlayer14 or set ADB_PATH to a valid adb.exe."), None, None
        offline = [device.serial for device in devices if device.status in {"offline", "unauthorized"}]
        usable = [device for device in devices if device.status == "device"]
        if not usable:
            detail = "Connected devices need authorization: " + ", ".join(offline) if offline else "No connected Android device was found."
            return _check("adb", "fail", "ADB and emulator", detail, "Start LDPlayer and ensure its device appears as 'device' in ADB.", {"adbPath": str(adb.adb_path), "devices": [device.serial for device in devices]}), None, None
        try:
            selected = adb.select_device()
        except ADBError as error:
            return _check("adb", "fail", "ADB and emulator", str(error), "Connect only one emulator or configure the intended device selection.", {"adbPath": str(adb.adb_path)}), None, None
        return _check("adb", "pass", "ADB and emulator", "A usable Android device was found.", "", {"adbPath": str(adb.adb_path), "deviceSerial": selected.serial}), adb, selected

    def _check_game(self, adb: ADBController, device: DeviceInfo) -> list[dict[str, Any]]:
        try:
            installed = PACKAGE_NAME in adb.get_installed_packages()
            if not installed:
                return [_check("clash_of_clans", "fail", "Clash of Clans", "Clash of Clans is not installed on the selected device.", "Install Clash of Clans in LDPlayer, then run checks again.", {"deviceSerial": device.serial, "packageName": PACKAGE_NAME})]
            running = adb.is_app_running(PACKAGE_NAME)
            foreground = adb.get_foreground_app() == PACKAGE_NAME
            status = "pass" if foreground else "warning"
            detail = (
                "Clash of Clans is in the foreground."
                if foreground
                else "Clash of Clans is installed but is not running or not in the foreground."
            )
            remediation = "Open Clash of Clans before starting a session." if not foreground else ""
            return [_check("clash_of_clans", status, "Clash of Clans", detail, remediation, {"deviceSerial": device.serial, "packageName": PACKAGE_NAME, "running": running, "foreground": foreground})]
        except ADBError as error:
            return [_check("clash_of_clans", "fail", "Clash of Clans", f"Could not inspect game state: {error}", "Reconnect LDPlayer and run checks again.", {"deviceSerial": device.serial})]

    def _check_tesseract(self) -> dict[str, Any]:
        try:
            executable = self.tesseract_resolver()
            if not self.tesseract_checker(executable):
                raise ResourceReaderError("Tesseract version command failed.")
            return _check("tesseract", "pass", "Tesseract", "External Tesseract executable is available.", "", {"tesseractPath": str(executable)})
        except (ResourceReaderError, OSError, subprocess.SubprocessError) as error:
            return _check("tesseract", "fail", "Tesseract", f"External Tesseract executable is unavailable: {error}", "Install Tesseract or set TESSERACT_PATH. The bundled Python wrapper is not the native executable.")

    def _check_screenshot(self, adb: ADBController) -> dict[str, Any]:
        output = self.runtime_data_dir / "screenshots" / "debug" / "preflight_latest.png"
        try:
            adb.capture_screenshot(output)
            width, height = self.image_size_reader(output)
            metadata = {"screenshotPath": str(output), "width": width, "height": height}
            if (width, height) != EXPECTED_SCREENSHOT_SIZE:
                return _check("screenshot", "fail", "Screenshot capability", f"Captured {width}x{height}; OCR currently requires 1920x1080.", "Set LDPlayer to 1920x1080, then run checks again.", metadata)
            return _check("screenshot", "pass", "Screenshot capability", "Screenshot capture and dimensions are valid.", "", metadata)
        except (ADBError, OSError, ValueError) as error:
            return _check("screenshot", "fail", "Screenshot capability", f"Could not capture or read screenshot: {error}", "Verify the selected LDPlayer device is online and accessible.", {"screenshotPath": str(output)})

    @staticmethod
    def _read_image_size(path: Path) -> tuple[int, int]:
        from PIL import Image

        with Image.open(path) as image:
            return image.size

    @staticmethod
    def _check_tesseract_executable(executable: Path) -> bool:
        result = subprocess.run([str(executable), "--version"], capture_output=True, text=True, timeout=5, check=False)
        return result.returncode == 0


def _check(check_id: str, status: str, title: str, detail: str, remediation: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": check_id, "status": status, "title": title, "detail": detail, "remediation": remediation, "metadata": metadata or {}}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _copy_report(report: dict[str, Any]) -> dict[str, Any]:
    return {**report, "checks": [{**check, "metadata": dict(check["metadata"])} for check in report["checks"]]}
