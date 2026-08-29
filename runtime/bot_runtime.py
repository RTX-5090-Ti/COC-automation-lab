from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adb_controller import ADBController, ADBError
from decision_engine import CONFIG_PATH, BotConfig, load_bot_config
from resource_reader import ResourceReader
from runtime.runtime_control import RuntimeControl, StopRequested
from runtime.runtime_state import RuntimeState
from trial_flow_controller import TrialFlowController


WorkerFactory = Callable[[RuntimeControl, BotConfig], None]


class _RecentLogHandler(logging.Handler):
    def __init__(self, entries: deque[dict[str, str]], lock: threading.RLock) -> None:
        super().__init__()
        self._entries = entries
        self._lock = lock

    def emit(self, record: logging.LogRecord) -> None:
        with self._lock:
            self._entries.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "message": self.format(record),
            })


class BotRuntime:
    """Owns one bounded session and a thread-safe API-facing snapshot."""

    def __init__(
        self,
        *,
        config_path: str | Path = CONFIG_PATH,
        worker_factory: WorkerFactory | None = None,
        log_limit: int = 500,
    ) -> None:
        self.config_path = Path(config_path)
        self._worker_factory = worker_factory or self._run_default_worker
        self._lock = threading.RLock()
        self._logs: deque[dict[str, str]] = deque(maxlen=log_limit)
        self._state = RuntimeState.IDLE
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._telemetry = self._empty_telemetry()
        self._preflight_report: dict[str, Any] | None = None
        self._log_handler: _RecentLogHandler | None = None

    def _empty_telemetry(self) -> dict[str, Any]:
        return {
            "phase": "IDLE", "gameScreen": "UNKNOWN", "screenConfidence": None,
            "screenDetails": None, "screenshotPath": None, "debugArtifactPaths": [],
            "gold": None, "elixir": None, "darkElixir": None, "decision": None,
            "decisionReasons": [], "basesChecked": 0, "maxBases": 0, "nextTaps": 0,
            "ocrAttempts": 0, "unknownStateRetries": 0, "attackPlan": None,
            "emulatorConnected": False, "gameRunning": False, "gameForeground": False,
            "strategy": None, "dryRun": None, "lastError": None, "startedAt": None,
            "terminalResult": None, "terminalMessage": None,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }

    def start(self) -> tuple[bool, str]:
        with self._lock:
            if self._state in {RuntimeState.STARTING, RuntimeState.RUNNING, RuntimeState.PAUSED, RuntimeState.STOPPING}:
                return False, "A bot session is already active."
            self._ensure_log_handler_locked()
            self._stop_event.clear()
            self._pause_event.clear()
            self._state = RuntimeState.STARTING
            self._telemetry = self._empty_telemetry()
            self._update_locked(phase="STARTING", startedAt=datetime.now(timezone.utc).isoformat())
            self._thread = threading.Thread(target=self._run_session, name="bot-session", daemon=True)
            self._thread.start()
        return True, "Bot session started."

    def pause(self) -> tuple[bool, str]:
        with self._lock:
            if self._state is RuntimeState.IDLE:
                return False, "No bot session is running."
            if self._state is RuntimeState.PAUSED:
                return True, "Bot session is already paused."
            if self._state is not RuntimeState.RUNNING:
                return False, f"Cannot pause while runtime is {self._state.value}."
            self._pause_event.set()
            self._state = RuntimeState.PAUSED
            self._update_locked(phase="PAUSED")
        return True, "Pause requested; it takes effect at the next safe checkpoint."

    def resume(self) -> tuple[bool, str]:
        with self._lock:
            if self._state is RuntimeState.PAUSED:
                self._pause_event.clear()
                self._state = RuntimeState.RUNNING
                self._update_locked(phase="RUNNING")
                return True, "Bot session resumed."
            return True, f"Bot session is not paused (runtime is {self._state.value})."

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            if self._state in {RuntimeState.IDLE, RuntimeState.STOPPED, RuntimeState.ERROR}:
                return True, "No active bot session to stop."
            self._state = RuntimeState.STOPPING
            self._pause_event.clear()
            self._stop_event.set()
            self._update_locked(phase="STOPPING")
        return True, "Stop requested; the session will end at the next safe checkpoint."

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"runtimeState": self._state.value, **self._telemetry}

    def telemetry(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._telemetry)

    def logs(self, limit: int) -> list[dict[str, str]]:
        with self._lock:
            return list(self._logs)[-limit:]

    def report(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._update_locked(**values)

    def log(self, level: int, message: str) -> None:
        with self._lock:
            self._logs.append({"timestamp": datetime.now(timezone.utc).isoformat(), "level": logging.getLevelName(level), "message": message})
        logging.log(level, message)

    def set_preflight_report(self, report: dict[str, Any]) -> None:
        with self._lock:
            self._preflight_report = report

    def preflight_report(self) -> dict[str, Any] | None:
        with self._lock:
            return None if self._preflight_report is None else {**self._preflight_report, "checks": [{**check, "metadata": dict(check["metadata"])} for check in self._preflight_report["checks"]]}

    def close(self) -> None:
        with self._lock:
            self._detach_log_handler_locked()

    def _ensure_log_handler_locked(self) -> None:
        if self._log_handler is None:
            self._log_handler = _RecentLogHandler(self._logs, self._lock)
            self._log_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            logging.getLogger().addHandler(self._log_handler)

    def _detach_log_handler_locked(self) -> None:
        if self._log_handler is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None

    def _update_locked(self, **values: Any) -> None:
        self._telemetry.update(values)
        self._telemetry["updatedAt"] = datetime.now(timezone.utc).isoformat()

    def _run_session(self) -> None:
        try:
            config = load_bot_config(self.config_path)
            with self._lock:
                self._state = RuntimeState.RUNNING
                self._update_locked(phase="RUNNING", strategy=config.strategy, dryRun=config.dry_run, maxBases=config.max_bases_to_check)
            control = RuntimeControl(self._stop_event, self._pause_event, self.report, self.log)
            self._worker_factory(control, config)
            with self._lock:
                self._state = RuntimeState.STOPPED
                self._update_locked(phase="STOPPED", terminalResult="completed", terminalMessage="Bot session completed.")
                self._detach_log_handler_locked()
        except StopRequested:
            with self._lock:
                self._state = RuntimeState.STOPPED
                self._update_locked(phase="STOPPED", terminalResult="stopped", terminalMessage="Bot session stopped.")
                self._detach_log_handler_locked()
            logging.info("Bot session stopped cooperatively")
        except Exception as error:
            logging.exception("Bot session failed: %s", error)
            with self._lock:
                self._state = RuntimeState.ERROR
                self._update_locked(phase="ERROR", lastError=str(error), terminalResult="failed", terminalMessage=str(error))
                self._detach_log_handler_locked()

    def _run_default_worker(self, control: RuntimeControl, config: BotConfig) -> None:
        controller = ADBController()
        controller.check_adb_available()
        device = controller.select_device()
        package_name = "com.supercell.clashofclans"
        if package_name not in controller.get_installed_packages():
            raise ADBError(f"Package not installed: {package_name}")
        foreground = controller.get_foreground_app()
        control.report(emulatorConnected=True, gameRunning=controller.is_app_running(package_name), gameForeground=foreground == package_name, phase=f"CONNECTED:{device.serial}")
        control.log(logging.INFO, f"Connected to Android device: {device.serial}")
        TrialFlowController(adb_controller=controller, resource_reader=ResourceReader(), bot_config=config, package_name=package_name, screen_threshold=0.85, battlefield_diff_threshold=0.05, dry_run=config.dry_run, control=control).run()
