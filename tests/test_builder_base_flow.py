from builder_base_flow_controller import BUILDER_TEMPLATES
from screen_detector import ScreenState, detect_screen


def test_builder_enemy_banner_detects_prepared_builder_base_screenshot(builder_enemy_base_screenshot) -> None:
    result = detect_screen(
        builder_enemy_base_screenshot,
        threshold=0.85,
        templates=BUILDER_TEMPLATES,
    )

    assert result.state is ScreenState.BUILDER_ENEMY_BASE
    assert result.matched_template_name == "enemy_base_banner.png"
    assert result.confidence >= 0.99
