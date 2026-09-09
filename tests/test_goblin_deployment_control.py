import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import trial_flow_controller as module
from runtime.runtime_control import RuntimeControl, StopRequested


class ObservedStopEvent(threading.Event):
    """Signal when the real checkpoint enters its pause wait."""

    def __init__(self):
        super().__init__()
        self.pause_wait_entered = threading.Event()

    def wait(self, timeout=None):
        self.pause_wait_entered.set()
        return super().wait(timeout)


@pytest.mark.parametrize("setup", [1, 2])
@pytest.mark.parametrize("request_kind", ["stop", "pause"])
def test_super_deployment_observes_request_after_slot_selection(monkeypatch, setup, request_kind):
    actions = [SimpleNamespace(sequence_number=i, x=100 + i, y=200) for i in range(1, 36)]
    goblin_box, super_box = object(), object()
    plan = SimpleNamespace(actions=actions, valid=True, screenshot_width=1920, screenshot_height=1080)
    planner = Mock()
    planner.plan_attack.return_value = SimpleNamespace(
        attack_plan=plan, troop_slot_result=SimpleNamespace(bounding_box=goblin_box)
    )
    monkeypatch.setattr(module, "SneakyGoblinPlanner", lambda: planner)
    monkeypatch.setattr(module, "detect_template", Mock(return_value=SimpleNamespace(
        found=True, bounding_box=super_box, screenshot_size=(1920, 1080)
    )))
    reader = Mock()
    reader.read.return_value = SimpleNamespace(value=100, raw_text="100")
    monkeypatch.setattr(module, "TroopCountReader", lambda: reader)
    end = Mock()
    end.return_value.run.return_value = 0
    monkeypatch.setattr(module, "BattleEndController", end)

    stop, pause = ObservedStopEvent(), threading.Event()
    controller = object.__new__(module.TrialFlowController)
    controller.control = RuntimeControl(stop_event=stop, pause_event=pause)
    controller.bot_config = SimpleNamespace(
        delay_between_groups_seconds_options=(0,), new_base_timeout_seconds=20,
        screen_transition_poll_seconds_options=(0.2,),
    )
    controller.screen_threshold = 0.85
    controller.package_name = "test.package"
    controller.adb_controller = Mock()
    for name in ("_save_attack_plan_debug_image", "_deploy_action_round", "_wait_after_deployment"):
        setattr(controller, name, Mock())
    controller._wait_with_checkpoints = lambda _delay, phase: controller.control.checkpoint(phase)

    injected = False

    def select_slot(box, *_args):
        nonlocal injected
        # Simulate a request arriving at the end of slot selection/wait,
        # after its last checkpoint. Slot-selection taps are intentionally
        # excluded so the ADB spy records only Super Wall Breaker deployments.
        if box is super_box and not injected:
            injected = True
            (stop if request_kind == "stop" else pause).set()

    controller._tap_slot = select_slot
    run = getattr(controller, f"_deploy_setup_{setup}_then_end_battle")
    if request_kind == "stop":
        with pytest.raises(StopRequested):
            run()
        assert injected
        controller.adb_controller.tap.assert_not_called()
        end.assert_not_called()
        return

    errors = []
    finished = threading.Event()

    def worker():
        try:
            run()
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert stop.pause_wait_entered.wait(2), "Deployment did not reach the pause checkpoint"
        assert not finished.is_set()
        controller.adb_controller.tap.assert_not_called()
        pause.clear()
        assert finished.wait(2), "Deployment did not resume"
        assert not errors
        assert controller.adb_controller.tap.call_count == 4
    finally:
        stop.set()
        pause.clear()
        thread.join(2)
