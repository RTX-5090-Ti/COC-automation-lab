import json
from unittest.mock import Mock

import pytest

from api.app import create_app
from decision_engine import DEFAULT_CONFIG, DecisionEngineError, load_bot_config
from runtime.bot_runtime import BotRuntime
from tests.conftest import request


def test_dragon_count_defaults_persists_and_rejects_invalid_updates(tmp_path):
    path = tmp_path / "bot_config.json"
    legacy = {key: value for key, value in DEFAULT_CONFIG.items() if key != "dragonCount"}
    legacy["maximumPlannedActions"] = legacy["plannedDeploymentPoints"]
    path.write_text(json.dumps(legacy), encoding="utf-8")
    runtime = BotRuntime(config_path=path)
    try:
        app = create_app(config_path=path, runtime=runtime)
        response = request(app, "GET", "/api/config")
        assert response.status_code == 200, response.text
        assert response.json()["dragonCount"] == 10
        assert "dragonCount" not in json.loads(path.read_text(encoding="utf-8"))
        for count in (10, 13, 17):
            response = request(app, "PUT", "/api/config", json={"dragonCount": count})
            assert response.status_code == 200
            assert response.json()["dragonCount"] == count
            assert load_bot_config(path).dragon_count == count
            assert request(app, "GET", "/api/config").json()["dragonCount"] == count
        saved = path.read_bytes()
        for count in (9, 18, 10.5, True, "12"):
            assert request(app, "PUT", "/api/config", json={"dragonCount": count}).status_code == 422
            assert path.read_bytes() == saved
        for strategy in ("dragon", "sneaky_goblin"):
            response = request(app, "PUT", "/api/config", json={"strategy": strategy})
            assert response.status_code == 200
            assert request(app, "GET", "/api/config").json()["strategy"] == strategy
            assert load_bot_config(path).strategy == strategy
        assert request(app, "PUT", "/api/config", json={"strategy": "unknown"}).status_code == 422
    finally:
        runtime.close()


@pytest.mark.parametrize("count", [9, 18, 10.5, True, None])
def test_invalid_dragon_count_on_disk_is_rejected(tmp_path, count):
    path = tmp_path / "bot_config.json"
    path.write_text(json.dumps({**DEFAULT_CONFIG, "dragonCount": count}), encoding="utf-8")
    with pytest.raises(DecisionEngineError, match="dragonCount"):
        load_bot_config(path)


@pytest.mark.parametrize("battles", [1, 5, 10])
def test_dragon_session_does_not_fall_back_to_goblin(tmp_path, monkeypatch, battles):
    path = tmp_path / "bot_config.json"
    data = {**DEFAULT_CONFIG, "strategy": "dragon", "farmMode": "home_village", "maximumPlannedActions": 50, "battlesPerSession": battles}
    path.write_text(json.dumps(data), encoding="utf-8")
    runtime = BotRuntime(config_path=path)

    adb = Mock()
    adb.get_installed_packages.return_value = ["com.supercell.clashofclans"]
    monkeypatch.setattr("runtime.bot_runtime.ADBController", lambda: adb)
    flow = Mock()
    flow.return_value.selected_setup = "dragon_da"
    monkeypatch.setattr("runtime.bot_runtime.TrialFlowController", flow)
    control = Mock()
    try:
        runtime._run_default_worker(control, load_bot_config(path))
        assert flow.call_count == battles
        assert flow.return_value.run.call_count == battles
        for call in flow.call_args_list:
            assert call.kwargs["random_dragon_setup"] is True
            assert call.kwargs["random_setup_test"] is False
        assert control.report.call_args.kwargs["battlesCompleted"] == battles
    finally:
        runtime.close()
