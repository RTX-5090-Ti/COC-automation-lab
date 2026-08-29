"""Small, local SQLite audit trail for completed bot sessions."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any


class HistoryStoreError(RuntimeError):
    """Raised to the runtime after a non-fatal history storage failure."""


class HistoryStore:
    """Thread-safe SQLite history with bounded session and event retention."""

    def __init__(self, runtime_data_dir: str | Path, *, session_limit: int = 200, event_limit: int = 100) -> None:
        self.database_path = Path(runtime_data_dir) / "history" / "session_history.sqlite3"
        self.session_limit = session_limit
        self.event_limit = event_limit
        self._lock = threading.RLock()
        self._initialized = False

    def create_session(self, session_id: str, started_at: str, preflight: dict[str, Any] | None) -> None:
        self._write(
            """INSERT INTO sessions (session_id, started_at, preflight_json)
               VALUES (?, ?, ?)""",
            (session_id, started_at, _json(preflight)),
        )

    def update_session_context(self, session_id: str, telemetry: dict[str, Any]) -> None:
        self._write(
            """UPDATE sessions SET dry_run = ?, strategy = ?, device_serial = ?, phase = ?
               WHERE session_id = ?""",
            (
                _bool(telemetry.get("dryRun")), telemetry.get("strategy"), _device_serial(telemetry),
                telemetry.get("phase"), session_id,
            ),
        )

    def finish_session(self, session_id: str, *, runtime_state: str, telemetry: dict[str, Any], ended_at: str) -> None:
        started_at = telemetry.get("startedAt")
        duration = _duration_seconds(started_at, ended_at)
        self._write(
            """UPDATE sessions SET end_at = ?, duration_seconds = ?, runtime_state = ?, terminal_result = ?,
                  terminal_message = ?, last_error = ?, phase = ?, dry_run = ?, strategy = ?, device_serial = ?,
                  bases_checked = ?, max_bases = ?, next_taps = ?, telemetry_json = ?, attack_plan_json = ?, artifact_paths_json = ?
               WHERE session_id = ?""",
            (
                ended_at, duration, runtime_state, telemetry.get("terminalResult"), telemetry.get("terminalMessage"),
                telemetry.get("lastError"), telemetry.get("phase"), _bool(telemetry.get("dryRun")),
                telemetry.get("strategy"), _device_serial(telemetry), telemetry.get("basesChecked", 0),
                telemetry.get("maxBases", 0), telemetry.get("nextTaps", 0), _json(telemetry),
                _json(telemetry.get("attackPlan")), _json(_artifact_paths(telemetry)), session_id,
            ),
            prune_sessions=True,
        )

    def add_event(self, session_id: str, *, timestamp: str, event_type: str, message: str, level: str | None = None, data: dict[str, Any] | None = None) -> None:
        self._write(
            """INSERT INTO session_events (session_id, timestamp, event_type, level, message, data_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, timestamp, event_type, level, message, _json(data)),
            prune_events_for=session_id,
        )

    def list_sessions(self, *, limit: int, offset: int) -> dict[str, Any]:
        with closing(self._connection()) as connection:
            rows = connection.execute(
                """SELECT session_id, started_at, end_at, duration_seconds, runtime_state, terminal_result,
                          terminal_message, dry_run, strategy, bases_checked, max_bases, next_taps
                   FROM sessions ORDER BY started_at DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
            total = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        return {"items": [_summary(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with closing(self._connection()) as connection:
            row = connection.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if row is None:
                return None
            events = connection.execute(
                """SELECT timestamp, event_type, level, message, data_json FROM session_events
                   WHERE session_id = ? ORDER BY id ASC LIMIT ?""",
                (session_id, self.event_limit),
            ).fetchall()
        result = dict(row)
        result["preflight"] = _from_json(result.pop("preflight_json"))
        result["telemetry"] = _from_json(result.pop("telemetry_json"))
        result["attackPlan"] = _from_json(result.pop("attack_plan_json"))
        result["artifactPaths"] = _from_json(result.pop("artifact_paths_json")) or []
        result["events"] = [
            {"timestamp": item["timestamp"], "eventType": item["event_type"], "level": item["level"], "message": item["message"], "data": _from_json(item["data_json"]) or {}}
            for item in events
        ]
        return _camel_case_session(result)

    def _write(self, statement: str, values: tuple[Any, ...], *, prune_events_for: str | None = None, prune_sessions: bool = False) -> None:
        try:
            with self._lock, closing(self._connection()) as connection:
                with connection:
                    connection.execute(statement, values)
                    if prune_events_for:
                        connection.execute(
                        """DELETE FROM session_events WHERE session_id = ? AND id NOT IN (
                               SELECT id FROM session_events WHERE session_id = ? ORDER BY id DESC LIMIT ?
                           )""",
                        (prune_events_for, prune_events_for, self.event_limit),
                        )
                    if prune_sessions:
                        connection.execute(
                        """DELETE FROM sessions WHERE session_id IN (
                               SELECT session_id FROM sessions ORDER BY started_at DESC LIMIT -1 OFFSET ?
                           )""",
                        (self.session_limit,),
                        )
        except (OSError, sqlite3.Error) as error:
            raise HistoryStoreError(str(error)) from error

    def _connection(self) -> sqlite3.Connection:
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.database_path, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            if not self._initialized:
                with self._lock:
                    if not self._initialized:
                        connection.executescript(_SCHEMA)
                        self._initialized = True
            return connection
        except (OSError, sqlite3.Error) as error:
            raise HistoryStoreError(str(error)) from error


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    end_at TEXT,
    duration_seconds REAL,
    runtime_state TEXT,
    terminal_result TEXT,
    terminal_message TEXT,
    last_error TEXT,
    phase TEXT,
    dry_run INTEGER,
    strategy TEXT,
    device_serial TEXT,
    bases_checked INTEGER DEFAULT 0,
    max_bases INTEGER DEFAULT 0,
    next_taps INTEGER DEFAULT 0,
    preflight_json TEXT,
    telemetry_json TEXT,
    attack_plan_json TEXT,
    artifact_paths_json TEXT
);
CREATE TABLE IF NOT EXISTS session_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    level TEXT,
    message TEXT NOT NULL,
    data_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_session_events_session_id ON session_events(session_id, id);
"""


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=True, default=str)


