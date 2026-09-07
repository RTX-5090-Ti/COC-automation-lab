from builder_base_deployment_test_controller import BuilderBaseDeploymentTestController
def test_builder_deployment_debug_image_marks_tap_coordinates(tmp_path, monkeypatch, builder_enemy_base_screenshot) -> None:
    monkeypatch.setattr(
        "builder_base_deployment_test_controller.DEBUG_DIRECTORY",
        tmp_path,
    )
    monkeypatch.setattr(
        "builder_base_deployment_test_controller.CURRENT_SCREENSHOT_PATH",
        builder_enemy_base_screenshot,
    )
    output = BuilderBaseDeploymentTestController._save_deployment_taps_debug(
        troop_index=2,
        troop_tap=(360, 935),
        point_sequence=12,
        deployment_tap=(1776, 479),
    )

    assert output == tmp_path / "builder_base_deployment_taps.png"
    assert output.is_file()
