from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adb_controller import ADBController, ADBError
from decision_engine import CONFIG_PATH, BotConfig, load_bot_config
from project_paths import RUNTIME_DATA_DIR
from resource_reader import ResourceReader
from runtime.history_store import HistoryStore, HistoryStoreError
from runtime.reliability_guard import ReliabilityError
from runtime.runtime_control import RuntimeControl, SessionTimedOut, StopRequested
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
        history_store: HistoryStore | None = None,
        runtime_data_dir: str | Path | None = None,
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
        history_data_dir = Path(runtime_data_dir) if runtime_data_dir else (
            RUNTIME_DATA_DIR if self.config_path.resolve() == Path(CONFIG_PATH).resolve() else self.config_path.parent
        )
        self._history_store = history_store or HistoryStore(history_data_dir)
        self._session_id: str | None = None
        self._log_handler: _RecentLogHandler | None = None

    def _empty_telemetry(self) -> dict[str, Any]:
        return {
            "phase": "IDLE", "gameScreen": "UNKNOWN", "screenConfidence": None,
            "screenDetails": None, "screenshotPath": None, "debugArtifactPaths": [],
            "gold": None, "elixir": None, "darkElixir": None, "decision": None,
            "decisionReasons": [], "basesChecked": 0, "maxBases": 0, "nextTaps": 0,
            "ocrAttempts": 0, "unknownStateRetries": 0, "attackPlan": None,
            "battlesPlanned": 0, "battlesCompleted": 0, "recentSetupHistory": [],
            "sessionElapsedSeconds": 0.0, "sessionRemainingSeconds": None, "sessionMaxRuntimeSeconds": None,
            "emulatorConnected": False, "gameRunning": False, "gameForeground": False,
            "strategy": None, "dryRun": None, "deviceSerial": None, "sessionId": None,
            "lastError": None, "startedAt": None,
            "terminalResult": None, "terminalMessage": None,
            "failureCode": None, "failureMessage": None, "expectedStates": [], "observedState": None,
            "diagnosticScreenshotPath": None, "calibrationArtifactPaths": [],
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }

    def start(self) -> tuple[bool, str]:
        with self._lock:
            if self._state in {RuntimeState.STARTING, RuntimeState.RUNNING, RuntimeState.PAUSED, RuntimeState.STOPPING}:
                return False, "A bot session is already active."
            self._ensure_log_handler_locked()
            self._stop_event.clear()
            self._pause_event.clear()
            session_id = str(uuid.uuid4())
            started_at = datetime.now(timezone.utc).isoformat()
            self._state = RuntimeState.STARTING
            self._session_id = session_id
            self._telemetry = self._empty_telemetry()
            self._update_locked(phase="STARTING", startedAt=started_at, sessionId=session_id)
            self._history_call("create session", self._history_store.create_session, session_id, started_at, self.preflight_report())
            self._history_event_locked("lifecycle", "Session started.")
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
            self._history_event_locked("pause", "Pause requested.")
            self._history_event_locked("phase", "Phase changed to PAUSED.", {"phase": "PAUSED"})
        return True, "Pause requested; it takes effect at the next safe checkpoint."

    def resume(self) -> tuple[bool, str]:
        with self._lock:
            if self._state is RuntimeState.PAUSED:
                self._pause_event.clear()
                self._state = RuntimeState.RUNNING
                self._update_locked(phase="RUNNING")
                self._history_event_locked("resume", "Session resumed.")
                self._history_event_locked("phase", "Phase changed to RUNNING.", {"phase": "RUNNING"})
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
            self._history_event_locked("stop", "Stop requested.")
            self._history_event_locked("phase", "Phase changed to STOPPING.", {"phase": "STOPPING"})
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
            previous_phase = self._telemetry.get("phase")
            self._update_locked(**values)
            current_phase = self._telemetry.get("phase")
            if current_phase and current_phase != previous_phase:
                self._history_event_locked("phase", f"Phase changed to {current_phase}.", {"phase": current_phase})

    def log(self, level: int, message: str) -> None:
        with self._lock:
            timestamp = datetime.now(timezone.utc).isoformat()
            level_name = logging.getLevelName(level)
            self._logs.append({"timestamp": timestamp, "level": level_name, "message": message})
            if level >= logging.WARNING:
                self._history_event_locked("log", message, level=level_name)
        logging.log(level, message)

    def set_preflight_report(self, report: dict[str, Any]) -> None:
        with self._lock:
            self._preflight_report = report

    def preflight_report(self) -> dict[str, Any] | None:
        with self._lock:
            return None if self._preflight_report is None else {**self._preflight_report, "checks": [{**check, "metadata": dict(check["metadata"])} for check in self._preflight_report["checks"]]}

    @property
    def history_store(self) -> HistoryStore:
        return self._history_store

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
            started_monotonic = time.monotonic()
            deadline_monotonic = started_monotonic + config.max_runtime_seconds
            with self._lock:
                self._state = RuntimeState.RUNNING
                self._update_locked(
                    phase="RUNNING", strategy=config.strategy, dryRun=config.dry_run,
                    maxBases=config.max_bases_to_check, sessionElapsedSeconds=0.0,
                    sessionRemainingSeconds=config.max_runtime_seconds,
                    sessionMaxRuntimeSeconds=config.max_runtime_seconds,
                )
                self._history_call("update session context", self._history_store.update_session_context, self._session_id, dict(self._telemetry))
                self._history_event_locked("phase", "Phase changed to RUNNING.", {"phase": "RUNNING"})
            control = RuntimeControl(
                self._stop_event,
                self._pause_event,
                self.report,
                self.log,
                started_monotonic=started_monotonic,
                deadline_monotonic=deadline_monotonic,
            )
            self._worker_factory(control, config)
            self._finish_session(RuntimeState.STOPPED, "completed", "Bot session completed.")
        except SessionTimedOut:
            message = "Session reached its configured max runtime and stopped safely."
            self.report(failureCode="MAX_RUNTIME_REACHED", failureMessage=message, sessionRemainingSeconds=0.0)
            self._finish_session(RuntimeState.STOPPED, "stopped", message)
        except StopRequested:
            self._finish_session(RuntimeState.STOPPED, "stopped", "Bot session stopped.")
            logging.info("Bot session stopped cooperatively")
        except Exception as error:
            logging.exception("Bot session failed: %s", error)
            failure = error if isinstance(error, ReliabilityError) else None
            if failure:
                self.report({
                    "failureCode": failure.failure_code, "failureMessage": failure.failure_message,
                    "expectedStates": failure.expected_states, "observedState": failure.observed_state,
                    "diagnosticScreenshotPath": failure.diagnostic_screenshot_path,
                })
            self._finish_session(RuntimeState.ERROR, "failed", str(error), last_error=str(error), exception_message=str(error))

    def _finish_session(self, state: RuntimeState, result: str, message: str, *, last_error: str | None = None, exception_message: str | None = None) -> None:
        with self._lock:
            session_id = self._session_id
            ended_at = datetime.now(timezone.utc).isoformat()
            self._state = state
            self._update_locked(phase=state.value, lastError=last_error, terminalResult=result, terminalMessage=message, sessionId=None)
            snapshot = dict(self._telemetry)
            if exception_message:
                self._history_event_locked("exception", exception_message, level="ERROR")
            self._history_event_locked("terminal", f"Session {result}: {message}", {"terminalResult": result})
            if session_id:
                self._history_call("finish session", self._history_store.finish_session, session_id, runtime_state=state.value, telemetry=snapshot, ended_at=ended_at)
            self._session_id = None
            self._detach_log_handler_locked()

    def _history_event_locked(self, event_type: str, message: str, data: dict[str, Any] | None = None, *, level: str | None = None) -> None:
        if self._session_id:
            self._history_call(
                "store event", self._history_store.add_event, self._session_id,
                timestamp=datetime.now(timezone.utc).isoformat(), event_type=event_type,
                message=message, level=level, data=data,
            )

    def _history_call(self, action: str, callback: Callable[..., None], *args: Any, **kwargs: Any) -> None:
        try:
            callback(*args, **kwargs)
        except (HistoryStoreError, OSError) as error:
            # Do not call self.log here: that would attempt to write the warning back to history.
            logging.warning("History storage failure while trying to %s: %s", action, error)

    def _run_default_worker(self, control: RuntimeControl, config: BotConfig) -> None:
        controller = ADBController()
        controller.check_adb_available()
        device = controller.select_device()
        package_name = "com.supercell.clashofclans"
        if package_name not in controller.get_installed_packages():
            raise ADBError(f"Package not installed: {package_name}")
        foreground = controller.get_foreground_app()
        control.report(emulatorConnected=True, gameRunning=controller.is_app_running(package_name), gameForeground=foreground == package_name, deviceSerial=device.serial, phase=f"CONNECTED:{device.serial}")
        control.log(logging.INFO, f"Connected to Android device: {device.serial}")
        setup_history: list[str] = []
        completed_battles = 0
        control.report(battlesPlanned=config.battles_per_session, battlesCompleted=completed_battles)
        for battle_number in range(1, config.battles_per_session + 1):
            control.checkpoint(f"PREPARE_BATTLE_{battle_number}")
            control.log(logging.INFO, f"Starting battle {battle_number} of {config.battles_per_session}.")
            flow = TrialFlowController(
                adb_controller=controller,
                resource_reader=ResourceReader(),
                bot_config=config,
                package_name=package_name,
                screen_threshold=0.85,
                battlefield_diff_threshold=0.05,
                dry_run=config.dry_run,
                random_setup_test=True,
                setup_history=setup_history,
                control=control,
            )
            flow.run()
            if flow.selected_setup is None:
                control.log(
                    logging.WARNING,
                    f"No suitable base was found for battle {battle_number}; ending the session after {completed_battles} completed battles.",
                )
                break
            completed_battles += 1
            control.report(
                battlesCompleted=completed_battles,
                recentSetupHistory=setup_history[-2:],
            )
            control.log(logging.INFO, f"Battle {completed_battles} completed using {flow.selected_setup}.")
