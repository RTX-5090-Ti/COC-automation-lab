from builder_base_end_controller import END_BATTLE_BUTTON_PATH, END_BATTLE_CONFIRM_OK_PATH, RETURN_HOME_BUTTON_PATH
from screen_detector import detect_template


def test_builder_end_templates_are_decodable() -> None:
    for template in (END_BATTLE_BUTTON_PATH, END_BATTLE_CONFIRM_OK_PATH, RETURN_HOME_BUTTON_PATH):
        result = detect_template(template, template)
        assert result.found
        assert result.confidence >= 0.99
