from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from api.app import create_app
from runtime.bot_runtime import BotRuntime
from runtime.history_store import HistoryStore, HistoryStoreError
from runtime.runtime_state import RuntimeState
from tests.conftest import request, wait_for


def _telemetry(started_at: str, result: str = "completed") -> dict:
    return {
        "startedAt": started_at, "terminalResult": result, "terminalMessage": f"{result} result",
        "lastError": None, "phase": "STOPPED", "dryRun": True, "strategy": "sneaky_goblin",
        "deviceSerial": "emulator-5554", "basesChecked": 2, "maxBases": 5, "nextTaps": 1,
        "gold": 101, "elixir": 202, "darkElixir": 303, "decision": "ATTACK",
        "decisionReasons": ["threshold met"], "ocrAttempts": 1, "unknownStateRetries": 0,
        "attackPlan": {"strategy": "sneaky_goblin", "plannedActionCount": 3},
        "screenshotPath": "C:/runtime/current.png", "debugArtifactPaths": ["C:/runtime/debug.png"],
    }


def test_history_persists_completed_session_and_api_shape(config_path, tmp_path) -> None:
    store = HistoryStore(tmp_path)

    def worker(control, _config) -> None:
        control.report(gold=101, elixir=202, darkElixir=303, decision="ATTACK", decisionReasons=["threshold met"], basesChecked=2, nextTaps=1, attackPlan={"strategy": "sneaky_goblin", "plannedActionCount": 3}, screenshotPath="C:/runtime/current.png", debugArtifactPaths=["C:/runtime/debug.png"], phase="FAKE_DONE")
        control.log(logging.WARNING, "important worker warning")

    runtime = BotRuntime(config_path=config_path, worker_factory=worker, history_store=store)
    runtime.set_preflight_report({"overallStatus": "ready", "checkedAt": "2026-01-01T00:00:00+00:00", "checks": []})
    app = create_app(config_path=config_path, runtime=runtime, history_store=store)
    assert request(app, "POST", "/api/session/start").status_code == 200
    wait_for(lambda: runtime.status()["runtimeState"] == RuntimeState.STOPPED.value)
    assert runtime.status()["sessionId"] is None

    listing = request(app, "GET", "/api/history/sessions?limit=10&offset=0")
    assert listing.status_code == 200
    item = listing.json()["items"][0]
    assert item["terminalResult"] == "completed"
    assert item["basesChecked"] == 2
    detail = request(app, "GET", f"/api/history/sessions/{item['sessionId']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["preflight"]["overallStatus"] == "ready"
    assert body["telemetry"]["gold"] == 101
    assert body["attackPlan"]["plannedActionCount"] == 3
    assert body["artifactPaths"] == ["C:/runtime/current.png", "C:/runtime/debug.png"]
    assert any(event["eventType"] == "log" and event["level"] == "WARNING" for event in body["events"])
    assert request(app, "GET", "/api/history/sessions/missing").status_code == 404
    runtime.close()


def test_history_records_stopped_and_failed_results(config_path, tmp_path) -> None:
    store = HistoryStore(tmp_path)
    started = threading.Event()

    def blocking_worker(control, _config) -> None:
        started.set()
        while True:
            control.checkpoint("FAKE_BLOCKING")
            time.sleep(0.01)

    stopped = BotRuntime(config_path=config_path, worker_factory=blocking_worker, history_store=store)
    assert stopped.start()[0]
    assert started.wait(1)
    active_id = stopped.status()["sessionId"]
    assert active_id
    assert stopped.stop()[0]
    wait_for(lambda: stopped.status()["runtimeState"] == RuntimeState.STOPPED.value)

    def failed_worker(_control, _config) -> None:
        raise RuntimeError("fake failure")

    failed = BotRuntime(config_path=config_path, worker_factory=failed_worker, history_store=store)
    assert failed.start()[0]
    wait_for(lambda: failed.status()["runtimeState"] == RuntimeState.ERROR.value)
    records = store.list_sessions(limit=10, offset=0)["items"]
    assert {item["terminalResult"] for item in records} == {"stopped", "failed"}
    failed_detail = next(store.get_session(item["sessionId"]) for item in records if item["terminalResult"] == "failed")
    assert failed_detail["runtimeState"] == "ERROR"
    assert any(event["eventType"] == "exception" for event in failed_detail["events"])
    stopped.close()
    failed.close()


def test_history_retention_and_recreation(tmp_path) -> None:
    store = HistoryStore(tmp_path, session_limit=2, event_limit=2)
    start = datetime.now(timezone.utc)
    for index in range(3):
        session_id = f"session-{index}"
        started_at = (start + timedelta(seconds=index)).isoformat()
        store.create_session(session_id, started_at, None)
        for event in range(3):
            store.add_event(session_id, timestamp=started_at, event_type="phase", message=str(event))
        store.finish_session(session_id, runtime_state="STOPPED", telemetry=_telemetry(started_at), ended_at=(start + timedelta(seconds=index + 1)).isoformat())
    recreated = HistoryStore(tmp_path, session_limit=2, event_limit=2)
    listing = recreated.list_sessions(limit=10, offset=0)
    assert [item["sessionId"] for item in listing["items"]] == ["session-2", "session-1"]
    assert len(recreated.get_session("session-2")["events"]) == 2
    assert recreated.list_sessions(limit=1, offset=1)["items"][0]["sessionId"] == "session-1"


def test_history_failure_does_not_fail_session(config_path) -> None:
    class BrokenStore:
        def create_session(self, *_args) -> None: raise HistoryStoreError("disk unavailable")
        def add_event(self, *_args, **_kwargs) -> None: raise HistoryStoreError("disk unavailable")
        def update_session_context(self, *_args) -> None: raise HistoryStoreError("disk unavailable")
        def finish_session(self, *_args, **_kwargs) -> None: raise HistoryStoreError("disk unavailable")

    runtime = BotRuntime(config_path=config_path, worker_factory=lambda _control, _config: None, history_store=BrokenStore())
    assert runtime.start()[0]
    wait_for(lambda: runtime.status()["runtimeState"] == RuntimeState.STOPPED.value)
    assert runtime.telemetry()["terminalResult"] == "completed"
    assert any("History storage failure" in entry["message"] for entry in runtime.logs(100))
    runtime.close()
