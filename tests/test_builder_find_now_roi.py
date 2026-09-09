from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

import builder_base_flow_controller as module
from runtime.runtime_control import RuntimeControl
from screen_detector import ScreenState, detect_screen, detect_template


@pytest.fixture
def menu_image(tmp_path):
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    for state, x, y in ((ScreenState.BUILDER_HOME, 29, 859), (ScreenState.BUILDER_ATTACK_MENU, 1238, 642)):
        path = next(path for candidate, path, _ in module.BUILDER_TEMPLATES if candidate is state)
        template = cv2.imread(str(path))
        h, w = template.shape[:2]
        image[y:y+h, x:x+w] = template
    path = tmp_path / "menu.png"
    assert cv2.imwrite(str(path), image)
    return path


def test_menu_with_background_attack_button_returns_global_coordinates(menu_image):
    result = module.detect_find_now(menu_image, 0.85)
    assert result.state is ScreenState.BUILDER_ATTACK_MENU
    assert (result.bounding_box.x, result.bounding_box.y) == (1238, 642)
    assert result.screenshot_size == (1920, 1080)


def test_find_now_does_not_accept_home_button_alone(menu_image):
    image = cv2.imread(str(menu_image))
    image[:, 384:] = 0
    assert cv2.imwrite(str(menu_image), image)
    assert detect_screen(menu_image, templates=module.BUILDER_TEMPLATES).state is ScreenState.BUILDER_HOME
    assert module.detect_find_now(menu_image, 0.85).state is ScreenState.UNKNOWN


def test_wait_and_fresh_tap_use_roi_only_for_menu(menu_image, monkeypatch):
    monkeypatch.setattr(module, "CURRENT_SCREENSHOT_PATH", menu_image)
    general = Mock(side_effect=AssertionError("Menu step must not compete with the background Home button"))
    monkeypatch.setattr(module, "detect_screen", general)
    adb = Mock()
    adb.get_foreground_app.return_value = "test"
    controller = module.BuilderBaseFlowController(
        adb_controller=adb, bot_config=SimpleNamespace(screen_transition_poll_seconds_options=(0,)),
        package_name="test", screen_threshold=0.85, dry_run=False, control=RuntimeControl(),
    )
    assert controller._wait_for_state(ScreenState.BUILDER_ATTACK_MENU, 1).state is ScreenState.BUILDER_ATTACK_MENU
    controller._tap("Find Now button", ScreenState.BUILDER_ATTACK_MENU)
    x, y = adb.tap.call_args.args
    assert 1238 <= x < 1614 and 642 <= y < 778
    assert adb.capture_screenshot.call_count == 2
    general.side_effect = None
    controller._capture_and_detect()
    general.assert_called_once()


def test_roi_ignores_template_outside_search_area(tmp_path):
    template = np.random.default_rng(7).integers(0, 256, (20, 20, 3), dtype=np.uint8)
    image = np.zeros((200, 300, 3), dtype=np.uint8)
    image[50:70, 10:30] = template
    screen, target = tmp_path / "screen.png", tmp_path / "target.png"
    assert cv2.imwrite(str(screen), image)
    assert cv2.imwrite(str(target), template)
    assert detect_template(screen, target).found
    assert not detect_template(screen, target, search_roi=module.FIND_NOW_SEARCH_ROI).found