def _from_json(value: str | None) -> Any:
    return None if value is None else json.loads(value)


def _bool(value: Any) -> int | None:
    return None if value is None else int(bool(value))


def _device_serial(telemetry: dict[str, Any]) -> str | None:
    phase = telemetry.get("phase")
    return phase.split(":", 1)[1] if isinstance(phase, str) and phase.startswith("CONNECTED:") else None


def _artifact_paths(telemetry: dict[str, Any]) -> list[str]:
    return [path for path in [telemetry.get("screenshotPath"), *telemetry.get("debugArtifactPaths", [])] if path]


def _duration_seconds(started_at: Any, ended_at: str) -> float | None:
    from datetime import datetime

    if not isinstance(started_at, str):
        return None
    try:
        return max(0.0, (datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)).total_seconds())
    except ValueError:
        return None


def _summary(row: sqlite3.Row) -> dict[str, Any]:
    return _camel_case_session(dict(row))


def _camel_case_session(values: dict[str, Any]) -> dict[str, Any]:
    renamed = {
        "session_id": "sessionId", "started_at": "startedAt", "end_at": "endedAt",
        "duration_seconds": "durationSeconds", "runtime_state": "runtimeState",
        "terminal_result": "terminalResult", "terminal_message": "terminalMessage",
        "last_error": "lastError", "dry_run": "dryRun", "device_serial": "deviceSerial",
        "bases_checked": "basesChecked", "max_bases": "maxBases", "next_taps": "nextTaps",
    }
    return {renamed.get(key, key): value for key, value in values.items()}
