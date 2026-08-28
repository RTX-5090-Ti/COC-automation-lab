from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_ADB_PATHS = (
    Path(r"C:\LDPlayer\LDPlayer14\adb.exe"),
    Path(r"C:\LDPlayer\LDPlayer9\adb.exe"),
    Path(r"C:\LDPlayer\LDPlayer\adb.exe"),
    Path(r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe"),
)


class ADBError(Exception):
    """Raised when an ADB operation fails in a user-facing way."""


@dataclass(frozen=True)
class DeviceInfo:
    """Represents one device returned by `adb devices -l`."""

    serial: str
    status: str
    details: str = ""


class ADBController:
    """Small wrapper around the ADB executable used in Milestone 1."""

    def __init__(
        self,
        adb_path: str | Path | None = None,
        device_id: str | None = None,
        timeout_seconds: int = 15,
    ) -> None:
        self.adb_path = self._resolve_adb_path(adb_path)
        self.device_id = device_id
        self.timeout_seconds = timeout_seconds

    @classmethod
    def _resolve_adb_path(cls, adb_path: str | Path | None = None) -> Path:
        if adb_path:
            candidate = Path(adb_path).expanduser()
            if candidate.is_file():
                return candidate
            raise ADBError(f"ADB executable not found at configured path: {candidate}")

        env_path = os.getenv("ADB_PATH") or os.getenv("LDPLAYER_ADB_PATH")
        if env_path:
            candidate = Path(env_path).expanduser()
            if candidate.is_file():
                return candidate

        detected_adb = shutil.which("adb")
        if detected_adb:
            return Path(detected_adb)

        for default_path in DEFAULT_ADB_PATHS:
            if default_path.is_file():
                return default_path

        raise ADBError(
            "ADB executable not found. Set the ADB_PATH environment variable, "
            "install adb in PATH, or install LDPlayer/BlueStacks at a default path."
        )

    def check_adb_available(self) -> bool:
        self.run_command(["version"])
        return True

    def list_devices(self) -> list[DeviceInfo]:
        result = self.run_command(["devices", "-l"], include_device=False)
        devices: list[DeviceInfo] = []

        for line in result.stdout.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("List of devices attached"):
                continue
            if stripped.startswith("* daemon"):
                continue

            parts = stripped.split(maxsplit=2)
            if len(parts) < 2:
                continue

            serial = parts[0]
            status = parts[1]
            details = parts[2] if len(parts) > 2 else ""
            devices.append(DeviceInfo(serial=serial, status=status, details=details))

        return devices

    def select_device(self, preferred_serial: str | None = None) -> DeviceInfo:
        devices = self.list_devices()
        available = [device for device in devices if device.status == "device"]

        offline = [device.serial for device in devices if device.status == "offline"]
        unauthorized = [device.serial for device in devices if device.status == "unauthorized"]

        if offline:
            raise ADBError(f"ADB device is offline: {', '.join(offline)}")

        if unauthorized:
            raise ADBError(f"ADB device is unauthorized: {', '.join(unauthorized)}")

        if not available:
            raise ADBError("No connected ADB device with status 'device' was found.")

        if preferred_serial:
            for device in available:
                if device.serial == preferred_serial:
                    self.device_id = device.serial
                    return device
            raise ADBError(f"Preferred device '{preferred_serial}' was not found.")

        if len(available) > 1:
            serials = ", ".join(device.serial for device in available)
            raise ADBError(
                f"Multiple connected devices found ({serials}). "
                "Pass a specific device id to select one."
            )

        selected = available[0]
        self.device_id = selected.serial
        return selected

    def get_installed_packages(self) -> list[str]:
        result = self.run_command(["shell", "pm", "list", "packages"])
        packages: list[str] = []

        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("package:"):
                packages.append(stripped.removeprefix("package:"))

        return packages

    def is_app_installed(self, package_name: str) -> bool:
        return package_name in self.get_installed_packages()

    def is_app_running(self, package_name: str) -> bool:
        result = self.run_command(["shell", "pidof", package_name], check=False)
        return bool(result.stdout.strip())

    def get_foreground_app(self) -> str | None:
        result = self.run_command(["shell", "dumpsys", "window", "windows"], check=False)

        markers = ("mCurrentFocus", "mFocusedApp")
        for line in result.stdout.splitlines():
            if any(marker in line for marker in markers):
                package_name = self._extract_package_from_focus_line(line)
                if package_name:
                    return package_name

        # Android 14 may omit focus markers from Window Manager output.
        activity_result = self.run_command(["shell", "dumpsys", "activity", "activities"], check=False)
        activity_markers = ("topResumedActivity", "mResumedActivity", "mCurrentFocus", "mFocusedApp")
        for line in activity_result.stdout.splitlines():
            if any(marker in line for marker in activity_markers):
                package_name = self._extract_package_from_focus_line(line)
                if package_name:
                    return package_name

        return None

    def is_app_in_foreground(self, package_name: str) -> bool:
        return self.get_foreground_app() == package_name

    def launch_app(self, package_name: str) -> subprocess.CompletedProcess[str]:
        return self.run_command(
            ["shell", "monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1"]
        )

    def tap(self, x: int, y: int) -> subprocess.CompletedProcess[str]:
        if x < 0 or y < 0:
            raise ADBError(f"Tap coordinates must be non-negative. Received: ({x}, {y})")
        return self.run_command(["shell", "input", "tap", str(x), str(y)])

    def capture_screenshot(self, output_path: str | Path) -> Path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        command = self._build_command(["exec-out", "screencap", "-p"], include_device=True)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as error:
            raise ADBError(f"ADB executable could not be launched: {self.adb_path}") from error
        except subprocess.TimeoutExpired as error:
            raise ADBError("ADB screenshot command timed out.") from error

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            raise ADBError(f"ADB screenshot command failed: {stderr or 'unknown error'}")

        output.write_bytes(result.stdout)
        return output

    def run_command(
        self,
        args: Iterable[str],
        *,
        include_device: bool = True,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = self._build_command(args, include_device=include_device)

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as error:
            raise ADBError(f"ADB executable could not be launched: {self.adb_path}") from error
        except subprocess.TimeoutExpired as error:
            raise ADBError(f"ADB command timed out: {' '.join(command)}") from error

        if check and result.returncode != 0:
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            message = stderr or stdout or "unknown error"
            raise ADBError(f"ADB command failed: {message}")

        return result

    def _build_command(self, args: Iterable[str], *, include_device: bool) -> list[str]:
        command = [str(self.adb_path)]
        if include_device and self.device_id:
            command.extend(["-s", self.device_id])
        command.extend(args)
        return command

    @staticmethod
    def _extract_package_from_focus_line(line: str) -> str | None:
        for token in line.replace("}", " ").split():
            if "/" in token and "." in token:
                return token.split("/", maxsplit=1)[0]
        return None
