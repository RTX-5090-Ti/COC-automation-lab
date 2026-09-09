from unittest.mock import Mock

import pytest

from api.app import create_app
from decision_engine import DecisionEngineError, load_bot_config
from runtime.bot_runtime import BotRuntime
from runtime.history_store import HistoryStore
from tests.conftest import request, wait_for
from tests.test_preflight import _service


def test_read_missing_config_does_not_create_parent(tmp_path):
    path = tmp_path / "absent" / "bot_config.json"
    with pytest.raises(DecisionEngineError, match="missing"):
        load_bot_config(path)
    assert not path.parent.exists()


@pytest.mark.parametrize("content", ["{broken", "[]", "null"])
def test_invalid_config_is_preserved(tmp_path, content):
    path = tmp_path / "bot_config.json"
    path.write_text(content)
    with pytest.raises(DecisionEngineError):
        load_bot_config(path)
    assert path.read_text() == content


def test_dashboard_read_then_preflight_keeps_missing_config_blocked(tmp_path):
    path = tmp_path / "bot_config.json"
    runtime = BotRuntime(config_path=path, history_store=HistoryStore(tmp_path))
    app = create_app(config_path=path, runtime=runtime, preflight_service=_service(path, tmp_path))
    try:
        for _ in range(2):
            response = request(app, "GET", "/api/config")
            assert response.status_code == 400
            assert "missing" in response.json()["detail"]
            assert not path.exists()
        report = request(app, "POST", "/api/preflight/run").json()
        assert report["overallStatus"] == "blocked"
        check = next(c for c in report["checks"] if c["id"] == "configuration")
        assert "missing" in check["detail"]
        assert request(app, "POST", "/api/session/start").status_code == 409
        assert not path.exists()
    finally:
        runtime.close()


def test_start_without_preflight_cannot_initialize_config(tmp_path):
    path = tmp_path / "bot_config.json"
    worker = Mock()
    runtime = BotRuntime(config_path=path, worker_factory=worker, history_store=HistoryStore(tmp_path))
    try:
        runtime.start()
        wait_for(lambda: runtime.telemetry()["terminalResult"] is not None)
        assert runtime.telemetry()["terminalResult"] == "failed"
        worker.assert_not_called()
        assert not path.exists()
    finally:
        runtime.close()
