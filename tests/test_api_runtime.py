from __future__ import annotations

import logging
import json
import threading
import time

from api.app import create_app
from runtime.bot_runtime import BotRuntime
from runtime.runtime_state import RuntimeState
from tests.conftest import request, wait_for


def _blocking_worker(started: threading.Event):
    def worker(control, _config) -> None:
        control.report(gameScreen="HOME", screenConfidence=0.99, phase="FAKE_WORKER")
        control.log(logging.WARNING, "fake worker started")
        started.set()
        while True:
            control.checkpoint("FAKE_WORKER")
            time.sleep(0.01)

    return worker


def test_health_and_baseline_status(config_path) -> None:
    runtime = BotRuntime(config_path=config_path)
    app = create_app(config_path=config_path, runtime=runtime)
    assert request(app, "GET", "/api/health").json() == {"status": "ok"}
    status = request(app, "GET", "/api/status").json()
    assert status["runtimeState"] == "IDLE"
    assert status["terminalResult"] is None
    runtime.close()


def test_duplicate_start_and_cooperative_pause_resume_stop(config_path) -> None:
    started = threading.Event()
    runtime = BotRuntime(config_path=config_path, worker_factory=_blocking_worker(started))
    app = create_app(config_path=config_path, runtime=runtime)
    assert request(app, "POST", "/api/session/start").status_code == 200
    assert started.wait(1.0)
    assert request(app, "POST", "/api/session/start").status_code == 409
    telemetry = request(app, "GET", "/api/telemetry").json()
    assert telemetry["gameScreen"] == "HOME"
    assert telemetry["screenConfidence"] == 0.99
    wait_for(lambda: runtime.status()["runtimeState"] == RuntimeState.RUNNING.value)
    assert request(app, "POST", "/api/session/pause").status_code == 200
    wait_for(lambda: runtime.status()["runtimeState"] == RuntimeState.PAUSED.value)
    assert request(app, "POST", "/api/session/resume").status_code == 200
    wait_for(lambda: runtime.status()["runtimeState"] == RuntimeState.RUNNING.value)
    assert request(app, "POST", "/api/session/stop").status_code == 200
    wait_for(lambda: runtime.status()["runtimeState"] == RuntimeState.STOPPED.value)
    assert runtime.telemetry()["terminalResult"] == "stopped"
    runtime.close()


def test_normal_completion_reports_completed_terminal_result(config_path) -> None:
    def complete_worker(control, _config) -> None:
        control.report(phase="COMPLETE", gameScreen="HOME")
        control.log(logging.WARNING, "fake worker completed")

    runtime = BotRuntime(config_path=config_path, worker_factory=complete_worker)
    assert runtime.start()[0]
    wait_for(lambda: runtime.status()["runtimeState"] == RuntimeState.STOPPED.value)
    status = runtime.status()
    assert status["terminalResult"] == "completed"
    assert status["terminalMessage"] == "Bot session completed."


def test_worker_failure_and_bounded_logs(config_path) -> None:
    def failing_worker(control, _config) -> None:
        for message in ("one", "two", "three"):
            control.log(logging.WARNING, message)
        raise RuntimeError("expected worker failure")

    runtime = BotRuntime(config_path=config_path, worker_factory=failing_worker, log_limit=2)
    assert runtime.start()[0]
    wait_for(lambda: runtime.status()["runtimeState"] == RuntimeState.ERROR.value)
    status = runtime.status()
    assert status["terminalResult"] == "failed"
    assert status["lastError"] == "expected worker failure"
    assert len(runtime.logs(100)) == 2
    app = create_app(config_path=config_path, runtime=runtime)
    entries = request(app, "GET", "/api/logs?limit=100").json()["entries"]
    assert len(entries) == 2
    assert all({"timestamp", "level", "message"} <= entry.keys() for entry in entries)
    runtime.close()


def test_telemetry_config_read_and_update(config_path) -> None:
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    raw_config["unexpectedRawField"] = "must not be exposed"
    config_path.write_text(json.dumps(raw_config), encoding="utf-8")
    runtime = BotRuntime(config_path=config_path)
    runtime.report({"gold": 123, "elixir": 456, "darkElixir": 789, "decision": "ATTACK", "nextTaps": 2})
    app = create_app(config_path=config_path, runtime=runtime)
    telemetry = request(app, "GET", "/api/telemetry").json()
    assert telemetry["gold"] == 123
    assert telemetry["nextTaps"] == 2
    config_response = request(app, "GET", "/api/config")
    assert config_response.status_code == 200
    assert config_response.json()["minimumGold"] == raw_config["minimumGold"]
    assert "unexpectedRawField" not in config_response.json()
    updated = request(app, "PUT", "/api/config", json={"minimumGold": 123456, "dryRun": False})
    assert updated.status_code == 200
    assert updated.json()["minimumGold"] == 123456
    assert updated.json()["dryRun"] is False
    assert request(app, "PUT", "/api/config", json={"maxBasesToCheck": 99}).status_code == 422
    runtime.close()


def test_completed_runtimes_detach_logging_handlers(config_path) -> None:
    baseline_handler_count = len(logging.getLogger().handlers)

    def complete_worker(_control, _config) -> None:
        return None

    for _ in range(2):
        runtime = BotRuntime(config_path=config_path, worker_factory=complete_worker)
        app = create_app(config_path=config_path, runtime=runtime)
        assert request(app, "GET", "/api/health").status_code == 200
        assert runtime.start()[0]
        wait_for(lambda: runtime.status()["runtimeState"] == RuntimeState.STOPPED.value)

    assert len(logging.getLogger().handlers) == baseline_handler_count


def test_invalid_config_is_rejected_on_read(config_path) -> None:
    config_path.write_text("{broken", encoding="utf-8")
    runtime = BotRuntime(config_path=config_path)
    app = create_app(config_path=config_path, runtime=runtime)
    response = request(app, "GET", "/api/config")
    assert response.status_code == 400
    assert "Invalid configuration" in response.json()["detail"]
    runtime.close()
