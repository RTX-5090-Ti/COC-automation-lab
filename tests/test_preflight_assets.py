import json
from pathlib import Path

import pytest

from project_paths import asset_path, dashboard_dist_path
from tests.test_preflight import _service


HOME = [
    "home/attack_button.png", "attack_menu/find_match_button.png",
    "army_confirmation/army_panel_anchor.png", "army_confirmation/confirm_attack_button.png",
    "enemy_base/next_button.png", "battle/end_battle_button.png", "battle/surrender_button.png",
    "battle/end_battle_confirm_dialog.png", "battle/end_battle_confirm_ok.png",
    "battle/return_home_button.png", "battle/sneaky_goblin_slot.png",
    "battle/super_wall_breaker_slot.png", "battle/dragon_slot.png",
]
BUILDER = [f"builder_base/{name}.png" for name in (
    "builder_attack_button", "find_now_button", "enemy_base_banner",
    "builder_end_battle_button", "builder_end_battle_confirm_ok", "builder_return_home_button",
)]
MODES = {"home_village": HOME, "builder_base": BUILDER}


def service_for_mode(config_path, tmp_path, monkeypatch, mode, missing=None):
    config = json.loads(config_path.read_text())
    config["farmMode"] = mode
    config_path.write_text(json.dumps(config))
    # Only the selected mode's files exist; the other mode is entirely absent.
    present = {asset_path("templates", item) for item in MODES[mode]}
    if missing:
        present.remove(asset_path("templates", missing))
    original = Path.is_file

    def is_file(path):
        if path.is_relative_to(asset_path("templates")):
            return path in present
        if path == dashboard_dist_path() / "index.html":
            return True
        return original(path)

    monkeypatch.setattr(Path, "is_file", is_file)
    service = _service(config_path, tmp_path)
    service.asset_paths = None  # Exercise production requirements, not test overrides.
    return service


@pytest.mark.parametrize("mode,missing", [(mode, item) for mode, items in MODES.items() for item in items])
def test_each_required_template_blocks_preflight(config_path, tmp_path, monkeypatch, mode, missing):
    report = service_for_mode(config_path, tmp_path, monkeypatch, mode, missing).run()
    assert report["overallStatus"] == "blocked"
    assets = next(c for c in report["checks"] if c["id"] == "bundled_assets")
    assert assets["status"] == "fail"
    assert assets["metadata"]["missingPaths"] == [str(asset_path("templates", missing))]


@pytest.mark.parametrize("mode", MODES)
def test_other_modes_templates_are_not_required(config_path, tmp_path, monkeypatch, mode):
    report = service_for_mode(config_path, tmp_path, monkeypatch, mode).run()
    assert report["overallStatus"] == "ready"


def test_invalid_config_does_not_select_mode(config_path, tmp_path, monkeypatch):
    config_path.write_text("{broken")
    def unexpected(_mode):
        pytest.fail("Asset requirements must not be selected without valid config")
    monkeypatch.setattr("runtime.preflight_service.required_template_paths", unexpected)
    service = _service(config_path, tmp_path)
    service.asset_paths = None
    report = service.run()
    assert report["overallStatus"] == "blocked"
    assert next(c for c in report["checks"] if c["id"] == "bundled_assets")["status"] == "warning"
